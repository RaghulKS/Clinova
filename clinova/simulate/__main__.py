"""CLI (owner: T14, then T24):
``python -m clinova.simulate batch --n-trials N --seeds 2 --max-calls M [--dry-run]`` and
``python -m clinova.simulate run --trial NCT... [--seeds 1 2]``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.simulate")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="Simulate many trials (resumable background job).")
    b.add_argument("--n-trials", type=int, required=True)
    b.add_argument("--seeds", type=int, default=2)
    b.add_argument("--max-calls", type=int, required=True)
    b.add_argument("--dry-run", action="store_true", help="Print planned calls and tokens, spend nothing.")
    r = sub.add_parser("run", help="Simulate one trial.")
    r.add_argument("--trial", required=True)
    r.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    p.parse_args(argv)
    raise NotImplementedError("T14 implements the simulate CLI")


if __name__ == "__main__":
    raise SystemExit(main())
