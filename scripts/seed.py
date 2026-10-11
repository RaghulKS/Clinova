"""Seed export and load for demo mode, Docker and Tiger Cloud (owner: T31).

    uv run python scripts/seed.py export   # DATABASE_URL -> db/seed/*.csv.gz + db/seed/manifest.json
    uv run python scripts/seed.py load     # db/seed -> DATABASE_URL (schema first, idempotent)
    uv run python scripts/seed.py load --force   # truncate the seeded tables first
    uv run python scripts/seed.py verify   # check file checksums against the manifest, no database

`load` applies db/schema.sql, copies every table that is still empty (a table with rows is left alone, so a
restart never duplicates data), recreates the sim_features view and refreshes the monthly aggregate.
Files outside the database (model bundle, surrogate, cohort cache) are plain files under db/seed/files/ and
are copied into place by the Dockerfile (`models/` and `.data/` are gitignored, hence the other names).
What is NOT seeded on purpose: llm_usage (a fresh Gemini budget for every install), feedback (empty), drafts.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import psycopg

from clinova.common.config import REPO_ROOT, get_settings
from clinova.common.db import apply_schema

SEED = REPO_ROOT / "db" / "seed"
DEMO_TRIALS = "('NCT03232892', 'NCT03417882', 'NCT06333678')"
# (table, WHERE clause or None). Load order = list order. feedback is created empty by the schema.
TABLES: list[tuple[str, str | None]] = [
    ("trials", None),
    ("trial_versions", None),
    ("criteria_rules", None),
    ("trial_embeddings", None),
    ("sim_runs", f"trial_id IN {DEMO_TRIALS}"),
    ("persona_outcomes", f"run_id IN (SELECT run_id FROM sim_runs WHERE trial_id IN {DEMO_TRIALS})"),
    ("predictions", "context IN ('replay', 'backtest', 'validation')"),
    ("prediction_scores", "context IN ('replay', 'backtest', 'validation')"),
    ("outcomes", None),
    ("fixes", f"trial_id IN {DEMO_TRIALS}"),
    ("llm_cache", None),
]


def _cols(conn: psycopg.Connection, table: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name = %s AND is_generated = 'NEVER' ORDER BY ordinal_position", (table,))]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files() -> list[Path]:
    return sorted(p for p in (SEED / "files").rglob("*") if p.is_file())


def export() -> None:
    SEED.mkdir(exist_ok=True)
    manifest: dict = {"tables": {}, "files": {}}
    with psycopg.connect(get_settings().DATABASE_URL.get_secret_value()) as conn:
        for table, where in TABLES:
            cols = ", ".join(_cols(conn, table))
            q = f"SELECT {cols} FROM {table}" + (f" WHERE {where}" if where else "")
            path = SEED / f"{table}.csv.gz"
            n = 0
            with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as gz, \
                    conn.cursor().copy(f"COPY ({q}) TO STDOUT (FORMAT csv, HEADER)") as cp:
                for chunk in cp:
                    gz.write(bytes(chunk))
            n = conn.execute(f"SELECT count(*) FROM ({q}) s").fetchone()[0]
            manifest["tables"][table] = {"rows": n, "sha256": _sha(path), "bytes": path.stat().st_size}
            print(f"{table}: {n} rows, {path.stat().st_size / 1e6:.2f} MB")
    for p in _files():
        manifest["files"][p.relative_to(SEED).as_posix()] = _sha(p)
    (SEED / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def verify() -> dict:
    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    for table, m in manifest["tables"].items():
        if _sha(SEED / f"{table}.csv.gz") != m["sha256"]:
            sys.exit(f"checksum mismatch: {table}.csv.gz (re-run `make seed` or re-export)")
    for rel, sha in manifest["files"].items():
        if _sha(SEED / rel) != sha:
            sys.exit(f"checksum mismatch: {rel}")
    print(f"seed ok: {len(manifest['tables'])} tables, {len(manifest['files'])} files")
    return manifest


def load(force: bool = False) -> None:
    manifest = verify()
    apply_schema()
    with psycopg.connect(get_settings().DATABASE_URL.get_secret_value(), autocommit=True) as conn:
        for table, _ in TABLES:
            have = conn.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]
            if have and not force:
                print(f"{table}: already has rows, skipped")
                continue
            if have:
                conn.execute(f"TRUNCATE {table}")
            with gzip.open(SEED / f"{table}.csv.gz", "rb") as gz:
                header = gz.readline().decode().strip()
                gz.seek(0)
                with conn.cursor().copy(f"COPY {table} ({header}) FROM STDIN (FORMAT csv, HEADER)") as cp:
                    while chunk := gz.read(1 << 20):
                        cp.write(chunk)
            print(f"{table}: loaded {manifest['tables'][table]['rows']} rows")
        conn.execute("CALL refresh_continuous_aggregate('prediction_scores_monthly', NULL, NULL)")
    from clinova.simulate.batch import create_features_view

    create_features_view()
    print("seed loaded")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        export()
    elif cmd == "load":
        load(force="--force" in sys.argv)
    elif cmd == "verify":
        verify()
    else:
        sys.exit(__doc__)
