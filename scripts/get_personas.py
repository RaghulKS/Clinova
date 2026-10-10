"""Download a reproducible, licensed 20k NVIDIA persona sample outside Git.

Usage: python -m scripts.get_personas --n 20000 --seed 42
The datasets-server rows endpoint avoids downloading multi-GB parquet shards or
adding the datasets dependency. Random non-overlapping 100-row blocks cover the
whole train split; this is block sampling, not an iid individual sample. Each
block is cached for resumption. HTTP failures leave the current complete sample
untouched. The cohort loader performs NSCLC importance resampling afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

# Also support the task's named direct-script invocation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clinova.cohort.personas import DATASET, SAMPLE_NAME  # noqa: E402
from clinova.common.config import get_settings  # noqa: E402

HF_API = f"https://huggingface.co/api/datasets/{DATASET}"
VIEWER_API = "https://datasets-server.huggingface.co"
LOGGER = logging.getLogger(__name__)


def _json_request(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    for attempt in range(8):
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            if attempt == 7:
                raise
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
                retry_after = error.response.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 20 * (attempt + 1)
                LOGGER.warning("Hugging Face rate limit; retrying in %.0f seconds", min(delay, 60))
                time.sleep(min(delay, 60))
            else:
                time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def download_personas(n: int = 20_000, seed: int = 42, data_dir: Path | None = None,
                      workers: int = 1, client: httpx.Client | None = None) -> Path:
    """Fetch licensed synthetic records and publish parquet + attribution metadata.

    If the source license is unavailable or changes, fail closed. The generator
    can still use its deterministic synthetic fallback. ``client`` is injectable
    so tests never contact the network.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ValueError("n must be a positive integer")
    if workers < 1:
        raise ValueError("workers must be positive")
    if client is None:
        with httpx.Client(timeout=60, follow_redirects=True) as owned_client:
            return download_personas(n, seed, data_dir, workers, owned_client)
    card = _json_request(client, HF_API)
    license_name = (card.get("cardData") or {}).get("license")
    if license_name != "cc-by-4.0":
        raise ValueError(f"Refusing persona data with unverified license: {license_name!r}")
    revision = card.get("sha")
    if not revision:
        raise ValueError("Dataset API did not provide a revision for provenance")
    size = _json_request(client, f"{VIEWER_API}/size", {"dataset": DATASET})
    splits = [split for split in size["size"]["splits"] if split["split"] == "train"]
    if not splits:
        raise ValueError("Dataset has no train split")
    split = splits[0]
    config, total = split["config"], int(split["num_rows"])
    if n > total:
        raise ValueError(f"Requested {n} records but source has {total}")
    output = Path(data_dir or get_settings().DATA_DIR) / "personas"
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "download" / f"{revision}-{seed}-{n}"
    cache.mkdir(parents=True, exist_ok=True)
    block_count = math.ceil(total / 100)
    offsets = np.random.default_rng(seed).permutation(block_count) * 100

    def fetch(offset: int) -> list[dict]:
        path = cache / f"{offset:09d}.json"
        if path.exists():
            page = json.loads(path.read_text())
        else:
            # Pace download threads below the viewer API's burst limit.
            time.sleep(1)
            page = _json_request(client, f"{VIEWER_API}/rows", {
                "dataset": DATASET, "config": config, "split": "train",
                "offset": offset, "length": min(100, total - offset),
            })
            _write_json(path, page)
        return [entry["row"] for entry in page["rows"]]

    rows: list[dict] = []
    # One extra page may be needed when the sampled final block is incomplete.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        start = 0
        while len(rows) < n:
            need = math.ceil((n - len(rows)) / 100)
            selected = offsets[start:start + need]
            for page in pool.map(fetch, (int(offset) for offset in selected)):
                rows.extend(page)
            start += len(selected)
            if not len(selected):
                raise ValueError("Dataset rows endpoint returned fewer records than advertised")
    frame = pd.DataFrame(rows[:n])
    required = {"uuid", "age", "sex", "occupation"}
    if not required.issubset(frame.columns) or frame["uuid"].isna().any():
        raise ValueError("Downloaded sample lacks expected persona fields")
    if frame["uuid"].duplicated().any():
        raise ValueError("Downloaded sample contains duplicate source UUIDs")
    # Viewer queries are served at the current revision; avoid mixed-revision results.
    final_card = _json_request(client, HF_API)
    if final_card.get("sha") != revision or (final_card.get("cardData") or {}).get("license") != license_name:
        raise ValueError("Dataset revision/license changed during download; rerun to build one snapshot")
    destination = output / SAMPLE_NAME
    temporary = destination.with_suffix(".parquet.tmp")
    frame.to_parquet(temporary, index=False)
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    metadata = {
        "dataset": DATASET, "creator": "NVIDIA", "license": license_name,
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_url": f"https://huggingface.co/datasets/{DATASET}",
        "retrieval_revision": revision, "retrieved_at": datetime.now(UTC).isoformat(),
        "rows": len(frame), "source_rows": total, "seed": seed, "sha256": digest,
        "sampling": "Uniform random non-overlapping 100-row blocks, shuffled deterministically; truncated to n",
        "changes": "Source rows sampled; no source field values modified. Clinova later reweights age/sex and imputes life context.",
    }
    temporary.replace(destination)
    _write_json(destination.with_suffix(".metadata.json"), metadata)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    path = download_personas(args.n, args.seed, args.data_dir, args.workers)
    print(json.dumps({"path": str(path), "rows": args.n, "seed": args.seed, "license": "cc-by-4.0"}))


if __name__ == "__main__":
    main()
