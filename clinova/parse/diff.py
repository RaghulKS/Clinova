"""Rule diff between two parsed versions (owner: T12). Used by the hit-rate metric and the backtest reveal."""

from __future__ import annotations

from collections.abc import Sequence

from clinova.contracts import CriteriaRule, RuleChange


def diff_rules(old: Sequence[CriteriaRule], new: Sequence[CriteriaRule]) -> list[RuleChange]:
    """Compare two rule sets per ClinicalField and return one RuleChange per changed field:
    ``relaxed`` (more patients pass), ``tightened``, ``removed`` or ``added``. Unchanged fields and
    unparseable rules are omitted. Pure function, no I/O."""
    raise NotImplementedError("T12 implements clinova.parse.diff.diff_rules")
