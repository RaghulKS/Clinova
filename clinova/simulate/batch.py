"""T24 sampling, first-version feature view and grouped surrogate validation."""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

from clinova.cohort.generate import cohort_frame, generate_cohort
from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.contracts import BurdenTag, ParsedProtocol
from clinova.simulate.agents import PROMPT_VERSION
from clinova.simulate.surrogate import Surrogate, _matrix, burden_features

DEMO_TRIALS = ("NCT03232892", "NCT03417882", "NCT06333678")
LABELS = ("label_accrual_fail", "label_shortfall", "label_amend")


def select_sample(frame: pd.DataFrame, n: int = 400) -> pd.DataFrame:
    """Mandatory demos/test amendments, then balanced round-robin split/label strata."""
    if n < 0 or frame.nct_id.duplicated().any():
        raise ValueError("sample size must be nonnegative and trial ids unique")
    mandatory = frame.nct_id.isin(DEMO_TRIALS) | (frame.split.eq("test") & frame.label_amend.eq(True))
    chosen = frame.loc[mandatory].sort_values("nct_id").nct_id.tolist()
    strata = defaultdict(deque)
    remaining = frame.loc[~mandatory].copy()
    remaining["rank"] = remaining.nct_id.map(lambda value: hashlib.sha256(f"42/{value}".encode()).hexdigest())
    for row in remaining.sort_values("rank").itertuples():
        key = (str(row.split), *("missing" if pd.isna(getattr(row, label)) else str(bool(getattr(row, label))) for label in LABELS))
        strata[key].append(row.nct_id)
    while len(chosen) < n and any(strata.values()):
        for key in sorted(strata):
            if strata[key] and len(chosen) < n:
                chosen.append(strata[key].popleft())
    return frame.set_index("nct_id").loc[chosen].reset_index()


def sample_trials(path: Path, n: int = 400) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute("SELECT nct_id, split, label_accrual_fail, label_shortfall, label_amend FROM trials "
                            "WHERE first_version <> '{}'::jsonb AND coalesce(eligibility_text_first,'') <> ''").fetchall()
    frame = select_sample(pd.DataFrame(rows, columns=["nct_id", "split", *LABELS]), n)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def create_features_view() -> None:
    """Latest run per seed, current model, first registered version only; fixtures excluded."""
    reasons = ",\n".join(
        f"avg(coalesce((SELECT count(*) FILTER (WHERE '{tag.value}'=ANY(p.burden_tags))::float "
        f"/ nullif(count(*),0) FROM persona_outcomes p WHERE p.run_id=latest.run_id "
        f"AND p.eligible AND p.enrolled IS NOT NULL AND (NOT p.enrolled OR p.dropout_visit IS NOT NULL)),0)) "
        f"FILTER (WHERE n_agents>0) AS reason_{tag.value}" for tag in BurdenTag)
    sql = f"""CREATE OR REPLACE VIEW sim_features AS
        WITH latest AS (
          SELECT DISTINCT ON (trial_id, seed) * FROM sim_runs
          WHERE version_no=0 AND model_version=%s AND trial_id LIKE 'NCT%%'
          ORDER BY trial_id, seed, run_at DESC, run_id DESC
        )
        SELECT trial_id AS nct_id, avg(eligible_frac) AS eligible_frac,
          avg(enroll_rate) FILTER (WHERE n_agents>0) AS enroll_rate,
          avg((dropout_curve->>1)::float) FILTER (WHERE n_agents*enroll_rate>0) AS dropout_visit_2,
          avg((dropout_curve->>3)::float) FILTER (WHERE n_agents*enroll_rate>0) AS dropout_visit_4,
          avg((dropout_curve->>-1)::float) FILTER (WHERE n_agents*enroll_rate>0) AS dropout_last,
          avg(((SELECT sum(value::float) FROM jsonb_array_elements_text(dropout_curve))
            - (dropout_curve->>-1)::float/2.0) / nullif(jsonb_array_length(dropout_curve),0))
            FILTER (WHERE n_agents*enroll_rate>0) AS dropout_area,
          {reasons},
          stddev_pop(enroll_rate) FILTER (WHERE n_agents>0) AS seed_spread, count(*) AS n_seeds
        FROM latest GROUP BY trial_id"""
    # DDL cannot bind a view parameter; psycopg quotes the model identifier as a literal.
    from psycopg import sql as pgsql

    model = f"{get_settings().GEMINI_MODEL_FAST}/{PROMPT_VERSION}"
    with get_conn() as conn:
        conn.execute(pgsql.SQL(sql.replace("%s", "{}").replace("NCT%%", "NCT%")).format(pgsql.Literal(model)))


