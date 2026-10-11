"""Polite sequential CT.gov requests with resumable public-response caching."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from clinova.common.config import get_settings


class CTGovClient:
    def __init__(self, data_dir: Path | None = None, *, interval: float = 1.0):
        self.cache_dir = (data_dir or get_settings().DATA_DIR) / "raw" / "ctgov"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval
        self._last_request = 0.0
        self.client = httpx.Client(
            base_url="https://clinicaltrials.gov", timeout=45,
            headers={"User-Agent": "Clinova/0.1 (public clinical-trial research; cached sequential requests)"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = str(self.client.build_request("GET", path, params=params).url)
        key = hashlib.sha256(url.encode()).hexdigest()
        dest = self.cache_dir / f"{key}.json"
        if dest.exists():
            return json.loads(dest.read_text(encoding="utf-8"))["body"]
        for attempt in range(4):
            time.sleep(max(0, self.interval - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            try:
                response = self.client.get(path, params=params)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    retry = response.headers.get("Retry-After", "")
                    delay = float(retry) if retry.isdigit() else 2 ** attempt
                    time.sleep(min(120, delay))
                    continue
            response.raise_for_status()  # Never retry a forbidden internal history endpoint.
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("CT.gov response must be a JSON object")
            payload = {"url": url, "retrieved_at": time.time(), "status": response.status_code, "body": body}
            temporary = dest.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(dest)
            return body
        raise RuntimeError("unreachable retry state")
