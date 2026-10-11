"""Thin calls into the real modules and the database, kept in one place for the routes.

Real modules that are still stubs raise ``NotImplementedError``; the routes catch that and fall back
to fixtures. Database trouble raises ``LiveUnavailable`` and is handled the same way. Set
``CLINOVA_API_NO_DB=1`` to skip the database entirely (the tests do).
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any

from clinova.common.llm import BudgetExceeded, CacheMiss
from clinova.contracts import Feedback, ParsedProtocol, Prediction, TrialRecord, VersionEvent

log = logging.getLogger("clinova.api")

_DB_CHECK: tuple[float, bool] | None = None


class LiveUnavailable(Exception):
    """The live path cannot run right now (no database, trial not loaded). Routes fall back."""


FALLBACK_ERRORS: tuple[type[BaseException], ...] = (NotImplementedError, LiveUnavailable, CacheMiss, BudgetExceeded)
# What routes and run stages treat as "this part is not available": a stub, no database, an LLM response that
# demo mode has not cached, or a spent Gemini budget. They fall back to fixtures, always tagged as such.
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
    """Parse a trial's FIRST registered version (leakage rule) into a ParsedProtocol, with the same
    title/arms/visits the batch simulator uses (G1: reuse ``registered_protocols``)."""
    _need_db()  # Gemini calls go through llm_cache in Postgres
    from clinova.simulate.__main__ import registered_protocols

    if not trial.first_version:
        raise LookupError(f"{trial.nct_id} has no first registered version")
    return registered_protocols(1, trial.nct_id)[0]


# --- engine stages ---------------------------------------------------------------------------------


def parse_text(eligibility_text, schedule_text, *, trial_id, phase):
    _need_db()  # Gemini calls go through llm_cache in Postgres
    from clinova.parse.parser import parse_protocol

    return parse_protocol(eligibility_text, schedule_text, trial_id=trial_id, version_no=0, phase=phase)


@functools.lru_cache(maxsize=6)
def _cohort_cached(n, seed, setting):
    from clinova.cohort.generate import generate_cohort_df

    return generate_cohort_df(n, seed, setting)


def make_cohort(n, seed, setting):
    """The cohort dataframe for (n, seed, setting). Deterministic, so it is cached; callers must not mutate it."""
    return _cohort_cached(n, seed, setting)


def screen(rules, cohort_df, trial_id):
    from clinova.simulate.screen import screen as _screen

    return _screen(rules, cohort_df, trial_id=trial_id)


def simulate(protocol, n, seeds):
    _need_db()  # Gemini calls go through llm_cache in Postgres
    from clinova.simulate.runner import simulate_trial

    # G1: never persist API (possibly edited / draft) runs as first-version sim_runs.
    return simulate_trial(protocol, n=n, seeds=tuple(seeds), persist=False)


def score(protocol, sims):
    from clinova.model.predict import score as _score

    return _score(protocol, sim=sims)


def flag_criteria(protocol):
    from clinova.model.predict import flag_criteria as _flags

    return _flags(protocol, k=3)


def optimize(protocol, max_edits=3):
    _need_db()  # edit space and precedents query the database (T23 contract change)
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


@functools.lru_cache(maxsize=1)
def _surrogate():
    from clinova.simulate.surrogate import Surrogate

    try:
        return Surrogate.load()
    except FileNotFoundError as exc:  # T24 has not fitted and saved one yet
        raise LiveUnavailable("no fitted surrogate on disk") from exc


def surrogate_score(protocol, eligible_df) -> tuple[float, float]:
    """``(enroll_rate, dropout_risk)`` from the fast surrogate: no Gemini calls."""
    return _surrogate().score(protocol, eligible_df)


def diversity(eligible_df) -> float:
    from clinova.simulate.diversity import diversity_index

    return diversity_index(eligible_df)


def diff_rules(old, new):
    from clinova.parse.diff import diff_rules as _diff

    return _diff(old, new)


def read_version_events(nct_id: str) -> list[VersionEvent]:
    """Field-level history of one trial from ``trial_versions``, oldest first."""
    _need_db()
    from psycopg.rows import dict_row

    from clinova.common.db import get_conn

    try:
        with get_conn() as conn:
            rows = conn.cursor(row_factory=dict_row).execute(
                "SELECT * FROM trial_versions WHERE nct_id = %s ORDER BY version_no, version_date, field",
                (nct_id.upper(),),
            ).fetchall()
    except Exception as exc:
        raise LiveUnavailable(f"version history read failed: {type(exc).__name__}") from exc
    return [VersionEvent.model_validate(r) for r in rows]


# --- drafts (table owned by T25) -------------------------------------------------------------------


def save_draft(draft_id: str, text: str, schedule_text: str | None, parsed: ParsedProtocol | None) -> bool:
    """Store a pasted draft protocol and its parse in ``drafts``. Best effort: returns False without a
    database or on any database error (a draft that cannot be saved must never break a run)."""
    if not db_available():
        return False
    from psycopg.types.json import Jsonb

    from clinova.common.db import get_conn

    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO drafts (draft_id, text, schedule_text, parsed) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (draft_id) DO UPDATE SET text = EXCLUDED.text, "
                "schedule_text = EXCLUDED.schedule_text, parsed = EXCLUDED.parsed",
                (draft_id, text, schedule_text, Jsonb(parsed.model_dump(mode="json")) if parsed else None),
            )
        return True
    except Exception as exc:
        log.info("draft not saved: %s", type(exc).__name__)
        return False


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
