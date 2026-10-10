"""Eligibility screen (owner: T13). Pure, vectorized, deterministic. See ARCHITECTURE.md 5.4."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from clinova.contracts import CriteriaRule, Edit, Funnel, ScreenResult


def screen(rules: Sequence[CriteriaRule], cohort: pd.DataFrame, *, trial_id: str = "") -> tuple[np.ndarray, Funnel]:
    """Apply ``rules`` to the cohort dataframe (``contracts.COHORT_COLUMNS``).

    Semantics are in the ``CriteriaRule`` docstring: inclusion must hold, exclusion excludes when it
    holds, missing values pass, unparseable rules are skipped and listed in ``Funnel.skipped_rules``.
    Returns a boolean eligibility mask aligned with ``cohort`` rows and the Funnel (sequential
    stages in rule order + per-rule marginal exclusions). Target: 10,000 rows x 25 rules < 50 ms.
    """
    raise NotImplementedError("T13 implements clinova.simulate.screen.screen")


def screen_results(rules: Sequence[CriteriaRule], cohort: pd.DataFrame) -> list[ScreenResult]:
    """Per-persona detail (which rules each persona fails). Slower than ``screen``; for the UI and tests."""
    raise NotImplementedError("T13 implements clinova.simulate.screen.screen_results")


def apply_edits(rules: Sequence[CriteriaRule], edits: Sequence[Edit]) -> list[CriteriaRule]:
    """Return a new rule list with the rule edits applied (``relax_rule`` sets ``new_value`` and
    optionally ``new_operator``; ``drop_rule`` removes the rule). Visit edits are ignored here
    (the optimizer applies them to visits). Raises ValueError for edits on ``locked`` rules or
    unknown rule_ids. Never mutates the input."""
    raise NotImplementedError("T13 implements clinova.simulate.screen.apply_edits")