def include_new_amendments(path: Path) -> list[str]:
    """Append mandatory trials exposed by concurrent ingestion without replacing the sample."""
    sample = pd.read_csv(path)
    with get_conn() as conn:
        rows = conn.execute("SELECT nct_id, split, label_accrual_fail, label_shortfall, label_amend FROM trials "
                            "WHERE split='test' AND label_amend IS TRUE AND first_version <> '{}'::jsonb "
                            "AND coalesce(eligibility_text_first,'') <> '' ORDER BY nct_id").fetchall()
    additions = pd.DataFrame(rows, columns=sample.columns)
    additions = additions.loc[~additions.nct_id.isin(sample.nct_id)]
    if not additions.empty:
        pd.concat([sample, additions], ignore_index=True).to_csv(path, index=False)
    return additions.nct_id.tolist()


def refit_surrogate() -> dict:
    """Split by trial, report synthetic decision accuracy, then refit on every joined decision."""
    settings = get_settings()
    snapshots = settings.DATA_DIR / "simulation/protocols"
    protocols = {p.stem: ParsedProtocol.model_validate_json(p.read_text(encoding="utf-8"))
                 for p in snapshots.glob("*.json")}
    with get_conn() as conn:
        rows = conn.execute("SELECT s.trial_id, p.eligible, p.enrolled, p.dropout_visit, p.persona, s.model_version "
                            "FROM persona_outcomes p JOIN sim_runs s USING(run_id) "
                            "WHERE p.eligible AND p.enrolled IS NOT NULL AND s.version_no=0").fetchall()
    frame = pd.DataFrame(rows, columns=["trial_id", "eligible", "enrolled", "dropout_visit", "persona", "model_version"])
    missing = sorted(set(frame.trial_id) - protocols.keys())
    if missing:
        raise ValueError(f"Missing frozen simulation protocol snapshots for {len(missing)} trials")
    if frame.trial_id.nunique() < 2:
        raise ValueError("need at least two trials for held-out surrogate accuracy")
    frame["protocol"] = frame.trial_id.map(protocols)
    train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(frame, groups=frame.trial_id))
    model = Surrogate.fit(frame.iloc[train])
    held = frame.iloc[test]
    metrics = {"observed_decisions": len(frame), "trials": int(frame.trial_id.nunique()),
               "registered_trials": int(frame.loc[frame.trial_id.str.startswith('NCT'), 'trial_id'].nunique()),
               "fixture_trials": int(frame.loc[~frame.trial_id.str.startswith('NCT'), 'trial_id'].nunique()),
               "simulator_versions": sorted(frame.model_version.unique().tolist()),
               "training_trials": int(frame.iloc[train].trial_id.nunique()),
               "held_out_trials": int(held.trial_id.nunique()), "held_out_decisions": len(held)}
    metrics["held_out_trial_ids"] = sorted(held.trial_id.unique().tolist())
    features = pd.DataFrame([{**row.persona, **row.persona.get("clinical", {}), **burden_features(row.protocol)}
                             for row in held.itertuples()])
    probabilities = np.column_stack(model._probabilities(_matrix(features)))
    enrolled = held.enrolled.astype(bool).to_numpy()
    dropout = held.dropout_visit.notna().to_numpy()
    metrics["enrollment_accuracy"] = float(((probabilities[:, 0] >= 0.5) == enrolled).mean())
    metrics["enrollment_majority_baseline"] = float(max(enrolled.mean(), 1 - enrolled.mean()))
    metrics["dropout_accuracy_conditional"] = float(((probabilities[enrolled, 1] >= 0.5) == dropout[enrolled]).mean()) if enrolled.any() else None
    metrics["dropout_majority_baseline_conditional"] = float(max(dropout[enrolled].mean(), 1 - dropout[enrolled].mean())) if enrolled.any() else None
    final = Surrogate.fit(frame)
    final.save(settings.DATA_DIR / "models/surrogate.json")
    protocol = frame.iloc[0].protocol
    cohort = cohort_frame(generate_cohort(1000, 42, protocol.setting))
    timings = []
    for _ in range(100):
        start = time.perf_counter()
        final.score(protocol, cohort)
        timings.append((time.perf_counter() - start) * 1000)
    metrics.update(score_1000_median_ms=float(np.median(timings)), score_1000_p95_ms=float(np.percentile(timings, 95)),
                   score_1000_max_ms=max(timings), note="Grouped trial holdout includes synthetic fixture protocols; this is agreement with synthetic Gemini decisions, not clinical outcome accuracy or validation on a broad registered-trial cohort. Final fit uses all observed eligible first-version decisions with frozen protocol snapshots, including historical prompt versions.")
    output = settings.DATA_DIR / "T24-surrogate-metrics.json"
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def save_protocol(protocol: ParsedProtocol) -> None:
    path = get_settings().DATA_DIR / "simulation/protocols" / f"{protocol.trial_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != protocol.model_dump_json():
        raise ValueError(f"Frozen simulation protocol changed for {protocol.trial_id}")
    path.write_text(protocol.model_dump_json(), encoding="utf-8")
