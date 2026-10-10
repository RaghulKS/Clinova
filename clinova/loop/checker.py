"""Outcome checker (owner: T21). Tables ``outcomes`` and ``prediction_scores``."""

from __future__ import annotations


def check_outcomes(*, limit: int | None = None, retrain_threshold: int = 25) -> dict[str, int]:
    """Re-fetch ``pending`` trials via ``clinova.ingest.ctgov``, write ``outcomes`` for trials that
    ended (observed_at = completion or termination date, labels per T11's rules), score matching
    predictions into ``prediction_scores``, and retrain when ``retrain_threshold`` or more new
    outcomes arrived. Returns counts, e.g. ``{"checked": 300, "ended": 12, "scored": 30, "retrained": 0}``."""
    raise NotImplementedError("T21 implements clinova.loop.checker.check_outcomes")
