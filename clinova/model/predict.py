"""Scoring and criterion flags (owner: T22). See ARCHITECTURE.md 5.6 and 7."""

from __future__ import annotations

from collections.abc import Sequence

from clinova.contracts import CriterionFlag, ParsedProtocol, RiskScore, SimRunSummary


def score(
    protocol_or_trial: ParsedProtocol | str,
    *,
    sim: Sequence[SimRunSummary] | None = None,
    model_version: str | None = None,
) -> list[RiskScore]:
    """Calibrated RiskScores for accrual_fail, shortfall and amend (in that order) for a parsed
    protocol or an NCT id (its first registered version). Ranges combine bootstrap models and
    the spread across ``sim`` seeds; confidence comes from embedding distance to the 10 nearest
    training trials. Callers log results with ``loop.ledger.record_prediction``."""
    raise NotImplementedError("T22 implements clinova.model.predict.score")


def flag_criteria(protocol: ParsedProtocol, *, k: int = 3) -> list[CriterionFlag]:
    """Rank rules by the drop in predicted accrual-failure probability when each is relaxed one
    step or removed (re-screen cached cohort, rebuild features, re-score). Returns the top ``k``
    flags (rank 1 first), each saying where, why and what to do. Locked and unparseable rules are skipped."""
    raise NotImplementedError("T22 implements clinova.model.predict.flag_criteria")
