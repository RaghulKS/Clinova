"""Distribution and mechanical invariants rather than individual invented outcomes."""
import numpy as np
import pandas as pd
import pytest

from clinova.cohort.generate import cohort_frame, generate_cohort, generate_cohort_df
from clinova.cohort.traits import load_assumptions, sample_traits
from clinova.contracts import COHORT_COLUMNS, Setting


def contexts(n=20000):
    return [{"age": 45 if i % 2 else 85, "sex": "female" if i % 3 else "male", "race_ethnicity": "asian" if i % 5 else "white"} for i in range(n)]


def test_traits_reproducible_and_exact_renal_formula():
    rows = contexts(50)
    first = sample_traits(rows, 4)
    assert first == sample_traits(rows, 4)
    for p, t in zip(rows, first, strict=True):
        expected = (140 - p["age"]) * t.weight_kg / (72 * t.creatinine_mg_dl)
        if p["sex"] == "female":
            expected *= 0.85
        assert t.crcl_ml_min == pytest.approx(expected)
        assert sum(x == "positive" for x in (t.egfr_status, t.alk_status, t.ros1_status)) <= 1


def test_age_and_smoking_correlations():
    rows = contexts()
    traits = sample_traits(rows, 12)
    frame = pd.DataFrame([t.model_dump(mode="json") for t in traits])
    ages = np.array([p["age"] for p in rows])
    assert frame.loc[ages == 85, "ecog"].mean() > frame.loc[ages == 45, "ecog"].mean()
    assert frame.loc[ages == 85, "crcl_ml_min"].mean() < frame.loc[ages == 45, "crcl_ml_min"].mean()
    never = frame.smoking_status == "never"
    assert (frame.loc[never, "egfr_status"] == "positive").mean() > (frame.loc[~never, "egfr_status"] == "positive").mean()


def test_ancestry_egfr_enrichment_with_identical_contexts():
    rows = [{"age": 69, "sex": "female", "race_ethnicity": "asian" if i % 2 else "white"} for i in range(20000)]
    traits = sample_traits(rows, 52)
    positive = np.array([t.egfr_status == "positive" for t in traits])
    assert positive[1::2].mean() > 2 * positive[::2].mean()


@pytest.mark.parametrize("setting", list(Setting))
def test_settings(setting):
    traits = sample_traits(contexts(500), 42, setting)
    if setting == Setting.first_line:
        assert all(t.prior_lines == 0 and not t.prior_platinum and not t.prior_immunotherapy for t in traits)
    elif setting == Setting.second_line_plus:
        assert all(t.prior_lines >= 1 for t in traits)
    else:
        assert {t.prior_lines for t in traits} >= {0, 1, 2}


def test_cohort_interfaces(monkeypatch):
    from clinova.cohort import generate
    original = [{"persona_id": f"p-{i}", **p} for i, p in enumerate(contexts(10))]
    monkeypatch.setattr(generate, "sample_personas", lambda n, seed: original[:n])
    personas = generate_cohort(10, 5)
    assert personas == generate_cohort(10, 5)
    assert list(cohort_frame(personas).columns) == list(COHORT_COLUMNS)
    pd.testing.assert_frame_equal(cohort_frame(personas), generate_cohort_df(10, 5))
    assert list(generate_cohort_df(0, 5).columns) == list(COHORT_COLUMNS)
    assert sample_traits([], 1) == []
    with pytest.raises(ValueError):
        generate_cohort(-1, 1)
    with pytest.raises(ValueError):
        generate_cohort(1, 1, "wrong")


def test_sources_preserved():
    from pathlib import Path

    import yaml
    draft = yaml.safe_load(Path("docs/spikes/assumptions.draft.yaml").read_text())
    shipped = load_assumptions()
    for key, value in draft.items():
        assert shipped[key] == value
