"""Eligibility screen (owner: T13). Pure, vectorized, deterministic. See ARCHITECTURE.md 5.4."""

from __future__ import annotations

import operator
from collections.abc import Sequence
from copy import deepcopy

import numpy as np
import pandas as pd

from clinova.contracts import (
    FIELD_SPECS,
    CriteriaRule,
    Edit,
    Funnel,
    FunnelStage,
    Operator,
    RuleExclusion,
    ScreenResult,
)

_COMPARE = {
    Operator.lt: operator.lt, Operator.le: operator.le,
    Operator.gt: operator.gt, Operator.ge: operator.ge,
    Operator.eq: operator.eq, Operator.ne: operator.ne,
}
_ORDERED = frozenset({Operator.lt, Operator.le, Operator.gt, Operator.ge})
_MEMBERSHIP = frozenset({Operator.in_, Operator.not_in})
_SYMBOL = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "=", "ne": "!="}
_NAMES = {
    "age": "Age", "stage": "Stage", "ecog": "ECOG", "prior_lines": "Prior lines",
    "prior_platinum": "Prior platinum chemotherapy", "prior_immunotherapy": "Prior PD-(L)1 inhibitor",
    "measurable_disease": "Measurable disease (RECIST 1.1)", "life_expectancy_months": "Life expectancy",
    "anc": "ANC", "platelets": "Platelets", "hemoglobin": "Hemoglobin",
    "crcl_ml_min": "Creatinine clearance", "ast_x_uln": "AST", "alt_x_uln": "ALT",
    "bilirubin_x_uln": "Bilirubin", "consent_capacity": "Can consent",
    "brain_mets": "brain metastases", "autoimmune_disease": "autoimmune disease",
    "interstitial_lung_disease": "interstitial lung disease", "active_infection": "active infection",
    "pregnancy": "pregnancy or breastfeeding",
}


def _label(rule: CriteriaRule) -> str:
    name = _NAMES.get(rule.field, rule.field.replace("_", " "))
    op = rule.operator
    if op in _MEMBERSHIP:
        text = f"{name} {'' if op == Operator.in_ else 'not '}{', '.join(map(str, rule.value))}"
    elif op in (Operator.present, Operator.absent):
        text = name if op == Operator.present else f"No {name}"
    else:
        unit = f" {rule.unit}" if rule.unit not in (None, "score", "lines") else ""
        text = f"{name} {_SYMBOL[op]} {rule.value}{unit}"
    if rule.kind == "exclusion":
        text = f"Excl. {text}"
    return f"{text} ({rule.rule_id})"


def _validate_rule(rule: CriteriaRule) -> None:
    """Reject malformed encodable rules rather than guessing their intended comparison."""
    if rule.parse_status == "unparseable":
        return
    spec = FIELD_SPECS[rule.field]
    op, value = rule.operator, rule.value
    error = None
    if op in (Operator.present, Operator.absent):
        if spec.kind != "boolean" or value is not None:
            error = "present/absent require a boolean field and value None"
    elif op in _MEMBERSHIP:
        if not isinstance(value, list):
            error = "in/not_in require a list"
    elif value is None or isinstance(value, list):
        error = "comparison requires a scalar value"
    if not error and op in _ORDERED and spec.kind not in ("numeric", "ordinal"):
        error = "ordered comparison requires a numeric or ordinal field"
    if not error and op not in (Operator.present, Operator.absent):
        values = value if isinstance(value, list) else [value]
        if spec.kind == "numeric" and any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in values
        ):
            error = "numeric field requires finite numeric values"
        elif spec.kind in ("categorical", "ordinal") and any(v not in spec.values for v in values):
            error = f"value must belong to {spec.values}"
        elif spec.kind == "boolean" and any(not isinstance(v, bool) for v in values):
            error = "boolean field requires boolean values"
    if error:
        raise ValueError(f"rule {rule.rule_id}: {error}")


