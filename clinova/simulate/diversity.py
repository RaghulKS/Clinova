"""Diversity index of an eligible cohort (owner: T13)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


@lru_cache(maxsize=1)
def _reference():
    path = Path(__file__).resolve().parents[1] / "cohort" / "assumptions.yaml"
    demographics = yaml.safe_load(path.read_text(encoding="utf-8"))["demographics"]
    bands = demographics["age"]["bands"]
    age = {str(i): band["p"] for i, band in enumerate(bands)}
    sex = demographics["sex"]["probs"]
    # The source records Hispanic ethnicity independently of race; Persona uses one
    # exclusive category. Scale non-Hispanic races to preserve that independent model.
    hispanic = demographics["hispanic_ethnicity"]["p"]
    race = {key: probability * (1 - hispanic)
            for key, probability in demographics["race"]["probs"].items()}
    race["hispanic"] = hispanic
    for probabilities in (age, sex, race):
        if not np.isclose(sum(probabilities.values()), 1) or any(p < 0 for p in probabilities.values()):
            raise ValueError("NSCLC reference probabilities must be nonnegative and sum to one")
    return bands, age, sex, race


def _total_variation(values: pd.Series, reference: dict[str, float]) -> float:
    # Missing/unknown values form unmatched mass rather than disappearing from the
    # denominator: incomplete demographic data cannot claim a perfect match.
    observed = values.astype(object).fillna("__unknown__").value_counts(normalize=True).to_dict()
    keys = reference.keys() | observed.keys()
    return 0.5 * sum(abs(observed.get(key, 0) - reference.get(key, 0)) for key in keys)


def diversity_index(eligible_df: pd.DataFrame) -> float:
    """Compare the age, sex and race/ethnicity mix of the eligible personas (cohort dataframe rows)
    with the NSCLC reference population. 1.0 = matches the reference; lower = less representative.
    Range [0, 1]; an empty frame returns 0.0. The score is one minus the mean total
    variation distance of the three demographic marginals (equal weight). The
    reference is read from ``cohort/assumptions.yaml``; it is not a uniform mix.
    Age bands are [low, high). The source's other/multiple race group combines
    native_american, pacific_islander, multiracial and other. Missing/out-of-range
    demographics count as unmatched mass. This measures marginal representation,
    not clinical appropriateness or representation within demographic intersections.
    """
    if eligible_df.empty:
        return 0.0
    required = {"age", "sex", "race_ethnicity"}
    if missing := required - set(eligible_df.columns):
        raise ValueError(f"cohort is missing demographic columns: {sorted(missing)}")
    bands, age_reference, sex_reference, race_reference = _reference()
    ages = pd.to_numeric(eligible_df["age"], errors="coerce")
    age_band = pd.Series("__unknown__", index=eligible_df.index)
    for i, band in enumerate(bands):
        low, high = band["range"]
        age_band.loc[(ages >= low) & (ages < high)] = str(i)
    races = eligible_df["race_ethnicity"].replace({
        "native_american": "other_or_multiple", "pacific_islander": "other_or_multiple",
        "multiracial": "other_or_multiple", "other": "other_or_multiple",
    })
    distances = (
        _total_variation(age_band, age_reference),
        _total_variation(eligible_df["sex"], sex_reference),
        _total_variation(races, race_reference),
    )
    return float(np.clip(1 - np.mean(distances), 0, 1))
