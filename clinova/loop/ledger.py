"""Prediction ledger and feedback (owner: T21). Tables ``predictions`` and ``feedback``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from clinova.common.db import get_conn
from clinova.contracts import Feedback, Prediction, RiskScore

_FEEDBACK_COLUMNS = (
    "feedback_id", "user_name", "target_type", "target_id", "trial_id", "decision", "rating", "comment", "created_at"
)

# Only an explicit prediction target links a decision to a score. Joining on the
# trial alone would falsely attach fix/persona decisions to every trial prediction.
AUDIT_LOG_SQL = """
CREATE OR REPLACE VIEW audit_log AS
SELECT COALESCE(f.created_at, p.predicted_at) AS event_at,
       COALESCE(f.trial_id, p.trial_id) AS trial_id,
       p.prediction_id, p.model_version, p.target, p.p, p.ci_low, p.ci_high,
       p.context, p.predicted_at,
       f.feedback_id, f.user_name, f.target_type, f.target_id, f.decision,
       f.rating, f.comment, f.created_at
FROM predictions p
FULL OUTER JOIN feedback f
  ON f.target_type = 'prediction' AND f.target_id = p.prediction_id
 AND (f.trial_id IS NULL OR f.trial_id = p.trial_id)
"""


def ensure_audit_view() -> None:
    """Install the T21-owned audit view once after applying the base schema."""
    with get_conn() as conn:
        conn.execute(AUDIT_LOG_SQL)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ledger timestamps must include a timezone")
    return value.astimezone(UTC)


def _nonempty(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def record_prediction(
    trial_id: str,
    risk: RiskScore,
    *,
    context: str = "live",
    predicted_at: datetime | None = None,
) -> Prediction:
    """Store one RiskScore as a Prediction (new prediction_id, model_version from the score,
    ``predicted_at`` defaults to now UTC; the replay passes the simulated month) and return it."""
    risk = RiskScore.model_validate(risk.model_dump())
    _nonempty(trial_id, "trial_id")
    _nonempty(risk.model_version, "model_version")
    prediction = Prediction(
        prediction_id=uuid4().hex,
        trial_id=trial_id,
        model_version=risk.model_version,
        target=risk.target,
        p=risk.p,
        ci_low=risk.ci_low,
        ci_high=risk.ci_high,
        context=context,
        predicted_at=_utc(predicted_at if predicted_at is not None else datetime.now(UTC)),
    )
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO predictions "
            "(prediction_id, trial_id, model_version, target, p, ci_low, ci_high, context, predicted_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            tuple(prediction.model_dump().values()),
        )
    return prediction


def record_feedback(feedback: Feedback) -> Feedback:
    """Store a human decision (fix accept/reject, persona-realism rating, comment) and return it."""
    feedback = Feedback.model_validate(feedback.model_dump())
    for name in ("feedback_id", "user_name", "target_id"):
        _nonempty(getattr(feedback, name), name)
    if feedback.trial_id is not None:
        _nonempty(feedback.trial_id, "trial_id")
    if (feedback.decision == "rate") != (feedback.rating is not None):
        raise ValueError("rating is required exactly when decision is 'rate'")
    if feedback.decision == "comment" and not (feedback.comment or "").strip():
        raise ValueError("comment decision requires a nonblank comment")
    feedback.created_at = _utc(feedback.created_at)
    columns = ", ".join(_FEEDBACK_COLUMNS)
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO feedback ({columns}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (feedback_id, created_at) DO NOTHING",
            tuple(getattr(feedback, name) for name in _FEEDBACK_COLUMNS),
        )
        row = conn.execute(
            f"SELECT {columns} FROM feedback WHERE feedback_id = %s AND created_at = %s",
            (feedback.feedback_id, feedback.created_at),
        ).fetchone()
        stored = Feedback.model_validate(dict(zip(_FEEDBACK_COLUMNS, row, strict=True)))
        if stored != feedback:
            raise ValueError("feedback identity already exists with different contents")
    return stored
