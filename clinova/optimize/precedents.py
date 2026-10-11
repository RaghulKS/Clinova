"""Stored trial evidence for optimizer edits; never substitutes illustrative fixtures.

Similarity uses first-version pgvector embeddings. Completed precedents use only
first-version parsed rules. The widening report requires an eligibility event and
parsed rules for its adjacent versions, so an unrelated amendment is not credited
to the queried field. No parser or language-model calls are made here.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from datetime import date, datetime
from statistics import median
from typing import Any

from pydantic import Field, ValidationError

from clinova.common.db import get_conn
from clinova.contracts import (
    FIELD_SPECS,
    ClinicalField,
    Contract,
    CriteriaRule,
    Edit,
    Operator,
    ParsedProtocol,
    Precedent,
    RuleValue,
)

NearestFn = Callable[..., Sequence[tuple[str, float]]]
_NSCLC = re.compile(
    r"\bnsclc\b|\bnon[\s-]*small[\s-]*cell\b.*\blung\b|\blung\b.*\bnon[\s-]*small[\s-]*cell\b",
    re.IGNORECASE,
)
_TRIAL_COLUMNS = (
    "nct_id", "title", "status", "conditions", "start_date", "planned_enrollment", "actual_enrollment",
    "history_source",
)
_RULE_COLUMNS = (
    "nct_id", "version_no", "rule_id", "kind", "field", "operator", "value", "unit", "raw_text",
    "parse_status", "locked", "note",
)
_EVENT_COLUMNS = ("nct_id", "version_no", "version_date", "field", "change_class", "old_value", "new_value")
_RULE_NAMES = {"crcl_ml_min": "CrCl", "ecog": "ECOG", "brain_mets": "brain metastases"}
_SYMBOLS = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "=", "ne": "!="}


class WideningReport(Contract):
    """Observed widening among similar NSCLC trials with the exact original rule.

    ``widened_share`` is the share with verified field-specific events in the stored
    data, not an estimate of unobserved amendments. Missing evidence is explicit.
    Months are elapsed days / (365.25 / 12), using each trial's earliest qualifying
    event strictly after its start date.
    """

    trial_id: str
    field: ClinicalField
    kind: str
    operator: Operator
    value: RuleValue = None
    comparable_n: int = Field(ge=0)
    original_threshold_n: int = Field(ge=0)
    widened_n: int = Field(ge=0)
    widened_share: float | None = Field(None, ge=0, le=1)
    median_months_after_start: float | None = Field(None, ge=0)
    unresolved_history_n: int = Field(0, ge=0)
    denominator_nct_ids: list[str] = Field(default_factory=list)
    widened_nct_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _rows(cursor: Any, columns: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(row) if isinstance(row, Mapping) else dict(zip(columns, row, strict=True))
            for row in cursor.fetchall()]


def _is_nsclc(row: Mapping[str, Any]) -> bool:
    conditions = row.get("conditions") or ()
    if isinstance(conditions, str):
        conditions = [conditions]
    return any(_NSCLC.search(term) for term in [row.get("title") or "", *conditions])


def _check_options(comparable_k: int, max_distance: float | None) -> None:
    if comparable_k < 1:
        raise ValueError("comparable_k must be positive")
    if max_distance is not None and (not math.isfinite(max_distance) or max_distance < 0):
        raise ValueError("max_distance must be finite and nonnegative")


def _nearest(conn: Any, trial_id: str, k: int, *, completed_only: bool = False) -> Sequence[tuple[str, float]]:
    # Filter NSCLC and, for citations, outcome before LIMIT: unrelated or ongoing
    # trials must not displace the closest qualifying trial evidence.
    outcome_filter = " AND t.status = 'COMPLETED'" if completed_only else ""
    rows = conn.execute(
        "SELECT e.nct_id, e.embedding <=> q.embedding AS distance "
        "FROM trial_embeddings e JOIN trials t ON t.nct_id = e.nct_id "
        "CROSS JOIN (SELECT embedding FROM trial_embeddings WHERE nct_id = %s) q "
        "WHERE e.nct_id <> %s " + outcome_filter + " AND EXISTS ("
        "SELECT 1 FROM unnest(COALESCE(t.conditions, '{}'::text[]) || ARRAY[COALESCE(t.title, '')]) term "
        "WHERE term ~* '\\mnsclc\\M|\\mnon[[:space:]-]*small[[:space:]-]*cell\\M.*\\mlung\\M|"
        "\\mlung\\M.*\\mnon[[:space:]-]*small[[:space:]-]*cell\\M') "
        "ORDER BY e.embedding <=> q.embedding, e.nct_id LIMIT %s",
        (trial_id, trial_id, k),
    ).fetchall()
    return [(r["nct_id"], r["distance"]) if isinstance(r, Mapping) else (r[0], r[1]) for r in rows]


def _evidence(
    protocol: ParsedProtocol, *, comparable_k: int, max_distance: float | None,
    conn: Any, nearest_fn: NearestFn | None, history: bool,
) -> tuple[dict[str, dict], dict[str, float], dict[str, dict[int, list[CriteriaRule]]], list[dict]]:
    candidates = (_nearest(conn, protocol.trial_id, comparable_k, completed_only=not history) if nearest_fn is None else
                  nearest_fn(protocol.trial_id, comparable_k, exclude=(protocol.trial_id,)))
    distances = {}
    for nct_id, distance in candidates:
        if nct_id == protocol.trial_id or distance is None:
            continue
        d = float(distance)
        if math.isfinite(d) and d >= 0 and (max_distance is None or d <= max_distance):
            distances[nct_id] = min(d, distances.get(nct_id, d))
    # Also constrain injected neighbor functions that return more than k rows.
    distances = dict(sorted(distances.items(), key=lambda item: (item[1], item[0]))[:comparable_k])
    if not distances:
        return {}, {}, {}, []
    ids = list(distances)
    trials = {
        row["nct_id"]: row for row in _rows(conn.execute(
            f"SELECT {', '.join(_TRIAL_COLUMNS)} FROM trials WHERE nct_id = ANY(%s)", (ids,),
        ), _TRIAL_COLUMNS) if _is_nsclc(row)
    }
    distances = {nct_id: distance for nct_id, distance in distances.items() if nct_id in trials}
    if not trials:
        return {}, {}, {}, []
    ids = list(trials)
    sql = f"SELECT {', '.join(_RULE_COLUMNS)} FROM criteria_rules WHERE nct_id = ANY(%s)"
    if not history:
        sql += " AND version_no = 0"
    rules: dict[str, dict[int, list[CriteriaRule]]] = defaultdict(lambda: defaultdict(list))
    for row in _rows(conn.execute(sql + " ORDER BY nct_id, version_no, rule_id", (ids,)), _RULE_COLUMNS):
        nct_id, version_no = row.pop("nct_id"), row.pop("version_no")
        try:
            parsed = CriteriaRule.model_validate(row)
        except (ValidationError, TypeError):
            # Keep an unknown rule so a corrupt row cannot make a version look
            # unconstrained (and therefore falsely broad).
            parsed = CriteriaRule(rule_id=str(row.get("rule_id", "unknown")), kind="inclusion",
                                  field=ClinicalField.other, operator=Operator.eq, raw_text="",
                                  parse_status="unparseable")
        rules[nct_id][version_no].append(parsed)
    events = []
    if history:
        events = _rows(conn.execute(
            f"SELECT {', '.join(_EVENT_COLUMNS)} FROM trial_versions WHERE nct_id = ANY(%s) "
            "AND field = 'eligibility' AND change_class IN ('widened', 'mixed') "
            "ORDER BY nct_id, version_date, version_no", (ids,),
        ), _EVENT_COLUMNS)
    return trials, distances, dict(rules), events


def _valid(rule: CriteriaRule) -> bool:
    if rule.parse_status != "ok" or rule.field == ClinicalField.other:
        return False
    spec = FIELD_SPECS[rule.field]
    op, value = rule.operator, rule.value
    if rule.unit is not None and rule.unit != spec.unit:
        return False
    if op in (Operator.present, Operator.absent):
        return spec.kind == "boolean" and value is None
    values = value if isinstance(value, list) else [value]
    if (op in (Operator.in_, Operator.not_in)) != isinstance(value, list):
        return False
    if op in (Operator.lt, Operator.le, Operator.gt, Operator.ge) and spec.kind not in ("numeric", "ordinal"):
        return False
    if spec.kind == "numeric":
        valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    and (spec.min is None or v >= spec.min) and (spec.max is None or v <= spec.max)
                    for v in values)
    elif spec.kind == "boolean":
        valid = all(isinstance(v, bool) for v in values)
    else:
        valid = all(v in (spec.values or ()) for v in values)
    return valid


def _field_rules(rules: Sequence[CriteriaRule], field: ClinicalField) -> list[CriteriaRule] | None:
    matching = [rule for rule in rules if rule.field == field]
    # Unknown fragments may hide an additional constraint on this field.
    if any(rule.parse_status != "ok" or rule.field == ClinicalField.other for rule in rules):
        return None
    return matching if all(_valid(rule) for rule in matching) else None


def _grid(field: ClinicalField, rule_sets: Sequence[Sequence[CriteriaRule]]) -> list[Any]:
    spec = FIELD_SPECS[field]
    if spec.kind == "boolean":
        return [False, True]
    if spec.values is not None:
        return list(spec.values)
    values = {float(v) for rules in rule_sets for rule in rules
              for v in (rule.value if isinstance(rule.value, list) else [rule.value])}
    # Each numeric predicate is constant between adjacent thresholds. Boundary
    # values and one midpoint per interval make containment exact on the domain.
    values.update([float(spec.min), float(spec.max)])
    points = sorted(values)
    return sorted({*points, *((a + b) / 2 for a, b in zip(points, points[1:], strict=False))})


def _passes(rule: CriteriaRule, value: Any) -> bool:
    op, target = rule.operator, rule.value
    if op in (Operator.present, Operator.absent):
        satisfies = value is (op == Operator.present)
    elif op in (Operator.in_, Operator.not_in):
        satisfies = (value in target) == (op == Operator.in_)
    elif op == Operator.eq:
        satisfies = value == target
    elif op == Operator.ne:
        satisfies = value != target
    else:
        spec = FIELD_SPECS[rule.field]
        if spec.kind == "ordinal":
            value, target = spec.values.index(value), spec.values.index(target)
        if op == Operator.lt:
            satisfies = value < target
        elif op == Operator.le:
            satisfies = value <= target
        elif op == Operator.gt:
            satisfies = value > target
        else:
            satisfies = value >= target
    return satisfies == (rule.kind == "inclusion")


def _pass_set(rules: Sequence[CriteriaRule], grid: Sequence[Any]) -> set[int]:
    return {i for i, value in enumerate(grid) if all(_passes(rule, value) for rule in rules)}


def _same_threshold(a: CriteriaRule, b: CriteriaRule) -> bool:
    # Set order does not change an in/not_in condition.
    av, bv = a.value, b.value
    if a.operator in (Operator.in_, Operator.not_in) and isinstance(av, list) and isinstance(bv, list):
        av, bv = set(av), set(bv)
    return (a.field, a.kind, a.operator, av) == (b.field, b.kind, b.operator, bv)


def _description(rule: CriteriaRule) -> str:
    name = _RULE_NAMES.get(rule.field, rule.field.value.replace("_", " "))
    prefix = "excluded " if rule.kind == "exclusion" else "required "
    op = rule.operator.value
    value = ", ".join(map(str, rule.value)) if isinstance(rule.value, list) else str(rule.value)
    if op in ("present", "absent"):
        condition = name if op == "present" else f"no {name}"
    else:
        condition = f"{name} {_SYMBOLS.get(op, op)} {value}"
    unit = f" {rule.unit}" if rule.unit and rule.unit not in ("score", "lines") else ""
    return prefix + condition + unit


def find_precedents(
    protocol: ParsedProtocol, edits: Sequence[Edit], *, k: int = 3, comparable_k: int = 100,
    max_distance: float | None = None, conn: Any = None, nearest_fn: NearestFn | None = None,
) -> list[Precedent]:
    """Closest completed NSCLC trials whose first rules support a suggested relaxation.

    A cited trial's complete parsed field restriction must strictly admit more
    patients than the original and admit at least the edited restriction. Titles,
    status, enrollment and thresholds come from the DB; absent evidence returns
    an empty list. Dropped rules and visit edits have no threshold precedent here.
    ``conn`` is borrowed without closing it; ``nearest_fn`` enables offline tests.
    """
    _check_options(comparable_k, max_distance)
    if k < 0:
        raise ValueError("k must be nonnegative")
    original_by_id = {rule.rule_id: rule for rule in protocol.rules}
    targets = []
    for edit in edits:
        original = original_by_id.get(edit.target_id)
        if edit.kind != "relax_rule" or original is None or not _valid(original):
            continue
        edited = original.model_copy(update={"operator": edit.new_operator or original.operator,
                                             "value": edit.new_value})
        baseline = _field_rules(protocol.rules, original.field)
        if not _valid(edited) or baseline is None:
            continue
        proposed = [edited if rule.rule_id == original.rule_id else rule for rule in baseline]
        grid = _grid(original.field, (baseline, proposed))
        if _pass_set(proposed, grid) > _pass_set(baseline, grid):
            targets.append((original.field, baseline, proposed))
    if not targets or k == 0:
        return []
    with (get_conn() if conn is None else nullcontext(conn)) as connection:
        trials, distances, versions, _ = _evidence(
            protocol, comparable_k=comparable_k, max_distance=max_distance,
            conn=connection, nearest_fn=nearest_fn, history=False,
        )
    result = []
    for nct_id in distances:
        trial = trials[nct_id]
        if trial.get("status") != "COMPLETED":
            continue
        first = versions.get(nct_id, {}).get(0)
        if not first:
            continue
        details = []
        for field, baseline, proposed in targets:
            observed = _field_rules(first, field)
            if not observed:
                continue
            grid = _grid(field, (baseline, proposed, observed))
            old_set, new_set, seen_set = (_pass_set(rules, grid) for rules in (baseline, proposed, observed))
            if seen_set > old_set and seen_set >= new_set:
                detail = "; ".join(_description(rule) for rule in observed)
                if detail not in details:
                    details.append(detail)
        if not details:
            continue
        outcome = "COMPLETED"
        planned, actual = trial.get("planned_enrollment"), trial.get("actual_enrollment")
        if planned is not None and planned > 0 and actual is not None:
            outcome += f" with {actual / planned:.0%} of planned enrollment ({actual}/{planned})"
        result.append(Precedent(nct_id=nct_id, title=trial.get("title") or nct_id, distance=distances[nct_id],
                                detail=f"First registered version {'; '.join(details)}; {outcome}."))
        if len(result) == k:
            break
    return result


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _snapshot_matches(text: Any, rules: Sequence[CriteriaRule]) -> bool:
    if not text or not rules:
        return False
    normalized = re.sub(r"\s+", " ", str(text)).strip().casefold()
    return all(rule.raw_text and re.sub(r"\s+", " ", rule.raw_text).strip().casefold() in normalized
               for rule in rules)


def widening_report(
    protocol: ParsedProtocol, rule: CriteriaRule, *, comparable_k: int = 100,
    max_distance: float | None = None, conn: Any = None, nearest_fn: NearestFn | None = None,
) -> WideningReport:
    """Share with verified later widening and median months from trial start.

    The denominator is unique comparable NSCLC trials with an exact matching
    first-version rule, regardless of outcome. A numerator trial needs a widened
    or mixed eligibility event strictly after start and strict field pass-set
    expansion versus both its first and immediately preceding version. Both event
    snapshots must contain the corresponding parsed field fragments. Missing
    adjacent parsed versions and removed fields cannot establish a threshold
    widening: amended parser rows may describe a current snapshot rather than the
    event's version. Only each trial's earliest
    qualifying event contributes to the median. Use ``model_dump(mode='json')``
    for a CLI-ready report.
    """
    _check_options(comparable_k, max_distance)
    if not _valid(rule):
        raise ValueError("widening_report requires a valid parseable rule")
    with (get_conn() if conn is None else nullcontext(conn)) as connection:
        trials, _, versions, events = _evidence(
            protocol, comparable_k=comparable_k, max_distance=max_distance,
            conn=connection, nearest_fn=nearest_fn, history=True,
        )
    denominator = {}
    for nct_id in trials:
        first = versions.get(nct_id, {}).get(0)
        if first and any(_valid(other) and _same_threshold(rule, other) for other in first):
            denominator[nct_id] = first
    earliest: dict[str, date] = {}
    # An absent eligibility event in an incomplete history is not evidence that
    # the rule stayed unchanged. Preserve the full observed-share denominator,
    # while reporting how many non-widened trials have unresolved history.
    unresolved = {nct_id for nct_id in denominator
                  if trials[nct_id].get("history_source") != "versions"
                  or _as_date(trials[nct_id].get("start_date")) is None}
    for event in events:
        nct_id = event["nct_id"]
        if nct_id not in denominator or event.get("field") != "eligibility" or event.get("change_class") not in ("widened", "mixed"):
            continue
        start, when = _as_date(trials[nct_id].get("start_date")), _as_date(event.get("version_date"))
        version = event["version_no"]
        if start is None or when is None:
            unresolved.add(nct_id)
            continue
        if version <= 0 or when <= start:
            continue
        parsed_versions = versions.get(nct_id, {})
        before, after = parsed_versions.get(version - 1), parsed_versions.get(version)
        if not before or not after:
            unresolved.add(nct_id)
            continue
        field_sets = [_field_rules(rules, rule.field) for rules in (denominator[nct_id], before, after)]
        if any(rules is None for rules in field_sets):
            unresolved.add(nct_id)
            continue
        first_rules, old_rules, new_rules = field_sets
        if not _snapshot_matches(event.get("old_value"), old_rules) or not _snapshot_matches(event.get("new_value"), new_rules):
            unresolved.add(nct_id)
            continue
        grid = _grid(rule.field, field_sets)
        first_set, old_set, new_set = (_pass_set(rules, grid) for rules in field_sets)
        if new_set > first_set and new_set > old_set:
            earliest[nct_id] = min(when, earliest.get(nct_id, when))
    months = [(when - _as_date(trials[nct_id]["start_date"])).days / (365.25 / 12)
              for nct_id, when in earliest.items()]
    unresolved.difference_update(earliest)
    n = len(denominator)
    notes = ["Share counts field-specific widening verified in stored adjacent parsed versions; missing history is not evidence of no amendment."]
    if unresolved:
        notes.append("Some trials have incomplete histories or lack dates, matching snapshots, or adjacent parsed field evidence; missing evidence was not counted as widening.")
    if not n:
        notes.append("No comparable trials have the exact original first-version threshold; share and median are unavailable.")
    return WideningReport(
        trial_id=protocol.trial_id, field=rule.field, kind=rule.kind, operator=rule.operator, value=rule.value,
        comparable_n=len(trials), original_threshold_n=n, widened_n=len(earliest),
        widened_share=len(earliest) / n if n else None,
        median_months_after_start=median(months) if months else None, unresolved_history_n=len(unresolved),
        denominator_nct_ids=sorted(denominator), widened_nct_ids=sorted(earliest), notes=notes,
    )
