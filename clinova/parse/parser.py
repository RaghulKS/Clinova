"""Criteria parser (owner: T12). See ARCHITECTURE.md 5.2 and docs/CONTRACTS.md."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from clinova.common import llm
from clinova.contracts import (
    FIELD_SPECS,
    LOCKED_BY_DEFAULT,
    ClinicalField,
    CriteriaRule,
    Operator,
    ParsedProtocol,
    Phase,
    Setting,
)

NAMESPACE = "parse"
_NUMERIC_OPS = {Operator.lt, Operator.le, Operator.gt, Operator.ge, Operator.eq, Operator.ne, Operator.in_, Operator.not_in}
_ORDERED_OPS = _NUMERIC_OPS
_CATEGORICAL_OPS = {Operator.eq, Operator.ne, Operator.in_, Operator.not_in}
_BOOLEAN_OPS = {Operator.present, Operator.absent, Operator.eq, Operator.ne}


class _RuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["inclusion", "exclusion"]
    field: ClinicalField
    operator: Operator
    value_json: str = Field(description="JSON-encoded value: 18, 1.5, \"IIIB\", [\"IVA\",\"IVB\"], true, or null for present/absent.")
    unit: str | None = None
    raw_text: str = Field(description="Exact source fragment this rule came from.")
    unparseable_reason: str | None = Field(None, description="Set only when field is 'other': why it cannot be coded.")


class ParseOut(BaseModel):
    """What the model returns; converted to ParsedProtocol by parse_protocol."""

    model_config = ConfigDict(extra="forbid")
    setting: Setting
    rules: list[_RuleOut]


_FIELDS_HELP = "\n".join(
    f"- {f.value}: "
    + (
        f"{s.kind}, unit {s.unit}, plausible {s.min}..{s.max}"
        if s.kind == "numeric"
        else f"{s.kind}, values {list(s.values)}"
        if s.values
        else s.kind
    )
    for f, s in FIELD_SPECS.items()
)

PROMPT = """You convert NSCLC clinical-trial eligibility criteria into atomic, machine-checkable rules.

Fields (use exactly these names):
{fields}

Rules for you:
1. One rule per atomic condition. Split "A or B" lists of values into one `in` rule when they test the same field; split conditions on different fields into separate rules. "ECOG 0-1" is one rule: ecog le 1. "Age 18 to 75" is two rules (age ge 18, age le 75).
2. kind = "inclusion" when listed under inclusion criteria or phrased as a requirement; "exclusion" when listed under exclusion criteria. A patient who satisfies an exclusion rule is excluded, so write the exclusion rule as the CONDITION being excluded (e.g. exclusion `brain_mets in ["untreated"]`, exclusion `autoimmune_disease present`, exclusion `egfr_status eq "positive"`).
3. Operators: lt le gt ge eq ne in not_in present absent. Use present/absent (value null) for boolean fields. Use `in`/`not_in` with a list for categorical sets. Stages are ordered {stages}: "stage IIIB or IV" is `stage in ["IIIB","IIIC","IVA","IVB"]`; "stage IV" is `in ["IVA","IVB"]`; "stage III" is `in ["IIIA","IIIB","IIIC"]`. "Non-squamous" is histology not_in ["squamous"]. brain_mets values: none, treated_stable, untreated. "No brain metastases at all" is exclusion brain_mets in ["treated_stable","untreated"]; "symptomatic/active/untreated brain metastases" is exclusion brain_mets in ["untreated"].
4. Convert to canonical units: CrCl in mL/min (eGFR accepted); ANC and platelets in 10^9/L (1500/mm3 -> 1.5; 100,000/uL -> 100); hemoglobin in g/dL (90 g/L -> 9.0); AST, ALT, bilirubin as multiples of ULN; PD-L1 as TPS percent; ECOG as the score (Karnofsky 70-80 -> ECOG le 1; KPS >= 60 -> ECOG le 2; Zubrod = ECOG); life expectancy in months (12 weeks -> 3). Set `unit` to the canonical unit from the field list (null for categorical and boolean).
5. Numbers are plain JSON numbers; do not put units in value_json.
6. Anything you cannot map to a field with a precise threshold (vague "adequate organ function", "serious comorbidity", drug-specific exclusions, washout periods, other malignancies, prior-therapy details not covered by prior_lines/prior_platinum/prior_immunotherapy, investigational-drug use) becomes field "other", operator "eq", value_json "null", with `unparseable_reason`. Never guess a threshold the text does not state. Do not invent rules the text does not contain.
7. Informed consent / ability to consent: consent_capacity present (inclusion). Pregnancy or breast-feeding: exclusion pregnancy present. Active infection (including HIV/hepatitis only if stated as an active infection): active_infection present.
8. raw_text is the exact fragment of the source text.
9. setting: "first_line" if the criteria require no prior systemic therapy for advanced disease, "second_line_plus" if they require prior therapy/progression on prior lines, else "any".{phase_line}

