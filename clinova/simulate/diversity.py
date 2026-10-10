"""Diversity index of an eligible cohort (owner: T13)."""

from __future__ import annotations

import pandas as pd


def diversity_index(eligible_df: pd.DataFrame) -> float:
    """Compare the age, sex and race/ethnicity mix of the eligible personas (cohort dataframe rows)
    with the NSCLC reference population. 1.0 = matches the reference; lower = less representative.
    Range [0, 1]; an empty frame returns 0.0."""
    raise NotImplementedError("T13 implements clinova.simulate.diversity.diversity_index")
