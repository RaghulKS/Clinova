"""Rule diff between two parsed versions (owner: T12). Used by the hit-rate metric and the backtest reveal."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from clinova.contracts import FIELD_SPECS, ClinicalField, CriteriaRule, Operator, RuleChange


def _words(r: CriteriaRule) -> str:
    return f"{'not ' if r.kind == 'exclusion' else ''}{r.field.value} {r.operator.value}" + ("" if r.value is None else f" {r.value}")


def _cond(r: CriteriaRule, x, order) -> bool:
    op, v = r.operator, r.value
    if op == Operator.present:
        return x is True
    if op == Operator.absent:
        return x is False
    if op == Operator.eq:
        return x == v
    if op == Operator.ne:
        return x != v
    if op in (Operator.in_, Operator.not_in):
        return (x in v) == (op == Operator.in_) if isinstance(v, list) else (x == v) == (op == Operator.in_)
    if order:  # ordinal: compare by position in the vocabulary
        x, v = order.index(x), order.index(v)
    return {Operator.lt: x < v, Operator.le: x <= v, Operator.gt: x > v, Operator.ge: x >= v}[op]


def _grid(field: ClinicalField, rules: list[CriteriaRule]) -> list:
    """Candidate patient values: the whole vocabulary, or every threshold mentioned plus midpoints and the outer sides."""
    spec = FIELD_SPECS[field]
    if spec.kind == "boolean":
        return [True, False]
    if spec.values:
        return list(spec.values)
    pts = sorted({float(x) for r in rules for x in (r.value if isinstance(r.value, list) else [r.value])
                  if isinstance(x, (int, float)) and not isinstance(x, bool)})
    if not pts:
        return []
    mids = [(a + b) / 2 for a, b in zip(pts, pts[1:], strict=False)]
    ints = list(range(int(spec.min), int(spec.max) + 1)) if spec.max is not None and spec.max - spec.min <= 20 else []
    return sorted({*pts, *mids, *ints, pts[0] - 1, pts[-1] + 1})


def _pass_set(field: ClinicalField, rules: list[CriteriaRule], grid: list) -> set:
    order = FIELD_SPECS[field].values if FIELD_SPECS[field].kind == "ordinal" else None
    return {x for x in grid if all(_cond(r, x, order) == (r.kind == "inclusion") for r in rules)}


def diff_rules(old: Sequence[CriteriaRule], new: Sequence[CriteriaRule]) -> list[RuleChange]:
    """Compare two rule sets per ClinicalField and return one RuleChange per changed field:
    ``relaxed`` (more patients pass), ``tightened``, ``removed`` or ``added``. Unchanged fields and
    unparseable rules are omitted. Pure function, no I/O."""
    by: dict[ClinicalField, tuple[list[CriteriaRule], list[CriteriaRule]]] = defaultdict(lambda: ([], []))
    for i, rules in enumerate((old, new)):
        for r in rules:
            if r.parse_status == "ok" and r.field != ClinicalField.other:
                by[r.field][i].append(r)
    changes = []
    for field, (o, n) in by.items():
        ow, nw = "; ".join(map(_words, o)) or None, "; ".join(map(_words, n)) or None
        ids = dict(old_rule_id=o[0].rule_id if o else None, new_rule_id=n[0].rule_id if n else None, old=ow, new=nw)
        if not o or not n:
            changes.append(RuleChange(field=field, change="removed" if o else "added", **ids))
            continue
        grid = _grid(field, o + n)
        a, b = _pass_set(field, o, grid), _pass_set(field, n, grid)
        if a == b:
            continue
        # ponytail: an incomparable change (neither pass-set contains the other) falls back to grid size
        relaxed = b > a or (not b < a and len(b) > len(a))
        changes.append(RuleChange(field=field, change="relaxed" if relaxed else "tightened", **ids))
    return changes
