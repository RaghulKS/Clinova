"""Network-free persistence tests using the real table DDL and SQLite SQL execution."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from clinova.common.db import _statements
from clinova.contracts import Feedback, RiskScore
from clinova.loop import ledger

NOW = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def database(monkeypatch):
    conn = sqlite3.connect(":memory:")
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    for statement in _statements(schema):
        if any(f"CREATE TABLE IF NOT EXISTS {name} (" in statement for name in ("predictions", "feedback")):
            conn.execute(statement.replace("DEFAULT now()", "DEFAULT CURRENT_TIMESTAMP"))

    class Connection:
        def execute(self, sql, params=()):
            params = tuple(p.isoformat() if isinstance(p, datetime) else p for p in params)
            return conn.execute(sql.replace("%s", "?").replace("CREATE OR REPLACE VIEW", "CREATE VIEW"), params)

    @contextmanager
    def get_conn():
        with conn:
            yield Connection()

    monkeypatch.setattr(ledger, "get_conn", get_conn)
    yield conn
    conn.close()


def risk(**updates):
    return RiskScore.model_validate(dict(
        target="accrual_fail", p=0.7, ci_low=0.5, ci_high=0.9,
        confidence="medium", model_version="model-v1", **updates,
    ))


def feedback(**updates):
    data = dict(feedback_id=uuid4().hex, target_type="fix", target_id="fix-1",
                trial_id="draft-1", decision="accept", created_at=NOW)
    data.update(updates)
    return Feedback.model_validate(data)


def test_prediction_persists_all_fields_and_utc_timestamp(database):
    timestamp = NOW.astimezone(timezone(timedelta(hours=-5)))
    prediction = ledger.record_prediction("draft-1", risk(), context="replay", predicted_at=timestamp)
    row = database.execute("SELECT * FROM predictions").fetchone()
    assert row == (prediction.prediction_id, "draft-1", "model-v1", "accrual_fail", 0.7,
                   0.5, 0.9, "replay", NOW.isoformat())
    assert prediction.predicted_at == NOW and prediction.predicted_at.tzinfo == UTC
    assert len(prediction.prediction_id) == 32


def test_live_prediction_defaults_to_now_and_new_event_per_call(database):
    before = datetime.now(UTC)
    first = ledger.record_prediction("draft-1", risk())
    second = ledger.record_prediction("draft-1", risk())
    assert before <= first.predicted_at <= datetime.now(UTC)
    assert first.context == "live" and first.prediction_id != second.prediction_id
    assert database.execute("SELECT count(*) FROM predictions").fetchone() == (2,)


@pytest.mark.parametrize("arguments", [
    {"trial_id": " "}, {"context": "unknown"}, {"predicted_at": NOW.replace(tzinfo=None)},
])
def test_invalid_prediction_never_writes(database, arguments):
    kwargs = dict(trial_id="draft-1", risk=risk())
    kwargs.update(arguments)
    with pytest.raises(ValueError):
        ledger.record_prediction(**kwargs)
    assert database.execute("SELECT count(*) FROM predictions").fetchone() == (0,)


@pytest.mark.parametrize("field,value", [("p", float("nan")), ("ci_low", 0.95), ("model_version", " ")])
def test_mutated_risk_is_revalidated(database, field, value):
    score = risk()
    setattr(score, field, value)
    with pytest.raises(ValueError):
        ledger.record_prediction("draft-1", score)
    assert database.execute("SELECT count(*) FROM predictions").fetchone() == (0,)


@pytest.mark.parametrize("decision,extra", [
    ("accept", {}), ("reject", {"comment": "Too restrictive"}),
    ("rate", {"target_type": "persona", "rating": 4}), ("comment", {"comment": "Realistic travel burden"}),
])
def test_feedback_persists_decisions_and_user(database, decision, extra):
    item = feedback(decision=decision, user_name="reviewer", **extra)
    assert ledger.record_feedback(item) == item
    row = database.execute("SELECT user_name, decision, rating, comment FROM feedback").fetchone()
    assert row == ("reviewer", decision, item.rating, item.comment)


def test_feedback_retry_is_idempotent_but_conflicting_payload_is_rejected(database):
    item = feedback()
    assert ledger.record_feedback(item) == ledger.record_feedback(item)
    with pytest.raises(ValueError, match="different contents"):
        ledger.record_feedback(item.model_copy(update={"decision": "reject"}))
    assert database.execute("SELECT decision, count(*) FROM feedback").fetchone() == ("accept", 1)


@pytest.mark.parametrize("updates", [
    {"decision": "rate"}, {"rating": 2}, {"decision": "comment", "comment": " "},
    {"feedback_id": " "}, {"target_id": ""}, {"trial_id": " "}, {"user_name": " "},
    {"created_at": NOW.replace(tzinfo=None)}, {"decision": "rate", "rating": 6},
])
def test_invalid_feedback_never_writes(database, updates):
    # model_copy also checks the boundary against a model mutated after validation.
    item = feedback().model_copy(update=updates)
    with pytest.raises(ValueError):
        ledger.record_feedback(item)
    assert database.execute("SELECT count(*) FROM feedback").fetchone() == (0,)


def test_feedback_normalizes_timezone_without_mutating_caller(database):
    item = feedback(created_at=NOW.astimezone(timezone(timedelta(hours=5))))
    stored = ledger.record_feedback(item)
    assert stored.created_at.tzinfo == UTC
    assert item.created_at.utcoffset() == timedelta(hours=5)


def test_audit_view_links_only_explicit_prediction_targets_and_keeps_all_rows(database):
    prediction = ledger.record_prediction("draft-1", risk(), predicted_at=NOW)
    untouched = ledger.record_prediction("draft-1", risk(), predicted_at=NOW)
    linked = feedback(target_type="prediction", target_id=prediction.prediction_id, trial_id=None)
    also_linked = feedback(target_type="prediction", target_id=prediction.prediction_id)
    unrelated = [
        feedback(), feedback(target_type="persona", trial_id=None),
        feedback(target_type="prediction", target_id="not-yet-logged"),
        feedback(target_type="prediction", target_id=prediction.prediction_id, trial_id="other-draft"),
    ]
    for item in (linked, also_linked, *unrelated):
        ledger.record_feedback(item)
    ledger.ensure_audit_view()
    rows = database.execute("SELECT prediction_id, feedback_id, trial_id FROM audit_log").fetchall()
    assert len(rows) == 7
    assert (prediction.prediction_id, linked.feedback_id, "draft-1") in rows
    assert (prediction.prediction_id, also_linked.feedback_id, "draft-1") in rows
    assert (untouched.prediction_id, None, "draft-1") in rows
    assert all((None, item.feedback_id, item.trial_id) in rows for item in unrelated)


def test_database_failure_propagates(monkeypatch):
    @contextmanager
    def unavailable():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(ledger, "get_conn", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        ledger.record_prediction("draft-1", risk())
    with pytest.raises(RuntimeError, match="database unavailable"):
        ledger.record_feedback(feedback())