def _failures(rules: Sequence[CriteriaRule], cohort: pd.DataFrame):
    """Return one vector of failures per applied rule; column conversion is reused."""
    ids = [r.rule_id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule_id")
    applied, skipped, failures = [], [], []
    columns = {}
    for rule in rules:
        if rule.parse_status == "unparseable":
            skipped.append(rule.rule_id)
            continue
        _validate_rule(rule)
        field = rule.field
        if field not in columns:
            if field not in cohort:
                raise ValueError(f"cohort is missing field {field!r} for rule {rule.rule_id}")
            spec = FIELD_SPECS[field]
            series = cohort[field]
            missing = series.isna().to_numpy()
            if spec.kind == "numeric":
                values = series.to_numpy(dtype=float, na_value=np.nan)
            else:
                values = series.to_numpy(dtype=object, copy=True)
                values[missing] = False if spec.kind == "boolean" else ""
            columns[field] = (values, missing)
        values, missing = columns[field]
        op, threshold = rule.operator, rule.value
        if op in (Operator.present, Operator.absent):
            satisfied = values == (op == Operator.present)
        elif op in _MEMBERSHIP:
            satisfied = np.isin(values, threshold)
            if op == Operator.not_in:
                satisfied = ~satisfied
        elif FIELD_SPECS[field].kind == "ordinal" and op in _ORDERED:
            vocabulary = FIELD_SPECS[field].values
            key = (field, "ordinal")
            if key not in columns:
                codes = pd.Categorical(values, categories=vocabulary, ordered=True).codes
                if np.any((codes < 0) & ~missing):
                    raise ValueError(f"cohort field {field!r} contains an unknown ordinal value")
                columns[key] = codes
            satisfied = _COMPARE[op](columns[key], vocabulary.index(threshold))
        else:
            satisfied = _COMPARE[op](values, threshold)
        failed = ~satisfied if rule.kind == "inclusion" else satisfied
        failures.append(np.asarray(failed, dtype=bool) & ~missing)
        applied.append(rule)
    matrix = np.stack(failures) if failures else np.zeros((0, len(cohort)), dtype=bool)
    return applied, skipped, matrix


def screen(rules: Sequence[CriteriaRule], cohort: pd.DataFrame, *, trial_id: str = "") -> tuple[np.ndarray, Funnel]:
    """Apply ``rules`` to the cohort dataframe (``contracts.COHORT_COLUMNS``).

    Semantics are in the ``CriteriaRule`` docstring: inclusion must hold, exclusion excludes when it
    holds, missing values pass, unparseable rules are skipped and listed in ``Funnel.skipped_rules``.
    Returns a boolean eligibility mask aligned with ``cohort`` rows and the Funnel (sequential
    stages in rule order + per-rule marginal exclusions). Target: 10,000 rows x 25 rules < 50 ms.
    """
    applied, skipped, failed = _failures(rules, cohort)
    n = len(cohort)
    fail_counts = failed.sum(axis=0)
    eligible = fail_counts == 0
    remaining = np.ones(n, dtype=bool)
    stages = [FunnelStage(label="Screened", remaining=n, excluded=0)]
    marginal = []
    for rule, failure in zip(applied, failed, strict=True):
        excluded = int(np.count_nonzero(remaining & failure))
        remaining &= ~failure
        label = _label(rule)
        stages.append(FunnelStage(rule_id=rule.rule_id, label=label,
                                  remaining=int(remaining.sum()), excluded=excluded))
        excluded_n = int(failure.sum())
        marginal.append(RuleExclusion(
            rule_id=rule.rule_id, field=rule.field, label=label, excluded_n=excluded_n,
            excluded_frac=excluded_n / n if n else 0.0,
            excluded_only_n=int(np.count_nonzero(failure & (fail_counts == 1))),
        ))
    marginal.sort(key=lambda item: -item.excluded_n)
    n_eligible = int(eligible.sum())
    return eligible, Funnel(
        trial_id=trial_id, n_screened=n, n_eligible=n_eligible,
        eligible_frac=n_eligible / n if n else 0.0,
        stages=stages, marginal=marginal, skipped_rules=skipped,
    )


def screen_results(rules: Sequence[CriteriaRule], cohort: pd.DataFrame) -> list[ScreenResult]:
    """Per-persona detail (which rules each persona fails). Slower than ``screen``; for the UI and tests."""
    applied, _, failed = _failures(rules, cohort)
    if "persona_id" not in cohort:
        raise ValueError("cohort is missing persona_id")
    ids = np.array([rule.rule_id for rule in applied], dtype=object)
    results = []
    for persona_id, failures in zip(cohort["persona_id"], failed.T, strict=True):
        failed_rules = ids[failures].tolist()
        results.append(ScreenResult(
            persona_id=persona_id, eligible=not failed_rules, failed_rules=failed_rules,
            first_failed_rule=failed_rules[0] if failed_rules else None,
        ))
    return results


def apply_edits(rules: Sequence[CriteriaRule], edits: Sequence[Edit]) -> list[CriteriaRule]:
    """Return a new rule list with the rule edits applied (``relax_rule`` sets ``new_value`` and
    optionally ``new_operator``; ``drop_rule`` removes the rule). Visit edits are ignored here
    (the optimizer applies them to visits). Raises ValueError for edits on ``locked`` rules or
    unknown rule_ids. Never mutates the input."""
    result = [rule.model_copy(deep=True) for rule in rules]
    if len({rule.rule_id for rule in result}) != len(result):
        raise ValueError("duplicate rule_id")
    for edit in edits:
        if edit.kind not in ("relax_rule", "drop_rule"):
            continue
        index = next((i for i, rule in enumerate(result) if rule.rule_id == edit.target_id), None)
        if index is None:
            raise ValueError(f"unknown rule_id: {edit.target_id}")
        rule = result[index]
        if rule.locked:
            raise ValueError(f"cannot edit locked rule: {rule.rule_id}")
        if edit.kind == "drop_rule":
            result.pop(index)
        else:
            data = rule.model_dump()
            data["value"] = deepcopy(edit.new_value)
            if edit.new_operator is not None:
                data["operator"] = edit.new_operator
            replacement = CriteriaRule.model_validate(data)
            _validate_rule(replacement)
            result[index] = replacement
    return result
