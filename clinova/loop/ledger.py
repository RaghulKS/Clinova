"""Prediction ledger and feedback (owner: T21). Tables ``predictions`` and ``feedback``."""

from __future__ import annotations

from datetime import datetime

from clinova.contracts import Feedback, Prediction, RiskScore


def record_prediction(
    trial_id: str,
    risk: RiskScore,
    *,
    context: str = "live",
    predicted_at: datetime | None = None,
) -> Prediction:
    """Store one RiskScore as a Prediction (new prediction_id, model_version from the score,
    ``predicted_at`` defaults to now UTC; the replay passes the simulated month) and return it."""
    raise NotImplementedError("T21 implements clinova.loop.ledger.record_prediction")


def record_feedback(feedback: Feedback) -> Feedback:
    """Store a human decision (fix accept/reject, persona-realism rating, comment) and return it."""
    raise NotImplementedError("T21 implements clinova.loop.ledger.record_feedback")
