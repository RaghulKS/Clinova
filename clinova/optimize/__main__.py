"""CLI optimizer and repeatable speed benchmark; original registered rules only."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from clinova.common.config import get_settings
from clinova.common.fixtures import load_fixture
from clinova.contracts import ParsedProtocol
from clinova.optimize.scoring import StubScorer
from clinova.optimize.search import optimize_with_report
from clinova.optimize.store import load_protocol, save_fixes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clinova.optimize")
    parser.add_argument("--trial", default=None, help="NCT id (default: the selected demo trial).")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-trials", type=int, default=300)
    parser.add_argument("--cohort-size", type=int, default=1000)
    parser.add_argument("--no-persist", action="store_true", help="Do not save fixes to the database.")
    parser.add_argument("--fixture", action="store_true", help="Offline synthetic example; explicitly heuristic.")
    parser.add_argument("--benchmark", action="store_true", help="Return failure when optimization exceeds 10 seconds.")
    parser.add_argument("--output", type=Path, help="Also save the JSON report to this path.")
    args = parser.parse_args(argv)
    if args.fixture and args.trial:
        parser.error("--fixture and --trial are mutually exclusive")
    started = time.perf_counter()
    trial = args.trial or get_settings().DEMO_NCT_ID
    if not trial and not args.fixture:
        parser.error("No selected demo trial; provide --trial")
    protocol = load_fixture("parsed_protocol.json", ParsedProtocol) if args.fixture else load_protocol(trial)
    kwargs = {"scorer": StubScorer(), "observed_rows": [], "precedent_fn": lambda *_: []} if args.fixture else {}
    result = optimize_with_report(protocol, seed=args.seed, n_trials=args.n_trials,
                                  cohort_size=args.cohort_size, **kwargs)
    persisted = 0 if args.no_persist or args.fixture else save_fixes(result.fixes)
    report = {"trial_id": protocol.trial_id, "protocol": protocol.model_dump(mode="json"),
              "protocol_snapshot_sha256": hashlib.sha256(protocol.model_dump_json().encode()).hexdigest(),
              "fixes": [f.model_dump(mode="json") for f in result.fixes],
              "elapsed_s": result.elapsed_s, "run_elapsed_s": time.perf_counter() - started,
              "n_trials": args.n_trials, "cohort_size": args.cohort_size,
              "evaluated_n": result.evaluated_n, "pareto_n": len(result.pareto_front),
              "scorer_source": result.scorer_source, "cohort_seeds": result.cohort_seeds,
              "bootstrap_models_n": result.bootstrap_models_n, "persisted_n": persisted,
              "notes": result.notes}
    if args.fixture:
        report["notes"].append("Synthetic fixture; no observed threshold ladders or actual precedents loaded.")
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 1 if args.benchmark and (max(result.elapsed_s, report["run_elapsed_s"]) >= 10
                                   or len(result.fixes) != 3) else 0


if __name__ == "__main__":
    raise SystemExit(main())
