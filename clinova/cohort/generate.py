"""Virtual cohort (owner: T13). See ARCHITECTURE.md 5.3 and docs/CONTRACTS.md."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from clinova.contracts import Persona, Setting


def generate_cohort(n: int, seed: int, setting: Setting = Setting.any) -> list[Persona]:
    """Sample ``n`` personas (persona dataset reweighted to NSCLC demographics, or the census
    fallback) and give each ClinicalTraits drawn from ``cohort/assumptions.yaml`` conditional on
    demographics and ``setting``. Deterministic for a given (n, seed, setting)."""
    raise NotImplementedError("T13 implements clinova.cohort.generate.generate_cohort")


def cohort_frame(personas: Sequence[Persona]) -> pd.DataFrame:
    """Flatten personas into the cohort dataframe with exactly ``contracts.COHORT_COLUMNS``
    (one row per persona; enum values as plain strings), the input of ``simulate.screen.screen``."""
    raise NotImplementedError("T13 implements clinova.cohort.generate.cohort_frame")


def generate_cohort_df(n: int, seed: int, setting: Setting = Setting.any) -> pd.DataFrame:
    """``cohort_frame(generate_cohort(n, seed, setting))``, possibly faster."""
    raise NotImplementedError("T13 implements clinova.cohort.generate.generate_cohort_df")
