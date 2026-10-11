"""Convert between ``CriteriaRule`` objects and the editable table in the app (pure, no Streamlit)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from clinova.contracts import FIELD_SPECS, ClinicalField, CriteriaRule, Operator

OPERATOR_SYMBOL: dict[str, str] = {
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "eq": "=",
    "ne": "!=",
    "in": "in",
    "not_in": "not in",
    "present": "present",
    "absent": "absent",
}
SYMBOL_OPERATOR = {v: k for k, v in OPERATOR_SYMBOL.items()}
OPERATOR_CHOICES = list(OPERATOR_SYMBOL.values())
FIELD_CHOICES = [f.value for f in ClinicalField]

COLUMNS = ["Lock", "ID", "Kind", "Field", "Operator", "Value", "Unit", "Source text", "Status"]
STATUS_OK = "Parsed"
STATUS_BAD = "Needs review"


def value_to_text(value: Any) -> str:
    """Render a rule value for the table: lists as 'a, b', booleans as true/false, None as ''."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(value_to_text(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _scalar(text: str) -> bool | int | float | str:
    t = text.strip()
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def text_to_value(text: Any, operator: str) -> Any:
    """Inverse of ``value_to_text``. ``in`` / ``not in`` split on commas; present/absent take None."""
    if operator in ("present", "absent"):
        return None
    if text is None or (isinstance(text, float) and pd.isna(text)) or str(text).strip() == "":
        return None
    s = str(text).strip()
    if operator in ("in", "not in"):
        if s.startswith("["):
            try:
                return json.loads(s)
            except ValueError:
                pass
        return [_scalar(p) for p in s.split(",") if p.strip()]
    return _scalar(s)


def rules_to_df(rules: list[CriteriaRule]) -> pd.DataFrame:
    rows = []
    for r in rules:
        rows.append(
            {
                "Lock": r.locked,
                "ID": r.rule_id,
                "Kind": r.kind,
                "Field": r.field.value,
                "Operator": OPERATOR_SYMBOL[r.operator.value],
                "Value": value_to_text(r.value),
                "Unit": r.unit or "",
                "Source text": r.raw_text,
                "Status": STATUS_OK if r.parse_status == "ok" else STATUS_BAD,
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def df_to_rules(df: pd.DataFrame, original: list[CriteriaRule]) -> tuple[list[CriteriaRule], list[str]]:
    """Rebuild rules from the edited table. Returns ``(rules, problems)``; a row that does not validate is
    kept as the original and named in ``problems``. Editing a flagged row to a known field clears its flag."""
    by_id = {r.rule_id: r for r in original}
    out: list[CriteriaRule] = []
    problems: list[str] = []
    for _, row in df.iterrows():
        rid = str(row["ID"])
        base = by_id.get(rid)
        if base is None:
            problems.append(f"{rid}: unknown rule id")
            continue
        try:
            operator = Operator(SYMBOL_OPERATOR[str(row["Operator"])])
            field = ClinicalField(str(row["Field"]))
            value = text_to_value(row["Value"], str(row["Operator"]))
            unit = str(row["Unit"]).strip() or None
            if unit is None and field != ClinicalField.other and field in FIELD_SPECS:
                unit = FIELD_SPECS[field].unit
            unchanged = (field, operator, value, unit) == (base.field, base.operator, base.value, base.unit)
            if unchanged:
                update = {"locked": bool(row["Lock"])}
            else:
                # An edited row is re-judged: a known field makes it parseable and clears the old note.
                ok = field != ClinicalField.other
                update = {
                    "locked": bool(row["Lock"]),
                    "field": field,
                    "operator": operator,
                    "value": value,
                    "unit": unit,
                    "parse_status": "ok" if ok else "unparseable",
                    "note": None if ok else base.note,
                }
            out.append(base.model_copy(update=update))
            CriteriaRule.model_validate(out[-1].model_dump())
        except (ValueError, KeyError) as exc:
            if out and out[-1].rule_id == rid:
                out.pop()
            out.append(base)
            problems.append(f"{rid}: {exc}")
    return out, problems


def rule_in_words(rule: CriteriaRule) -> str:
    """Short plain-words form, e.g. 'creatinine clearance >= 60 mL/min'."""
    label = rule.field.value.replace("_", " ")
    sym = OPERATOR_SYMBOL[rule.operator.value]
    val = value_to_text(rule.value)
    unit = f" {rule.unit}" if rule.unit else ""
    if rule.operator.value in ("present", "absent"):
        return f"{label} {sym}"
    return f"{label} {sym} {val}{unit}".strip()
