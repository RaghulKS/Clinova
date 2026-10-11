"""Request and response bodies of the Clinova API that are not already contract models.

Responses wrap contract models and carry ``source`` (``fixture`` / ``live`` / ``cache``) so the app can
show a "fixture data" tag whenever a route fell back to ``tests/fixtures``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from clinova.contracts import (
    Edit,
    Feedback,
    Fix,
    ParsedProtocol,
    Phase,
    Prediction,
    ReplayResult,
    Source,
    ValidationMetrics,
)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParseRequest(Body):
    """Either pasted text or an NCT id."""

    eligibility_text: str | None = Field(None, description="Pasted eligibility criteria.")
    schedule_text: str | None = Field(None, description="Pasted visit schedule.")
    nct_id: str | None = Field(None, description="Load this registered trial as first registered.")
    phase: Phase | None = Field(None, description="Phase, if known.")


class ParseResponse(Body):
    protocol: ParsedProtocol
    source: Source
    registered_on: date | None = Field(None, description="First-version date when loaded by NCT id.")
    note: str | None = Field(None, description="Plain-words note, e.g. why fixture data was used.")


class RunRequest(Body):
    """Start a run. Give ``protocol`` (the reviewed rule table), or ``nct_id``, or pasted text."""

    protocol: ParsedProtocol | None = None
    nct_id: str | None = None
    eligibility_text: str | None = None
    schedule_text: str | None = None
    phase: Phase | None = None
    edits: list[Edit] = Field(
        default_factory=list, description="Accepted fix edits applied before running (the quick re-run)."
    )
    n_personas: int = Field(1000, ge=25, le=10000)
    seeds: list[int] = Field(default_factory=lambda: [1, 2])


class RunCreated(Body):
    run_id: str
    status_url: str


class OptimizeRequest(Body):
    protocol: ParsedProtocol
    max_edits: int = Field(3, ge=1, le=3)


class OptimizeResponse(Body):
    fixes: list[Fix]
    source: Source


class FeedbackIn(Body):
    """A human decision; the server assigns the id and the time."""

    user_name: str = "anonymous"
    target_type: Literal["fix", "flag", "rule", "persona", "prediction", "run"]
    target_id: str
    trial_id: str | None = None
    decision: Literal["accept", "reject", "rate", "comment"]
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None


class FeedbackResponse(Body):
    feedback: Feedback
    source: Source = Field(description="live = stored in the database; fixture = kept in memory only.")


class ValidationResponse(Body):
    metrics: ValidationMetrics
    source: Source


class ReplayResponse(Body):
    replay: ReplayResult
    source: Source


class PulseResponse(Body):
    nct_id: str
    status: Literal["available", "not_run"]
    report_md: str | None = None
    source: Source
    note: str | None = None


class AuditResponse(Body):
    feedback: list[Feedback]
    predictions: list[Prediction]
    source: Source


class HealthResponse(Body):
    status: Literal["ok"] = "ok"
    contracts_version: str
    mode: str
    db: bool = Field(description="Database reachable.")


class Measures(Body):
    """The numbers shown side by side before and after accepting a fix. ``None`` = not available."""

    eligible_frac: float | None = Field(None, description="Share of virtual patients who pass the criteria.")
    enroll_rate: float | None = Field(None, description="Share of eligible virtual patients who enroll.")
    dropout_rate: float | None = Field(None, description="Share of enrolled who drop out before the last visit.")
    diversity_index: float | None = Field(None, description="1.0 matches the NSCLC reference population.")
    accrual_fail_p: float | None = None
    shortfall_p: float | None = None
    amend_p: float | None = None


class AcceptRequest(Body):
    """Accept one fix of a finished run."""

    run_id: str
    fix_id: str
    user_name: str = "anonymous"
    comment: str | None = None


class AcceptResponse(Body):
    """Result of accepting a fix: the logged decision plus before and after numbers.

    ``basis`` says how ``after`` was produced: ``quick_rerun`` = code screen, surrogate and model re-run
    with no new LLM calls; ``optimizer_estimate`` = the optimizer's own deltas applied to ``before``
    (used while the engine is on fixtures or a stage is unavailable)."""

    feedback: Feedback
    feedback_source: Source = Field(description="live = stored in the database; fixture = kept in memory only.")
    basis: Literal["quick_rerun", "optimizer_estimate"]
    before: Measures
    after: Measures
    protocol: ParsedProtocol = Field(description="The protocol with the fix's edits applied.")
    source: Source = Field(description="live when every re-run stage used the real engine, else fixture.")
    note: str | None = None
