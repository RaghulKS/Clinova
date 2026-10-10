"""CLI: ``python -m clinova.optimize [--trial NCT...]`` (owner: T23; default trial = DEMO_NCT_ID)."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.optimize")
    p.add_argument("--trial", default=None, help="NCT id (default: the selected demo trial).")
    p.add_argument("--seed", type=int, default=42)
    p.parse_args(argv)
    raise NotImplementedError("T23 implements the optimize CLI")


if __name__ == "__main__":
    raise SystemExit(main())
