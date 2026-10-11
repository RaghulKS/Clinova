"""Backtest reveal logic (pure, no Streamlit): turn a BacktestResult into timeline milestones and matches.

Every milestone comes from a field of the trial record or a version-history row; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from clinova.contracts import BacktestResult, RuleChange

RELAXING = ("relaxed", "removed")


@dataclass(frozen=True)
class Milestone:
    when: date
    title: str
    detail: str = ""
    kind: str = "plain"  # plain | seen (what Clinova saw) | key (widening, termination)


def _status_words(s: str | None) -> str:
    return (s or "").replace("_", " ").lower()


def milestones(bt: BacktestResult) -> list[Milestone]:
    """Chronological milestones for the reveal timeline."""
    t = bt.trial
    out: list[Milestone] = []
    first = t.first_version_date or t.first_posted
    if first:
        out.append(Milestone(first, "First registered", "The version Clinova reads", "seen"))
    if t.start_date:
        out.append(Milestone(t.start_date, "Study start"))
    has_termination_event = False
    for e in sorted(bt.events, key=lambda e: (e.version_date, e.version_no)):
        if e.field == "eligibility":
            cls = e.change_class or "changed"
            key = e.change_class in ("widened", "mixed")
            out.append(Milestone(e.version_date, f"Eligibility {cls}", f"version {e.version_no}", "key" if key else "plain"))
        elif e.field == "overall_status":
            status = _status_words(e.new_value)
            if (e.new_value or "").upper() in ("TERMINATED", "SUSPENDED", "WITHDRAWN"):
                has_termination_event = True
                out.append(Milestone(e.version_date, status.capitalize(), t.why_stopped or "", "key"))
            else:
                out.append(Milestone(e.version_date, f"Status: {status}", f"version {e.version_no}"))
        elif e.field == "enrollment_count":
            out.append(Milestone(e.version_date, "Target enrollment changed", f"{e.old_value or '?'} to {e.new_value or '?'}"))
    if not has_termination_event and t.status.value in ("TERMINATED", "SUSPENDED", "WITHDRAWN") and t.completion_date:
        out.append(Milestone(t.completion_date, _status_words(t.status.value).capitalize(), t.why_stopped or "", "key"))
    return sorted(out, key=lambda m: m.when)


def has_dated_widening(bt: BacktestResult) -> bool:
    return any(e.field == "eligibility" for e in bt.events)


def widened_changes(bt: BacktestResult) -> list[RuleChange]:
    """Rule changes that let more patients in (relaxed or removed)."""
    return [c for c in bt.changes if c.change in RELAXING]


def flagged_and_widened(bt: BacktestResult, rule_ids: list[str]) -> list[RuleChange]:
    """Widened changes on a rule Clinova pointed at (its flags or the funnel's stall criterion), matched
    by rule id of the first version, or by field."""
    run = bt.run
    fields = set()
    if run is not None:
        by_id = {r.rule_id: r for r in run.protocol.rules}
        fields = {by_id[r].field for r in rule_ids if r in by_id}
    return [c for c in widened_changes(bt) if (c.old_rule_id in rule_ids) or (c.field in fields)]


_OPS = {"ge": ">=", "gt": ">", "le": "<=", "lt": "<", "eq": "=", "ne": "!=", "in": "in", "not_in": "not in"}


def condition_words(cond: str | None) -> str:
    """'crcl_ml_min ge 60' -> 'crcl ml min >= 60'; 'not cardiac_condition present' -> 'no cardiac condition'."""
    if not cond:
        return ""
    words = cond.split()
    neg = words[:1] == ["not"]
    if neg:
        words = words[1:]
    if words and words[-1] in ("present", "absent") and len(words) == 2:
        field = words[0].replace("_", " ")
        has = (words[-1] == "present") != neg
        return f"{field} required" if has else f"no {field}"
    out = " ".join(_OPS.get(w, w.replace("_", " ")) for w in words)
    return f"not ({out})" if neg else out


def enrollment_line(bt: BacktestResult) -> str | None:
    t = bt.trial
    if t.planned_enrollment and t.actual_enrollment is not None:
        share = t.actual_enrollment / t.planned_enrollment
        return f"Enrolled {t.actual_enrollment} of {t.planned_enrollment} planned ({share * 100:.1f}%)".replace(".0%", "%")
    if t.planned_enrollment:
        return f"Planned enrollment {t.planned_enrollment}; actual not reported"
    return None
