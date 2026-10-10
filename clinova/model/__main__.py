"""CLI (owner: T22): ``python -m clinova.model train [--version v0.x]`` and ``python -m clinova.model evaluate``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.model")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="Build features from the database and train a versioned bundle.")
    t.add_argument("--version", default=None)
    sub.add_parser("evaluate", help="Write docs/metrics.json and docs/figures/*.png on the test split.")
    p.parse_args(argv)
    raise NotImplementedError("T22 implements the model CLI")


if __name__ == "__main__":
    raise SystemExit(main())
