"""Outcome checker (owner: T21). Tables ``outcomes`` and ``prediction_scores``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib
from psycopg.rows import dict_row

from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.contracts import ENDED_STATUSES
from clinova.ingest.ctgov import CTGovClient
from clinova.ingest.history import HistoryResult, fetch_history
from clinova.ingest.labels import label_trial
from clinova.ingest.pull import map_trial
from clinova.loop.data import CHECKER_CFG, available_training, fit, load_features, training_frame


def score_predictions(conn, nct_id, target, value, observed_at) -> int:
    """Only score predictions made before their outcome; retries never duplicate scores."""
    return conn.execute(
        "INSERT INTO prediction_scores "
        "(prediction_id,trial_id,model_version,context,target,p,y,month,scored_at) "
        "SELECT p.prediction_id,p.trial_id,p.model_version,p.context,p.target,p.p,%s,"
        "date_trunc('month',p.predicted_at)::date,%s FROM predictions p "
        "WHERE p.trial_id=%s AND p.target=%s AND p.predicted_at < %s "
        "AND p.context NOT IN ('fixture','replay') AND NOT EXISTS "
        "(SELECT 1 FROM prediction_scores s WHERE s.prediction_id=p.prediction_id) "
        "ON CONFLICT DO NOTHING", (float(value), observed_at, nct_id, target, observed_at),
    ).rowcount


def check_outcomes(*, limit: int | None = None, retrain_threshold: int = 25) -> dict[str, int]:
    """Re-fetch ``pending`` trials via ``clinova.ingest.ctgov``, write ``outcomes`` for trials that
    ended (observed_at = completion or termination date, labels per T11's rules), score matching
    predictions into ``prediction_scores``, and retrain when ``retrain_threshold`` or more new
    outcomes arrived. Returns counts, e.g. ``{"checked": 300, "ended": 12, "scored": 30, "retrained": 0}``."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    if retrain_threshold < 1:
        raise ValueError("retrain_threshold must be positive")
    counts = dict.fromkeys(("checked", "ended", "outcomes", "scored", "retrained", "failed", "undated"), 0)
    directory = get_settings().DATA_DIR / "loop"
    directory.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        pending = conn.cursor(row_factory=dict_row).execute(
            "SELECT * FROM trials t WHERE split='pending' AND NOT EXISTS "
            "(SELECT 1 FROM outcomes o WHERE o.nct_id=t.nct_id AND o.label='status' "
            "AND o.source LIKE 'ctgov:T21%%') ORDER BY nct_id LIMIT %s", (limit,),
        ).fetchall()
    # T11's successful-response cache has no TTL; isolate fresh reads without deleting its files.
    with TemporaryDirectory(prefix="check-", dir=directory) as temporary, CTGovClient(Path(temporary)) as client:
        for saved in pending:
            counts["checked"] += 1
            try:
                current = client.get("/api/v2/studies/" + saved["nct_id"])
                if current["protocolSection"]["statusModule"]["overallStatus"] not in ENDED_STATUSES:
                    continue
                trial = map_trial(current, saved["first_version"],
                                  first_version_date=saved["first_version_date"],
                                  history_source=saved.get("history_source", "earliest_vs_current"))
                if trial.completion_date is None or trial.completion_date > date.today():
                    counts["undated"] += 1
                    continue
                history = HistoryResult(first=saved["first_version"] or None)
                if trial.split != "pending":
                    history = fetch_history(client, saved["nct_id"], current, full=True)
                    if history.first is None and saved["first_version"]:
                        history.first = saved["first_version"]
                    trial = map_trial(current, saved["first_version"] or history.first,
                                      first_version_date=saved["first_version_date"] or history.first_version_date,
                                      history_source=history.source)
                trial, _ = label_trial(trial, history)
                observed = datetime.combine(trial.completion_date, datetime.min.time(), UTC)
                with get_conn() as conn:
                    for target in ("accrual_fail", "shortfall", "amend"):
                        value = getattr(trial, "label_" + target)
                        if value is None:
                            continue
                        counts["outcomes"] += conn.execute(
                            "INSERT INTO outcomes (nct_id,label,value,source,observed_at) "
                            "VALUES (%s,%s,%s,'ctgov:T21',%s) ON CONFLICT DO NOTHING",
                            (trial.nct_id, target, float(value), observed),
                        ).rowcount
                        counts["scored"] += score_predictions(conn, trial.nct_id, target, value, observed)
                    conn.execute("INSERT INTO outcomes (nct_id,label,value,source,observed_at) "
                                 "VALUES (%s,'status',NULL,%s,%s) ON CONFLICT DO NOTHING",
                                 (trial.nct_id, "ctgov:T21:" + trial.status, observed))
                counts["ended"] += 1
            except Exception as exc:
                counts["failed"] += 1
                logging.getLogger(__name__).error("outcome check failed for %s (%s)",
                                                  saved["nct_id"], type(exc).__name__)
    state_path = directory / "checker-state.json"
    trained = set(json.loads(state_path.read_text()).get("trained", [])) if state_path.exists() else set()
    with get_conn() as conn:
        arrived = {r[0] for r in conn.execute("SELECT DISTINCT nct_id FROM outcomes "
                                            "WHERE source='ctgov:T21' AND value IS NOT NULL")}
    counts["awaiting_retrain"] = len(arrived - trained)
    if counts["awaiting_retrain"] >= retrain_threshold:
        frame = available_training(training_frame(), date.today())
        # T22 defaults (tuned, bootstrapped), kept under DATA_DIR/loop: never overwrites models/ bundles.
        bundle = fit(frame, load_features(frame), CHECKER_CFG, "T22-checker-")
        if bundle["models"]:
            model_path = directory / "checker-model.joblib"
            model_temporary = model_path.with_suffix(".tmp")
            joblib.dump(bundle, model_temporary)
            model_temporary.replace(model_path)
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"trained": sorted(arrived), "version": bundle["version"]}))
            temporary.replace(state_path)
            counts["retrained"] = 1
            counts["awaiting_retrain"] = 0
    return counts
