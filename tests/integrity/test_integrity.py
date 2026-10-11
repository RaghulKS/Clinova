"""Gate 2 integrity checks on the frozen model v1.0 artifacts. Offline: they read only committed files
(docs/predictions.csv, docs/model_manifest.json, docs/metrics.json, docs/data_report.md, docs/demo_results/).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

# Features rebuild from the first registered version's JSON alone (same check as tests/model, re-run here
# so the integrity suite states it on its own).
from tests.model.test_model import cohort, test_features_come_from_first_version_json_alone  # noqa: F401

DOCS = Path(__file__).resolve().parents[2] / "docs"
DEMO = ["NCT03232892", "NCT03417882", "NCT06333678"]  # demo trial, then the two backups (G1)
SPLIT_DATE = pd.Timestamp("2018-01-01")


@pytest.fixture(scope="module")
def pred() -> pd.DataFrame:
    return pd.read_csv(DOCS / "predictions.csv", dtype={"nct_id": str})


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((DOCS / "model_manifest.json").read_text(encoding="utf-8"))


def _test_ids(manifest: dict) -> set[str]:
    return {i for t in manifest["targets"].values() for i in t["test_ids"]}


def _fit_ids(manifest: dict) -> set[str]:
    ids = set(manifest.get("pca_fit_ids") or []) | set(manifest.get("ladder_source_ids") or [])
    for t in manifest["targets"].values():
        ids |= set(t["train_ids"]) | set(t["oof_calibration_ids"])
    return ids


def test_manifest_matches_metrics(pred, manifest):
    metrics = json.loads((DOCS / "metrics.json").read_text(encoding="utf-8"))
    assert manifest["model_version"] == metrics["model_version"] == "v1.0"
    assert set(pred["model_version"]) == {"v1.0"}


def test_demo_trial_and_backups_are_in_the_test_split(pred, manifest):
    test_ids, fit_ids = _test_ids(manifest), _fit_ids(manifest)
    for nct in DEMO:
        rows = pred[pred["nct_id"] == nct]
        assert len(rows), f"{nct} has no test predictions"
        assert (rows["split"] == "test").all()
        assert (pd.to_datetime(rows["start_date"]) >= SPLIT_DATE).all()
        assert nct in test_ids and nct not in fit_ids


def test_no_test_trial_touches_training_pca_or_calibration(pred, manifest):
    test_ids = _test_ids(manifest) | set(pred["nct_id"])
    assert manifest.get("ladder_source_ids") is not None, "v1.0 must record the ladder source ids"
    assert manifest["pca_fit_ids"], "v1.0 must record the PCA fit ids"
    for name, t in manifest["targets"].items():
        assert not test_ids & set(t["train_ids"]), f"{name}: test trial in training"
        assert not test_ids & set(t["oof_calibration_ids"]), f"{name}: test trial in calibration"
        assert set(t["oof_calibration_ids"]) <= set(t["train_ids"])
    assert not test_ids & set(manifest["pca_fit_ids"]), "test trial in PCA fit"
    assert not test_ids & set(manifest["ladder_source_ids"]), "test trial in relax ladders"
    assert (pred["split"] == "test").all()
    assert (pd.to_datetime(pred["start_date"]) >= SPLIT_DATE).all()


def test_label_prevalence_matches_data_report(manifest):
    text = (DOCS / "data_report.md").read_text(encoding="utf-8")
    report = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))
    for target, counts in manifest["label_counts"].items():
        known = sum(c["n"] for c in counts.values())
        pos = sum(c["pos"] for c in counts.values())
        rep = report["labels"][target]
        if not known:
            assert not rep["known"] or rep["positive"] == 0
            continue
        # Same database snapshot, written minutes apart while ingestion streams in: allow 1% drift.
        assert abs(known - rep["known"]) <= max(2, 0.01 * rep["known"]), (target, known, rep)
        assert pos / known == pytest.approx(rep["prevalence"], abs=0.01), (target, pos / known, rep)


def test_probabilities_are_in_unit_interval(pred):
    for col in ("p_model", "p_baseline"):
        assert pred[col].notna().all() and pred[col].between(0, 1).all(), col
    assert set(pred["label"].unique()) <= {0, 1}


def test_demo_results_are_valid_probabilities():
    files = [DOCS / "demo_results" / f"{nct}.json" for nct in DEMO]
    for f in files:
        report = json.loads(f.read_text(encoding="utf-8"))
        assert report["model_version"] == "v1.0"
        for s in report["scores"]:
            assert 0 <= s["ci_low"] <= s["p"] <= s["ci_high"] <= 1, (f.name, s["target"])
        assert len(report["flags"]) <= 3
