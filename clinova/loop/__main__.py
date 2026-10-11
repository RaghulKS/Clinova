"""CLI (owner: T21): ``python -m clinova.loop check [--limit N]`` and
``python -m clinova.loop replay [--end YYYY-MM] [--out DIR] [--no-persist]``."""

from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.loop")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="Record outcomes of pending trials and score past predictions.")
    c.add_argument("--limit", type=int, default=None)
    r = sub.add_parser("replay", help="Run the 2020-2023 time-machine replay.")
    r.add_argument("--end", default="2023-12", help="Last simulated month, YYYY-MM (shorter = smoke run).")
    r.add_argument("--out", default=None, help="Directory for replay.json and figures/ (default docs/).")
    r.add_argument("--no-persist", action="store_true", help="Skip ledger writes and the aggregate refresh.")
    a = p.parse_args(argv)
    if a.cmd == "check":
        from clinova.loop.checker import check_outcomes

        print(json.dumps(check_outcomes(limit=a.limit)))
    else:
        from clinova.loop.replay import run_replay

        print(run_replay(end=a.end, out=a.out, persist=not a.no_persist).model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
