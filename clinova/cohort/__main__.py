"""CLI: ``python -m clinova.cohort generate --n 1000 --seed 42 --setting any --out cohort.parquet`` (owner: T13)."""

from __future__ import annotations

import argparse

from clinova.cohort.generate import generate_cohort_df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.cohort")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="Generate a virtual cohort and write it as parquet.")
    g.add_argument("--n", type=int, default=1000)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--setting", choices=["first_line", "second_line_plus", "any"], default="any")
    g.add_argument("--out", required=True)
    a = p.parse_args(argv)
    generate_cohort_df(a.n, a.seed, a.setting).to_parquet(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
