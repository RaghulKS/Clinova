"""Fast surrogate of the persona agents (owner: T14, refit by T24)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from clinova.contracts import ParsedProtocol


class Surrogate:
    """Predicts enrollment and dropout from visit-burden features and persona traits, so the
    optimizer can re-score edits without Gemini calls."""

    @classmethod
    def fit(cls, outcomes: pd.DataFrame) -> Surrogate:
        """Fit on ``persona_outcomes`` rows joined with their protocol's visit-burden features."""
        raise NotImplementedError("T14 implements Surrogate.fit")

    def score(self, protocol: ParsedProtocol, cohort: pd.DataFrame) -> tuple[float, float]:
        """Return ``(enroll_rate, dropout_risk)`` for the eligible personas in ``cohort`` under
        ``protocol``'s visits. Must run in under 20 ms."""
        raise NotImplementedError("T14 implements Surrogate.score")

    def save(self, path: Path) -> None:
        """Persist to ``path`` (under DATA_DIR or models/)."""
        raise NotImplementedError("T14 implements Surrogate.save")

    @classmethod
    def load(cls, path: Path | None = None) -> Surrogate:
        """Load a saved surrogate (``None`` = the default location)."""
        raise NotImplementedError("T14 implements Surrogate.load")
