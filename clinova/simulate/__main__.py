"""Simulation CLI with a no-network spending plan and resumable batch execution."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from clinova.common import llm
from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.common.fixtures import load_fixture
from clinova.contracts import ParsedProtocol, Persona, TrialRecord
from clinova.parse.parser import parse_protocol
from clinova.parse.visits import estimate_visits
from clinova.simulate.agents import BATCH_SIZE, MAX_ATTEMPTS, PROMPT_VERSION, agent_prompt
from clinova.simulate.batch import (
    DEMO_TRIALS,
    create_features_view,
    include_new_amendments,
    refit_surrogate,
    sample_trials,
    save_protocol,
)
from clinova.simulate.runner import simulate_trial


def registered_protocols(limit: int, trial_id: str | None = None) -> list[ParsedProtocol]:
    """Build from first-version fields using the shared cached parser and visit estimator."""
    with get_conn() as conn:
        cursor = conn.execute("SELECT * FROM trials WHERE (%s::text IS NULL OR nct_id=%s) AND first_version <> '{}'::jsonb "
                              "AND coalesce(eligibility_text_first, '') <> '' ORDER BY nct_id LIMIT %s",
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
    batch.add_argument("--sample", type=Path, help="Frozen CSV selection; create it if missing.")
    batch.add_argument("--max-agents", type=int, default=200)
    sub.add_parser("features", help="Create the first-version sim_features view.")
    sub.add_parser("refit", help="Validate by held-out trial and refit the surrogate.")
    run = sub.add_parser("run")
    run.add_argument("--trial", required=True)
    run.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    args = parser.parse_args(argv)
    if args.cmd == "features":
        create_features_view()
        return 0
    if args.cmd == "refit":
        print(json.dumps(refit_surrogate()), flush=True)
        return 0
    if args.cmd == "batch":
        if args.n_trials < 0 or args.seeds < 1 or args.max_calls < 0 or args.max_agents < 1:
            parser.error("n-trials/max-calls must be nonnegative; seeds must be positive")
        calls_per_trial = math.ceil(args.max_agents / BATCH_SIZE) * args.seeds + 1
        extra_seeds = 0
        if args.sample and args.sample.exists():
            sampled_ids = pd.read_csv(args.sample).nct_id
            extra_seeds = int(sampled_ids.isin(DEMO_TRIALS).sum()) * max(0, 5 - args.seeds)
        nominal_calls = args.n_trials * calls_per_trial + extra_seeds * math.ceil(args.max_agents / BATCH_SIZE)
        # Missing-persona retries and common.llm's schema repair each add calls.
        worst_calls = nominal_calls * MAX_ATTEMPTS * 2
        personas = load_fixture("personas.json", list[Persona])[:BATCH_SIZE]
        protocol = load_fixture("parsed_protocol.json", ParsedProtocol)
        tokens_per_batch = math.ceil(len(agent_prompt(personas, protocol)) / 4)
        plan = {"requested_trials": args.n_trials, "nominal_calls": nominal_calls,
                "worst_case_calls_excluding_transport_retries": worst_calls,
                "max_calls": args.max_calls, "estimated_input_tokens": tokens_per_batch * nominal_calls,
                "estimated_output_tokens": args.max_agents * 120 * (args.n_trials * args.seeds + extra_seeds),
                "note": "Calls include one parsing request per trial; token estimates cover agent prompts only and use fixture lengths. Actual schedules and retries vary."}
        print(json.dumps(plan), flush=True)
        if args.dry_run:
            return 0
        settings = get_settings()
        if settings.GEMINI_API_KEY is not None:
            used = llm._usage_count(llm._fingerprint(settings.GEMINI_API_KEY.get_secret_value()))
            settings.MAX_GEMINI_CALLS = min(used + args.max_calls,
                                           used + math.floor((settings.MAX_GEMINI_CALLS - used) * 0.85) - settings.GEMINI_CONCURRENCY)
            print(json.dumps({"used_calls": used, "shared_stop_at": settings.MAX_GEMINI_CALLS}), flush=True)
        if args.sample:
            sample = pd.read_csv(args.sample) if args.sample.exists() else sample_trials(args.sample, args.n_trials)
            trial_ids = sample.nct_id.tolist()
        else:
            with get_conn() as conn:
                trial_ids = [r[0] for r in conn.execute("SELECT nct_id FROM trials WHERE first_version <> '{}'::jsonb "
                                                       "AND coalesce(eligibility_text_first,'') <> '' ORDER BY nct_id LIMIT %s", (args.n_trials,))]
        if args.n_trials and not trial_ids:
            raise RuntimeError("No ingested first-version trials; run ingestion first")
        seeds = list(range(1, args.seeds + 1))
        create_features_view()
        failed = []
        for attempt in range(2):
            pending_trials, failed = trial_ids, []
            if args.sample:
                pending_trials.extend(include_new_amendments(args.sample))
            for index, trial_id in enumerate(pending_trials):
                try:
                    if args.sample and index and index % 25 == 0:
                        pending_trials.extend(include_new_amendments(args.sample))
                    desired = list(range(1, 6)) if trial_id in DEMO_TRIALS else seeds
                    with get_conn() as conn:
                        completed = {r[0] for r in conn.execute("SELECT seed FROM sim_runs WHERE trial_id=%s AND version_no=0 AND model_version=%s",
                                                               (trial_id, f"{settings.GEMINI_MODEL_FAST}/{PROMPT_VERSION}"))}
                    pending = [seed for seed in desired if seed not in completed]
                    if not pending:
                        continue
                    protocol = registered_protocols(1, trial_id)[0]
                    save_protocol(protocol)
                    for seed in pending:
                        for summary in simulate_trial(protocol, seeds=[seed], max_agents=args.max_agents):
                            print(summary.model_dump_json(), flush=True)
                except llm.BudgetExceeded:
                    print(json.dumps({"budget_exhausted": True, "trial_id": trial_id}), flush=True)
                    return 2
                except Exception as error:
                    # Provider messages can contain request details; log only the class and trial.
                    print(json.dumps({"failed_trial": trial_id, "attempt": attempt + 1, "error": type(error).__name__,
                                      "status_code": getattr(error, "code", None)}), flush=True)
                    if getattr(error, "code", None) == 429:
                        print(json.dumps({"quota_paused": True, "resume_trial": trial_id}), flush=True)
                        return 2
                    failed.append(trial_id)
            trial_ids = failed
        print(json.dumps({"failed_trials": failed}), flush=True)
        return int(bool(failed))
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
