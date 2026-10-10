"""CLI (owner: T12):
``python -m clinova.parse run --versions first|amended [--limit N]`` and ``python -m clinova.parse embed``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.parse")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Batch-parse trials into criteria_rules (resumable, test split first).")
    r.add_argument("--versions", choices=["first", "amended"], default="first")
    r.add_argument("--limit", type=int, default=None)
    sub.add_parser("embed", help="Embed first-version eligibility text into trial_embeddings (resumable).")
    a = p.parse_args(argv)
    if a.cmd == "embed":
        from clinova.parse.embed import embed_trials

        print(embed_trials())
        return 0
    raise NotImplementedError(f"T12 implements the batch parser (--versions {a.versions})")


if __name__ == "__main__":
    raise SystemExit(main())
