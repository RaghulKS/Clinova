"""In-memory audit store, used while the ledger (T21) or the database is not available.

Feedback and predictions that could not be written to the database are kept here so the Audit log tab
and the accept/reject flow work end to end. They disappear when the API restarts.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

from clinova.contracts import Feedback, Prediction, RiskScore


class MemoryAudit:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.feedback: list[Feedback] = []
        self.predictions: list[Prediction] = []

    def clear(self) -> None:
        with self._lock:
            self.feedback.clear()
            self.predictions.clear()

    def add_feedback(self, fb: Feedback) -> None:
        with self._lock:
            self.feedback.append(fb)

    def add_prediction(self, trial_id: str, risk: RiskScore, context: str) -> Prediction:
        pred = Prediction(
            prediction_id=uuid.uuid4().hex,
            trial_id=trial_id,
            model_version=risk.model_version,
            target=risk.target,
            p=risk.p,
            ci_low=risk.ci_low,
            ci_high=risk.ci_high,
            context=context,  # type: ignore[arg-type]
            predicted_at=datetime.now(UTC),
        )
        with self._lock:
            self.predictions.append(pred)
        return pred

    def snapshot(self) -> tuple[list[Feedback], list[Prediction]]:
        with self._lock:
            return (
                sorted(self.feedback, key=lambda f: f.created_at, reverse=True),
                sorted(self.predictions, key=lambda p: p.predicted_at, reverse=True),
            )


MEMORY = MemoryAudit()
