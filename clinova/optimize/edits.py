"""Empirical rule relaxations and conservative, pure protocol edits (owner: T23).

There is deliberately no hard-coded clinical threshold ladder. Live observations come
from other registered NSCLC trials' ``criteria_rules``; an injected iterable makes
tests and explicitly labelled fixture runs independent of the database.
"""

from __future__ import annotations

import json
import math
import operator
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from psycopg.rows import dict_row

from clinova.common.db import get_conn
from clinova.contracts import (
    FIELD_SPECS,
    LOCKED_BY_DEFAULT,
    ClinicalField,
    CriteriaRule,
    Edit,
    Operator,
    ParsedProtocol,
    Visit,
)
from clinova.simulate.screen import apply_edits

ObservedRow = Mapping[str, Any] | CriteriaRule
_ORDERED = frozenset({Operator.lt, Operator.le, Operator.gt, Operator.ge})
_COMPARE = {
    Operator.lt: operator.lt,
    Operator.le: operator.le,
    Operator.gt: operator.gt,
    Operator.ge: operator.ge,
    Operator.eq: operator.eq,
    Operator.ne: operator.ne,
}
# Local laboratory / home nursing is allowed only when the parsed visit explicitly
# permits it. Unknown procedures and procedures requiring specialist equipment fail
# closed even if an upstream parser incorrectly sets remote_ok.
_REMOTE_PROCEDURES = frozenset({
    "vitals", "blood_draw", "urine_test", "questionnaire", "adverse_event_review", "phone_call",
})
_OPTIONAL_CONTACT_PROCEDURES = frozenset({"phone_call", "questionnaire"})


