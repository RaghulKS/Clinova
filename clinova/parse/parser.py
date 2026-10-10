"""Criteria parser (owner: T12). See ARCHITECTURE.md 5.2 and docs/CONTRACTS.md."""

from __future__ import annotations

from clinova.contracts import ParsedProtocol, Phase


def parse_protocol(
    eligibility_text: str,
    schedule_text: str | None = None,
    *,
    trial_id: str,
    version_no: int = 0,
    phase: Phase | None = None,
) -> ParsedProtocol:
    """Turn free-text eligibility criteria (and an optional visit schedule) into a ParsedProtocol.

    One ``CriteriaRule`` per atomic condition, mapped to ``ClinicalField`` with values converted to
    the canonical units in ``FIELD_SPECS``. Unmappable conditions get ``field=other`` and
    ``parse_status=unparseable`` (flag, never guess). Deterministic sanity checks run after the
    model (plausible range per field, operator valid for the field kind). Rules on
    ``LOCKED_BY_DEFAULT`` fields are ``locked=True``. Setting is inferred (first_line /
    second_line_plus / any). Visits come from ``schedule_text`` when given (``estimated=False``);
    otherwise ``visits`` is empty and callers use ``parse.visits.estimate_visits``.

    All Gemini calls go through ``clinova.common.llm.generate_json`` (cached; demo mode works).
    """
    raise NotImplementedError("T12 implements clinova.parse.parser.parse_protocol")
