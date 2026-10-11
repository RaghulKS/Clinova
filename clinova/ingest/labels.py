"""Conservative outcome labels; unknown evidence stays unknown."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from clinova.common import llm
from clinova.common.config import get_settings
from clinova.contracts import ENDED_STATUSES, StopCategory


class StopReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: StopCategory


class Amendment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_class: Literal["widened", "narrowed", "mixed", "administrative"]
    explanation: str


def label_trial(trial, history):
    metadata = {}
    if trial.status not in ENDED_STATUSES or trial.split == "pending":
        return trial, metadata
    category = None
    if trial.status in {"TERMINATED", "WITHDRAWN"}:
        if trial.why_stopped:
            try:
                category = llm.generate_json(
                    "Classify this ClinicalTrials.gov stop reason. Use accrual only when enrollment/recruitment "
                    "problems are explicit; mixed efficacy and recruitment is other. Categories: accrual, safety, "
                    "efficacy_futility, business_funding, other, unknown. Text: " + json.dumps(trial.why_stopped),
                    StopReason, namespace="stop_reason", seed=get_settings().SEED,
                ).category
            except llm.LLMError as exc:
                metadata["stop_error"] = type(exc).__name__
        else:
            category = StopCategory.unknown
    accrual = trial.status == "TERMINATED" and category == StopCategory.accrual
    if trial.status == "TERMINATED" and category in (None, StopCategory.unknown):
        accrual = None
    shortfall = None
    if history.first is not None and trial.actual_enrollment is not None and trial.planned_enrollment is not None:
        shortfall = trial.actual_enrollment < 0.8 * trial.planned_enrollment
    amend = False if history.complete and trial.start_date else None
    for event in history.events:
        if event.field != "eligibility" or not trial.start_date or event.version_date <= trial.start_date:
            continue
        try:
            classified = llm.generate_json(
                "Classify the eligibility change as widened, narrowed, mixed, or administrative. "
                "Widened permits additional patients; mixed both widens and narrows. Explain the concrete change. "
                + json.dumps({"old": event.old_value, "new": event.new_value}),
                Amendment, namespace="amend_class", seed=get_settings().SEED,
            )
            event.change_class = classified.change_class
            metadata.setdefault("amendments", []).append({
                "version": event.version_no, "date": event.version_date.isoformat(),
                **classified.model_dump(),
            })
            if classified.change_class in {"widened", "mixed"}:
                amend = True
        except llm.LLMError as exc:
            metadata["amend_error"] = type(exc).__name__
            if amend is not True:
                amend = None
    return trial.model_copy(update={"stop_category": category, "label_accrual_fail": accrual,
                                    "label_shortfall": shortfall, "label_amend": amend}), metadata
