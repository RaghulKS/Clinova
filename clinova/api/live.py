"""Thin calls into the real modules and the database, kept in one place for the routes.

Real modules that are still stubs raise ``NotImplementedError``; the routes catch that and fall back
to fixtures. Database trouble raises ``LiveUnavailable`` and is handled the same way. Set
``CLINOVA_API_NO_DB=1`` to skip the database entirely (the tests do).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from clinova.contracts import Feedback, Prediction, TrialRecord

log = logging.getLogger("clinova.api")

_DB_CHECK: tuple[float, bool] | None = None


class LiveUnavailable(Exception):
    """The live path cannot run right now (no database, trial not loaded). Routes fall back."""


def db_available(max_age_s: float = 30.0) -> bool:
    """True when the database answers within 3 seconds. The answer is remembered for 30 seconds."""
    global _DB_CHECK
    if os.environ.get("CLINOVA_API_NO_DB"):
        return False
    now = time.monotonic()
    if _DB_CHECK and now - _DB_CHECK[0] < max_age_s:
        return _DB_CHECK[1]
    ok = False
    try:
        import psycopg

        from clinova.common.config import get_settings

        url = get_settings().DATABASE_URL
        if url is not None:
            with psycopg.connect(url.get_secret_value(), connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            ok = True
    except Exception as exc:  # network, auth, missing driver: all mean "not available"
        log.info("database not reachable: %s", type(exc).__name__)
    _DB_CHECK = (now, ok)
    return ok


def _need_db() -> None:
    if not db_available():
        raise LiveUnavailable("database not reachable")


# --- trials ---------------------------------------------------------------------------------------


def fetch_trial(nct_id: str) -> TrialRecord:
    """Read one trial from ``trials``. ``LookupError`` if absent, ``LiveUnavailable`` without a database."""
    _need_db()
    from psycopg.rows import dict_row

    from clinova.common.db import get_conn

    try:
        with get_conn() as conn:
            row = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM trials WHERE nct_id = %s", (nct_id.upper(),)
            ).fetchone()
    except Exception as exc:
        raise LiveUnavailable(f"trial lookup failed: {type(exc).__name__}") from exc
    if row is None:
        raise LookupError(nct_id)
    row.pop("updated_at", None)
    return TrialRecord.model_validate(row)


def protocol_from_trial(trial: TrialRecord) -> Any:
    """Parse a trial's FIRST registered eligibility text (leakage rule) into a ParsedProtocol."""
    from clinova.parse.parser import parse_protocol
    from clinova.parse.visits import estimate_visits

    protocol = parse_protocol(
        trial.eligibility_text_first or "", None, trial_id=trial.nct_id, version_no=0, phase=trial.phase
    )
    if not protocol.visits:
        protocol = protocol.model_copy(update={"visits": estimate_visits(trial)})
    return protocol


# --- engine stages ---------------------------------------------------------------------------------


def parse_text(eligibility_text, schedule_text, *, trial_id, phase):
    from clinova.parse.parser import parse_protocol

    return parse_protocol(eligibility_text, schedule_text, trial_id=trial_id, version_no=0, phase=phase)


def make_cohort(n, seed, setting):
    from clinova.cohort.generate import generate_cohort_df

    return generate_cohort_df(n, seed, setting)


def screen(rules, cohort_df, trial_id):
    from clinova.simulate.screen import screen as _screen

    return _screen(rules, cohort_df, trial_id=trial_id)


def simulate(protocol, n, seeds):
    from clinova.simulate.runner import simulate_trial

    return simulate_trial(protocol, n=n, seeds=tuple(seeds))


def score(protocol, sims):
    from clinova.model.predict import score as _score

    return _score(protocol, sim=sims)


def flag_criteria(protocol):
    from clinova.model.predict import flag_criteria as _flags

    return _flags(protocol, k=3)


def optimize(protocol, max_edits=3):
    from clinova.optimize.search import optimize as _optimize

    return _optimize(protocol, max_edits=max_edits)


def apply_edits(rules, edits):
    from clinova.simulate.screen import apply_edits as _apply

    return _apply(rules, edits)


# --- ledger ----------------------------------------------------------------------------------------


def record_prediction(trial_id, risk, context="live"):
    from clinova.loop.ledger import record_prediction as _record

    return _record(trial_id, risk, context=context)


def record_feedback(feedback: Feedback) -> Feedback:
    from clinova.loop.ledger import record_feedback as _record

    return _record(feedback)


def backtest(nct_id: str):
    """Owner T25: loads the trial, runs the first version, adds version events and rule changes."""
    raise NotImplementedError("T25 implements the live backtest")


def read_feedback(limit: int = 200) -> list[Feedback]:
    _need_db()
    from psycopg.rows import dict_row

    from clinova.common.db import get_conn

    try:
        with get_conn() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM feedback ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()
    except Exception as exc:
        raise LiveUnavailable(f"feedback read failed: {type(exc).__name__}") from exc
    return [Feedback.model_validate(r) for r in rows]


def read_predictions(limit: int = 200) -> list[Prediction]:
    _need_db()
    from psycopg.rows import dict_row

    from clinova.common.db import get_conn

    try:
        with get_conn() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM predictions ORDER BY predicted_at DESC LIMIT %s", (limit,)
            ).fetchall()
    except Exception as exc:
        raise LiveUnavailable(f"predictions read failed: {type(exc).__name__}") from exc
    return [Prediction.model_validate(r) for r in rows]