ELIGIBILITY CRITERIA:
\"\"\"
{text}
\"\"\"
"""


def _bad_range(spec, v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (
        (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max)
    )


def sanity(field: ClinicalField, op: Operator, value) -> str | None:
    """Deterministic post-check. Returns a violation message, or None if the rule is plausible."""
    spec = FIELD_SPECS[field]
    if spec.kind == "free":
        return "field 'other'"
    ops = {"numeric": _NUMERIC_OPS, "ordinal": _ORDERED_OPS, "categorical": _CATEGORICAL_OPS, "boolean": _BOOLEAN_OPS}[spec.kind]
    if op not in ops:
        return f"operator {op.value} invalid for {spec.kind} field {field.value}"
    if op in (Operator.present, Operator.absent):
        return None if value is None else "present/absent must have null value"
    vals = value if isinstance(value, list) else [value]
    if (op in (Operator.in_, Operator.not_in)) != isinstance(value, list):
        return f"operator {op.value} and value shape {type(value).__name__} disagree"
    if not vals or any(v is None for v in vals):
        return "missing value"
    if spec.kind == "numeric":
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
            return "non-numeric value for numeric field"
        if any(_bad_range(spec, v) for v in vals):
            return f"value {value} outside plausible range {spec.min}..{spec.max} {spec.unit}"
    elif spec.kind in ("ordinal", "categorical"):
        if any(v not in spec.values for v in vals):
            return f"value {value} not in vocabulary {list(spec.values)}"
    elif spec.kind == "boolean" and any(not isinstance(v, bool) for v in vals):
        return "non-boolean value for boolean field"
    return None


def _convert(out: ParseOut) -> list[CriteriaRule]:
    rules: list[CriteriaRule] = []
    n = {"inclusion": 0, "exclusion": 0}
    for r in sorted(out.rules, key=lambda r: r.kind != "inclusion"):  # inclusion first, stable
        try:
            value, bad = json.loads(r.value_json), None
        except ValueError:
            value, bad = None, "value_json is not valid JSON"
        if r.field == ClinicalField.other:
            bad = r.unparseable_reason or "unmappable"
        else:
            bad = bad or sanity(r.field, r.operator, value)
        n[r.kind] += 1
        rid = ("I" if r.kind == "inclusion" else "E") + str(n[r.kind])
        spec = FIELD_SPECS[r.field]
        common = dict(rule_id=rid, kind=r.kind, raw_text=r.raw_text, locked=r.field in LOCKED_BY_DEFAULT)
        if bad:
            note = f"sanity check: {bad}" if r.field != ClinicalField.other else bad
            rules.append(CriteriaRule(**common, field=ClinicalField.other, operator=Operator.eq, value=None, unit=None,
                                      parse_status="unparseable", note=note))
        else:
            rules.append(CriteriaRule(**common, field=r.field, operator=r.operator, value=value, unit=spec.unit if spec.kind == "numeric" else None))
    return rules


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
    prompt = PROMPT.format(
        fields=_FIELDS_HELP,
        stages=" < ".join(FIELD_SPECS[ClinicalField.stage].values),
        phase_line=f" The trial is {phase.value}." if phase else "",
        text=eligibility_text.strip(),
    )
    out = llm.generate_json(prompt, ParseOut, namespace=NAMESPACE, seed=0, temperature=0.0)
    visits = []
    if schedule_text:
        from clinova.parse.visits import extract_visits

        visits = extract_visits(schedule_text, trial_id=trial_id)
    return ParsedProtocol(
        trial_id=trial_id, version_no=version_no, phase=phase, setting=out.setting,
        rules=_convert(out), visits=visits,
    )