def load_observed_rules(
    *, fields: Iterable[ClinicalField] | None = None, exclude_trial_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read real registered NSCLC rule observations, including amended versions.

    Fixture/draft rows are excluded by the join and NCT-id check. The protocol being
    optimized is excluded when supplied. Database failures propagate so callers can
    explicitly select an offline fallback without calling fixture values empirical.
    """
    selected = sorted({str(field) for field in fields}) if fields is not None else None
    if selected == []:
        return []
    sql = """
        SELECT r.nct_id, r.version_no, r.rule_id, r.kind, r.field, r.operator,
               r.value, r.unit, r.raw_text, r.parse_status, r.locked, r.note
        FROM criteria_rules r JOIN trials t ON t.nct_id = r.nct_id
        WHERE r.parse_status = 'ok' AND r.nct_id ~ '^NCT[0-9]{8}$'
          AND (%s::text IS NULL OR r.nct_id <> %s)
          AND (%s::text[] IS NULL OR r.field = ANY(%s))
          AND EXISTS (
              SELECT 1 FROM unnest(t.conditions) AS condition
              WHERE lower(condition) ~
              '(^|[^a-z])nsclc([^a-z]|$)|non[- ]?small[- ]?cell.*lung|lung.*non[- ]?small[- ]?cell'
          )
        ORDER BY r.nct_id, r.version_no, r.rule_id
    """
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (exclude_trial_id, exclude_trial_id, selected, selected))
        return [dict(row) for row in cur.fetchall()]


def _validate_rule(rule: CriteriaRule) -> None:
    spec = FIELD_SPECS[rule.field]
    op, value = rule.operator, rule.value
    if rule.parse_status != "ok" or spec.kind == "free":
        raise ValueError(f"cannot relax unparseable rule: {rule.rule_id}")
    if rule.unit not in (None, spec.unit):
        raise ValueError(f"non-canonical unit for rule: {rule.rule_id}")
    if op in (Operator.present, Operator.absent):
        if spec.kind != "boolean" or value is not None:
            raise ValueError("present/absent require a boolean field and value None")
        return
    if op in (Operator.in_, Operator.not_in):
        if not isinstance(value, list):
            raise ValueError("in/not_in require a list")
        values = value
    else:
        if value is None or isinstance(value, list):
            raise ValueError("comparison requires a scalar value")
        values = [value]
    if op in _ORDERED and spec.kind not in ("numeric", "ordinal"):
        raise ValueError("ordered comparison requires a numeric or ordinal field")
    for item in values:
        if spec.kind == "numeric":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise ValueError("numeric rule requires finite numeric values")
            if (spec.min is not None and item < spec.min) or (spec.max is not None and item > spec.max):
                raise ValueError("numeric value outside the field's plausible range")
        elif spec.kind in ("ordinal", "categorical") and item not in (spec.values or ()):
            raise ValueError("rule value outside the controlled vocabulary")
        elif spec.kind == "boolean" and not isinstance(item, bool):
            raise ValueError("boolean rule requires boolean values")


def _allows(rule: CriteriaRule, value: Any) -> bool:
    if rule.operator in (Operator.present, Operator.absent):
        satisfied = value == (rule.operator == Operator.present)
    elif rule.operator in (Operator.in_, Operator.not_in):
        satisfied = value in rule.value
        if rule.operator == Operator.not_in:
            satisfied = not satisfied
    else:
        threshold = rule.value
        if FIELD_SPECS[rule.field].kind == "ordinal" and rule.operator in _ORDERED:
            vocabulary = FIELD_SPECS[rule.field].values
            value, threshold = vocabulary.index(value), vocabulary.index(threshold)
        satisfied = _COMPARE[rule.operator](value, threshold)
    return bool(satisfied) if rule.kind == "inclusion" else not bool(satisfied)


def _probes(old: CriteriaRule, new: CriteriaRule) -> list[Any]:
    spec = FIELD_SPECS[old.field]
    if spec.kind == "boolean":
        return [False, True]
    if spec.kind in ("categorical", "ordinal"):
        return list(spec.values or ())
    # Numeric predicates change only at their thresholds. Testing every boundary
    # and each open interval between boundaries proves set inclusion, rather than
    # merely finding a relaxation on a sampled cohort.
    boundaries = {float(spec.min), float(spec.max)}
    for rule in (old, new):
        values = rule.value if isinstance(rule.value, list) else [rule.value]
        boundaries.update(float(value) for value in values)
    points = sorted(boundaries)
    return [*points, *((a + b) / 2 for a, b in zip(points, points[1:], strict=False))]


def _strict_relaxation(old: CriteriaRule, new: CriteriaRule) -> bool:
    if old.field != new.field or old.kind != new.kind:
        return False
    _validate_rule(old)
    _validate_rule(new)
    before, after = zip(*((_allows(old, value), _allows(new, value)) for value in _probes(old, new)),
                        strict=True)
    return all(not a or b for a, b in zip(before, after, strict=True)) and before != after


def _observation(row: ObservedRow, index: int) -> CriteriaRule | None:
    if isinstance(row, CriteriaRule):
        data = row.model_dump()
    else:
        data = {key: value for key, value in row.items() if key in CriteriaRule.model_fields}
        data.setdefault("rule_id", f"observed-{index}")
        data.setdefault("raw_text", "Injected observation (not a measured result)")
    try:
        observed = CriteriaRule.model_validate(data)
        _validate_rule(observed)
        if isinstance(observed.value, list):
            # Membership order carries no meaning; canonicalize without touching
            # an injected CriteriaRule or its mutable value list.
            observed.value = sorted(set(observed.value), key=lambda value: json.dumps(value))
        return observed
    except (TypeError, ValueError):
        return None


def visit_can_be_remote(visit: Visit) -> bool:
    """Whether explicit remote permission and all procedure tags permit no site travel."""
    return bool(visit.remote_ok and not visit.invasive and visit.procedures
                and set(visit.procedures) <= _REMOTE_PROCEDURES)


def _can_cut(visit: Visit) -> bool:
    # Visit has no optional flag; require explicit optional wording and no treatment,
    # safety review, invasive procedure or diagnostic assessment.
    required = re.search(r"\b(?:required|mandatory|non[\s-]*optional|not\s+optional)\b",
                         visit.name, re.IGNORECASE)
    return bool(re.search(r"\boptional\b", visit.name, re.IGNORECASE) and not required and not visit.invasive
                and visit.procedures and set(visit.procedures) <= _OPTIONAL_CONTACT_PROCEDURES)


def _validate_visits(visits: Sequence[Visit]) -> None:
    ids = [visit.visit_no for visit in visits]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate visit_no")
    if any(a.day > b.day for a, b in zip(visits, visits[1:], strict=False)):
        raise ValueError("visits must be in chronological order")


def _candidate_key(rule: CriteriaRule) -> tuple[Any, ...]:
    if FIELD_SPECS[rule.field].kind != "numeric":
        return tuple(_allows(rule, value) for value in _probes(rule, rule))
    return (rule.operator, tuple(rule.value) if isinstance(rule.value, list) else rule.value)


def build_edit_space(
    protocol: ParsedProtocol, *, observed_rows: Iterable[ObservedRow] | None = None,
) -> list[Edit]:
    """Return deterministic admissible single edits, with no invented thresholds.

    Every relaxation uses an observed rule on the same field and with the same
    inclusion/exclusion semantics. All less restrictive observed steps are available,
    ordered from the closest numerical threshold. Locked/default-locked and
    unparseable rules have no candidates. An explicit empty iterable disables the
    database and yields rule drops and feasible visit edits only. Injected synthetic
    observations must be labelled as fixtures by the caller, never empirical results.
    """
    _validate_visits(protocol.visits)
    editable = [rule for rule in protocol.rules if not rule.locked
                and rule.field not in LOCKED_BY_DEFAULT and rule.parse_status == "ok"]
    rows = observed_rows if observed_rows is not None else load_observed_rules(
        fields={rule.field for rule in editable}, exclude_trial_id=protocol.trial_id,
    )
    observations = []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("nct_id") == protocol.trial_id:
            continue
        observation = _observation(row, index)
        if observation is not None:
            observations.append(observation)
    # Equivalent finite-domain predicates can have different encodings (e.g.
    # brain_mets <= treated_stable and membership in [none, treated_stable]).
    # Choose their representative independently of injected/database row order.
    observations.sort(key=lambda observed: json.dumps(
        [observed.field, observed.kind, observed.operator, observed.value], sort_keys=True,
    ))
    edits = []
    for rule in editable:
        _validate_rule(rule)
        candidates = {}
        for observed in observations:
            try:
                if not _strict_relaxation(rule, observed):
                    continue
            except (TypeError, ValueError):
                continue
            candidates.setdefault(_candidate_key(observed), observed)
        def distance(candidate: CriteriaRule, original: CriteriaRule = rule) -> tuple[float, str]:
            numeric = isinstance(original.value, (int, float)) and not isinstance(original.value, bool)
            scalar = isinstance(candidate.value, (int, float)) and not isinstance(candidate.value, bool)
            step = abs(float(candidate.value) - float(original.value)) if numeric and scalar else 0.0
            return step, json.dumps([candidate.operator, candidate.value], sort_keys=True)
        for candidate in sorted(candidates.values(), key=distance):
            edits.append(Edit(kind="relax_rule", target_id=rule.rule_id, new_value=candidate.value,
                              new_operator=candidate.operator if candidate.operator != rule.operator else None))
        edits.append(Edit(kind="drop_rule", target_id=rule.rule_id))
    for index, visit in enumerate(protocol.visits):
        target_id = str(visit.visit_no)
        if _can_cut(visit):
            edits.append(Edit(kind="cut_visit", target_id=target_id))
        if index + 1 < len(protocol.visits) and visit.day == protocol.visits[index + 1].day:
            edits.append(Edit(kind="merge_visits", target_id=target_id))
        if visit.requires_travel and visit_can_be_remote(visit):
            edits.append(Edit(kind="make_remote", target_id=target_id))
    return edits


def apply_protocol_edits(protocol: ParsedProtocol, edits: Sequence[Edit]) -> ParsedProtocol:
    """Apply a batch to a deep copy, validating locks, relaxations and visit feasibility.

    Visit ids refer to the original schedule throughout the batch. Overlapping edits
    are rejected (a merge consumes its next visit); the completed schedule is then
    renumbered from one. Merges preserve all procedure occurrences and sum durations.
    Remote edits preserve duration estimates because no time saving is established.
    Invalid batches never mutate the source protocol or the caller's edit values.
    """
    _validate_visits(protocol.visits)
    rules = {rule.rule_id: rule for rule in protocol.rules}
    visits = {str(visit.visit_no): visit for visit in protocol.visits}
    visit_positions = {str(visit.visit_no): index for index, visit in enumerate(protocol.visits)}
    seen_rules, consumed_visits = set(), set()
    for edit in edits:
        if edit.kind in ("relax_rule", "drop_rule"):
            if edit.target_id not in rules:
                raise ValueError(f"unknown rule_id: {edit.target_id}")
            rule = rules[edit.target_id]
            if rule.locked or rule.field in LOCKED_BY_DEFAULT:
                raise ValueError(f"cannot edit locked rule: {rule.rule_id}")
            if edit.target_id in seen_rules:
                raise ValueError(f"multiple edits for rule: {edit.target_id}")
            seen_rules.add(edit.target_id)
            if edit.kind == "relax_rule":
                data = rule.model_dump()
                data.update(value=edit.new_value, operator=edit.new_operator or rule.operator)
                if not _strict_relaxation(rule, CriteriaRule.model_validate(data)):
                    raise ValueError(f"edit is not a strict relaxation: {rule.rule_id}")
            continue
        if edit.target_id not in visits:
            raise ValueError(f"unknown visit_no: {edit.target_id}")
        visit = visits[edit.target_id]
        affected = {edit.target_id}
        if edit.kind == "cut_visit" and not _can_cut(visit):
            raise ValueError(f"cannot cut required or clinically substantive visit: {edit.target_id}")
        if edit.kind == "make_remote" and (not visit.requires_travel or not visit_can_be_remote(visit)):
            raise ValueError(f"visit cannot be made remote: {edit.target_id}")
        if edit.kind == "merge_visits":
            index = visit_positions[edit.target_id]
            if index + 1 == len(protocol.visits):
                raise ValueError("cannot merge the last visit")
            following = protocol.visits[index + 1]
            if visit.day != following.day:
                raise ValueError("cannot move procedures across study days without timing windows")
            affected.add(str(following.visit_no))
        if consumed_visits & affected:
            raise ValueError("overlapping visit edits")
        consumed_visits.update(affected)
    changed_rules = apply_edits(protocol.rules, edits)
    changed_visits = [visit.model_copy(deep=True) for visit in protocol.visits]
    for edit in edits:
        if edit.kind in ("relax_rule", "drop_rule"):
            continue
        index = next(i for i, visit in enumerate(changed_visits) if str(visit.visit_no) == edit.target_id)
        visit = changed_visits[index]
        if edit.kind == "cut_visit":
            changed_visits.pop(index)
        elif edit.kind == "make_remote":
            changed_visits[index] = visit.model_copy(update={"requires_travel": False})
        else:
            following = changed_visits[index + 1]
            changed_visits[index:index + 2] = [Visit(
                visit_no=visit.visit_no, name=f"{visit.name} + {following.name}", day=visit.day,
                procedures=[*visit.procedures, *following.procedures],
                est_hours=visit.est_hours + following.est_hours,
                requires_travel=visit.requires_travel or following.requires_travel,
                invasive=visit.invasive or following.invasive,
                remote_ok=visit_can_be_remote(visit) and visit_can_be_remote(following),
                estimated=visit.estimated or following.estimated,
            )]
    for number, visit in enumerate(changed_visits, 1):
        visit.visit_no = number
    data = protocol.model_dump()
    data.update(rules=[rule.model_dump() for rule in changed_rules],
                visits=[visit.model_dump() for visit in changed_visits])
    return ParsedProtocol.model_validate(data)
