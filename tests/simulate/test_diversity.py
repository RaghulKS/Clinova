"""Diversity checks against the documented NSCLC demographic references."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from clinova.simulate.diversity import diversity_index


def reference_frame():
    # Exact integer marginals at n=100,000 exercise the public YAML reference,
    # independently of the module's normalization and distance helpers.
    path = Path(__file__).resolve().parents[2] / "clinova/cohort/assumptions.yaml"
    demographics = yaml.safe_load(path.read_text())["demographics"]
    n = 100_000
    age = [sum(band["range"]) / 2 for band in demographics["age"]["bands"]
           for _ in range(round(n * band["p"]))]
    sex = [value for value, p in demographics["sex"]["probs"].items() for _ in range(round(n * p))]
    hispanic = demographics["hispanic_ethnicity"]["p"]
    race = ["other" if value == "other_or_multiple" else value
            for value, p in demographics["race"]["probs"].items()
            for _ in range(round(n * p * (1 - hispanic)))]
    race += ["hispanic"] * round(n * hispanic)
    return pd.DataFrame({"age": age, "sex": sex, "race_ethnicity": race})


def test_reference_marginals_match_exactly():
    frame = reference_frame()
    assert diversity_index(frame) == pytest.approx(1.0, abs=1e-12)
    assert diversity_index(frame.sample(frac=1, random_state=4)) == pytest.approx(1.0, abs=1e-12)


def test_reference_incorrect_age_sex_and_race_reduce_score():
    frame = reference_frame()
    for column, value in [("age", 40), ("sex", "male"), ("race_ethnicity", "white")]:
        changed = frame.copy()
        changed[column] = value
        assert 0 <= diversity_index(changed) < 1


def test_single_demographic_formula():
    frame = pd.DataFrame({"age": [70], "sex": ["female"], "race_ethnicity": ["white"]})
    # Each point mass has TV = 1 - reference probability at the occupied category.
    expected = 1 - ((1 - .367) + (1 - .464) + (1 - .773 * .94)) / 3
    assert diversity_index(frame) == pytest.approx(expected)


def test_empty_frame_returns_zero():
    assert diversity_index(pd.DataFrame()) == 0


def test_missing_and_unknown_are_unmatched_mass():
    frame = pd.DataFrame({"age": [None, 12, 120], "sex": [None, "unknown", np.nan],
                          "race_ethnicity": [None, "unknown", pd.NA]})
    assert diversity_index(frame) == 0


@pytest.mark.parametrize("race", ["native_american", "pacific_islander", "multiracial", "other"])
def test_grouped_source_race_categories(race):
    frame = pd.DataFrame({"age": [70], "sex": ["male"], "race_ethnicity": [race]})
    expected = 1 - ((1 - .367) + (1 - .536) + (1 - .107 * .94)) / 3
    assert diversity_index(frame) == pytest.approx(expected)


@pytest.mark.parametrize("age, probability", [(18, .030), (49.99, .030), (50, .301),
                                              (65, .367), (75, .302), (90.99, .302), (91, 0)])
def test_half_open_age_bands(age, probability):
    frame = pd.DataFrame({"age": [age], "sex": ["male"], "race_ethnicity": ["hispanic"]})
    assert diversity_index(frame) == pytest.approx((probability + .536 + .06) / 3)


def test_nullable_categories():
    frame = pd.DataFrame({"age": pd.Series([70, None], dtype="Float64"),
                          "sex": pd.Categorical(["male", None]),
                          "race_ethnicity": pd.Categorical(["white", None])})
    assert 0 < diversity_index(frame) < 1


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="demographic columns"):
        diversity_index(pd.DataFrame({"age": [70]}))


@given(st.lists(st.tuples(st.one_of(st.none(), st.integers(0, 120)),
                          st.sampled_from(["male", "female", None]),
                          st.sampled_from(["white", "black", "asian", "hispanic", "other", None])),
                max_size=100))
@settings(derandomize=True, deadline=None)  # G3: deterministic, no wall-clock deadline (was flaky)
def test_score_is_bounded_and_replication_invariant(rows):
    frame = pd.DataFrame(rows, columns=["age", "sex", "race_ethnicity"])
    value = diversity_index(frame)
    assert 0 <= value <= 1
    assert diversity_index(pd.concat([frame, frame], ignore_index=True)) == pytest.approx(value)
