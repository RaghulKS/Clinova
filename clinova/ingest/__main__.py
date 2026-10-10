"""CLI: ``python -m clinova.ingest run [--limit N] [--scope nsclc|solid_tumors]`` (owner: T11)."""

from __future__ import annotations

import argparse
import json

from clinova.ingest.pipeline import run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Pull, history-diff, label and load trials (resumable).")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--scope", choices=["nsclc", "solid_tumors"], default="nsclc")
    r.add_argument("--no-resume", action="store_true")
    a = p.parse_args(argv)
    print(json.dumps(run(limit=a.limit, scope=a.scope, resume=not a.no_resume)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
