"""Simulation CLI with a no-network spending plan and resumable batch execution."""
from __future__ import annotations

import argparse
import json
import math

from clinova.common import llm
from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.common.fixtures import load_fixture
from clinova.contracts import ParsedProtocol, Persona, TrialRecord
from clinova.parse.parser import parse_protocol
from clinova.parse.visits import estimate_visits
from clinova.simulate.agents import BATCH_SIZE, MAX_ATTEMPTS, PROMPT_VERSION, agent_prompt
from clinova.simulate.runner import simulate_trial


def registered_protocols(limit: int, trial_id: str | None = None) -> list[ParsedProtocol]:
    """Build from first-version fields using the shared cached parser and visit estimator."""
    with get_conn() as conn:
        cursor = conn.execute("SELECT * FROM trials WHERE (%s::text IS NULL OR nct_id=%s) ORDER BY nct_id LIMIT %s",
                              (trial_id, trial_id, limit))
        columns = [column.name for column in cursor.description]
        rows = cursor.fetchall()
    protocols = []
    for values in rows:
        data = dict(zip(columns, values, strict=True))
        data.pop("updated_at", None)
        trial = TrialRecord.model_validate(data)
        protocol = parse_protocol(trial.eligibility_text_first, trial_id=trial.nct_id, version_no=0, phase=trial.phase)
        protocol.title = trial.title
        protocol.summary = trial.first_version.get("descriptionModule", {}).get("briefSummary")
        protocol.randomized = True if trial.allocation == "RANDOMIZED" else False if trial.allocation == "NON_RANDOMIZED" else None
        protocol.arms = [arm.get("label", "Unspecified arm") for arm in trial.first_version.get("armsInterventionsModule", {}).get("armGroups", [])]
        if not protocol.visits:
            protocol.visits = estimate_visits(trial)
        protocols.append(protocol)
    return protocols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clinova.simulate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    batch = sub.add_parser("batch", help="Simulate first-version protocols; skip completed seeds.")
    batch.add_argument("--n-trials", type=int, required=True)
    batch.add_argument("--seeds", type=int, default=2)
    batch.add_argument("--max-calls", type=int, required=True)
    batch.add_argument("--dry-run", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("--trial", required=True)
    run.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    args = parser.parse_args(argv)
    if args.cmd == "batch":
        if args.n_trials < 0 or args.seeds < 1 or args.max_calls < 0:
            parser.error("n-trials/max-calls must be nonnegative; seeds must be positive")
        calls_per_trial = math.ceil(200 / BATCH_SIZE) * args.seeds + 1
        # Missing-persona retries and common.llm's schema repair each add calls.
        worst_per_trial = calls_per_trial * MAX_ATTEMPTS * 2
        personas = load_fixture("personas.json", list[Persona])[:BATCH_SIZE]
        protocol = load_fixture("parsed_protocol.json", ParsedProtocol)
        tokens_per_batch = math.ceil(len(agent_prompt(personas, protocol)) / 4)
        plan = {"requested_trials": args.n_trials, "nominal_calls": args.n_trials * calls_per_trial,
                "worst_case_calls_excluding_transport_retries": args.n_trials * worst_per_trial,
                "max_calls": args.max_calls, "estimated_input_tokens": tokens_per_batch * args.n_trials * calls_per_trial,
                "estimated_output_tokens": 200 * 120 * args.n_trials * args.seeds,
                "note": "Calls include one parsing request per trial; token estimates cover agent prompts only and use fixture lengths. Actual schedules and retries vary."}
        print(json.dumps(plan))
        if args.dry_run:
            return 0
        settings = get_settings()
        if settings.GEMINI_API_KEY is not None:
            used = llm._usage_count(llm._fingerprint(settings.GEMINI_API_KEY.get_secret_value()))
            settings.MAX_GEMINI_CALLS = min(settings.MAX_GEMINI_CALLS, used + args.max_calls)
        protocols = registered_protocols(args.n_trials)
        if args.n_trials and not protocols:
            raise RuntimeError("No ingested first-version trials; run ingestion first")
        seeds = list(range(1, args.seeds + 1))
    else:
        protocols = registered_protocols(1, args.trial)
        if not protocols:
            raise RuntimeError("Requested first-version trial has not been ingested")
        seeds = args.seeds
    for protocol in protocols:
        with get_conn() as conn:
            completed = {row[0] for row in conn.execute(
                "SELECT seed FROM sim_runs WHERE trial_id=%s AND version_no=%s AND model_version=%s",
                (protocol.trial_id, protocol.version_no, f"{get_settings().GEMINI_MODEL_FAST}/{PROMPT_VERSION}"))}
        pending = [seed for seed in seeds if seed not in completed]
        for summary in simulate_trial(protocol, seeds=pending):
            print(summary.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
