"""T22 model tests: offline (no DB, no network). The virtual cohort is the 200-persona fixture."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from clinova.cohort.generate import cohort_frame
from clinova.contracts import CriteriaRule, Edit, RiskScore, TrialRecord
from clinova.model import evaluate as E
from clinova.model import features as F
from clinova.model import predict as P
from clinova.model import train as T
from tests.conftest import load_fixture

FIRST = {  # a richer first registered version for the fictional fixture trial
    "identificationModule": {"nctId": "NCT99999901", "briefTitle": "FICTIONAL: ZX-4012 plus docetaxel"},
    "statusModule": {"overallStatus": "NOT_YET_RECRUITING", "startDateStruct": {"date": "2019-03-01"}},
    "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 180, "type": "ESTIMATED"},
                     "designInfo": {"allocation": "RANDOMIZED", "maskingInfo": {"masking": "NONE"}}},
    "eligibilityModule": {"eligibilityCriteria": "Inclusion Criteria: ECOG 0-1 ...", "minimumAge": "18 Years"},
    "armsInterventionsModule": {"armGroups": [{"label": "ZX-4012 + docetaxel"}, {"label": "Docetaxel"}],
                                "interventions": [{"name": "Docetaxel", "type": "DRUG"}]},
    "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Fictional Oncology", "class": "INDUSTRY"}},
    "contactsLocationsModule": {"locations": [{"country": "United States"}, {"country": "United States"},
                                              {"country": "Canada"}, {"country": "United States"}]},
}


@pytest.fixture
def cohort(personas):
    return cohort_frame(personas)


def _row(**over):
    rec = load_fixture("trial_record.json") | {"first_version": FIRST} | over
    return pd.DataFrame([rec])


def test_features_come_from_first_version_json_alone(parsed_protocol, cohort):
    protocols = {"NCT99999901": parsed_protocol}
    full = F.build_features(_row(), protocols, cohort=cohort)
    # Same first-version JSON, every other column dropped or replaced by later/label-side values.
    alone = F.build_features(pd.DataFrame({"nct_id": ["NCT99999901"], "first_version": [json.dumps(FIRST)]}),
                             protocols, cohort=cohort)
    leaky = F.build_features(_row(planned_enrollment=5, actual_enrollment=999, n_sites=99, start_date="2024-01-01",
                                  status="COMPLETED", label_accrual_fail=False), protocols, cohort=cohort)
    pd.testing.assert_frame_equal(full, alone)
    pd.testing.assert_frame_equal(full, leaky)
    # ...and the design features equal T11's first-version mapping of the same JSON.
    from clinova.ingest.pull import map_trial

    t: TrialRecord = map_trial(FIRST, FIRST)
    r = full.loc["NCT99999901"]
    assert r.log_planned_enrollment == pytest.approx(math.log1p(t.planned_enrollment)) == pytest.approx(math.log1p(180))
    assert (r.n_arms, r.n_sites, r.n_countries, r.randomized, r.sponsor_industry) == (t.n_arms, t.n_sites, 2, 1.0, 1.0)
    assert r.us_share == pytest.approx(0.75)
    assert not any("year" in c or "start" in c for c in full.columns)


def test_rule_features_and_strictness(parsed_protocol, cohort):
    r = F.rule_features(parsed_protocol.rules)
    assert (r["n_rules"], r["n_unparseable"], r["n_comorbidity_excl"], r["prior_platinum_required"]) == (22, 1, 3, 1)
    assert (r["ecog_max"], r["crcl_ml_min_min"], r["anc_min"], r["bilirubin_x_uln_max"]) == (1, 60, 1.5, 1.5)
    assert (r["prior_lines_max"], r["brain_mets_max"], r["stage_min"]) == (2, 1, 3)  # brain mets: treated allowed; >= IIIB
    assert math.isnan(r["prior_lines_min"]) and math.isnan(r["pdl1_tps_min"])
    raw = F.build_features(_row(), {"NCT99999901": parsed_protocol}, cohort=cohort)
    ref = F.fit_ref(raw.assign(ecog_max=2.0, crcl_ml_min_min=45.0))  # pretend the training median is looser
    out = F.apply_ref(raw, None, ref)
    assert out.loc["NCT99999901", "strict_ecog_max"] == 1.0  # ECOG <= 1 is stricter than a median of 2
    assert out.loc["NCT99999901", "strict_crcl_ml_min_min"] == 15.0
    assert "ecog_max" not in out and out["emb_pc1"].isna().all()
    assert 0 < out.loc["NCT99999901", "screen_eligible_frac"] < 1


def test_relax_edit_directions():
    ref = {"ladders": {"crcl_ml_min": [30.0, 45.0, 50.0, 60.0], "ecog": [0.0, 1.0, 2.0, 3.0, 4.0],
                       "bilirubin_x_uln": [1.0, 1.5, 2.0, 3.0]}}

    def rule(kind, field, op, value):
        return CriteriaRule(rule_id="I1" if kind == "inclusion" else "E1", kind=kind, field=field, operator=op,
                            value=value, raw_text="x")

    assert P.relax_edit(rule("inclusion", "ecog", "le", 1), ref).new_value == 2
    assert P.relax_edit(rule("inclusion", "crcl_ml_min", "ge", 60), ref).new_value == 50
    assert P.relax_edit(rule("exclusion", "bilirubin_x_uln", "gt", 1.5), ref).new_value == 2
    assert P.relax_edit(rule("inclusion", "stage", "ge", "IIIB"), ref).new_value == "IIIA"
    assert P.relax_edit(rule("exclusion", "autoimmune_disease", "present", None), ref) is None
    assert P.relax_edit(rule("inclusion", "crcl_ml_min", "ge", 30), ref) is None  # nothing looser was used


def _synthetic(parsed_protocol, cohort, n=90, seed=0):
    """Variants of the fixture protocol (random rules dropped) with labels tied to eligibility."""
    from clinova.simulate.screen import apply_edits

    rng = np.random.default_rng(seed)
    droppable = [r.rule_id for r in parsed_protocol.rules if not r.locked and r.parse_status == "ok"]
    protocols, rows = {}, []
    for i in range(n):
        nid = f"NCT{90000000 + i}"
        drop = rng.choice(droppable, size=rng.integers(0, 6), replace=False)
        rules = apply_edits(parsed_protocol.rules, [Edit(kind="drop_rule", target_id=str(d)) for d in drop])
        protocols[nid] = parsed_protocol.model_copy(update={"trial_id": nid, "rules": rules})
        fv = json.loads(json.dumps(FIRST))
        fv["designModule"]["enrollmentInfo"]["count"] = int(rng.integers(20, 600))
        rows.append({"nct_id": nid, "first_version": fv})
    trials = pd.DataFrame(rows)
    raw = F.build_features(trials, protocols, cohort=cohort)
    ref = F.fit_ref(raw, None, protocols)
    df = F.apply_ref(raw, None, ref)
    logit = -4 * (df["screen_eligible_frac"] - df["screen_eligible_frac"].mean()) / df["screen_eligible_frac"].std()
    df["label_accrual_fail"] = rng.random(n) < 1 / (1 + np.exp(-logit))
    df["label_shortfall"] = rng.random(n) < 0.5
    df["label_amend"] = None
    df["start_date"] = pd.date_range("2008-01-01", periods=n, freq="30D").date
    df["split"] = "train"
    return df, ref


def test_train_save_load_score_and_flags(parsed_protocol, cohort, tmp_path, monkeypatch):
    df, ref = _synthetic(parsed_protocol, cohort)
    bundle = T.train(df, {"ref": ref, "bootstraps": 4, "save": True, "models_dir": tmp_path, "version": "vtest"})
    assert set(bundle["models"]) == {"accrual_fail", "shortfall"} and "amend" in bundle["skipped"]
    assert not any(c in bundle["feature_names"] for c in F.SIM_COLS) and "start_date" not in bundle["feature_names"]
    assert len(bundle["bootstraps"]["accrual_fail"]) == 4 and len(bundle["baseline_features"]) == 10
    monkeypatch.setattr(T, "MODELS_DIR", tmp_path)
    loaded = T.load_bundle()
    assert loaded["version"] == "vtest"
    p = T.predict_p(loaded, "accrual_fail", df)
    assert np.allclose(p, T.predict_p(bundle, "accrual_fail", df)) and ((p >= 0) & (p <= 1)).all()

    # Offline scoring of the fixture protocol: no DB lookups, fixture cohort.
    monkeypatch.setattr(F, "screen_cohort", lambda: cohort)
    trials = pd.DataFrame({"nct_id": ["FIXTURE-2L-001"], "first_version": [FIRST]})
    monkeypatch.setattr(P, "_context", lambda x: (parsed_protocol, trials, {}, pd.DataFrame(columns=F.SIM_COLS)))
    scores = P.score(parsed_protocol, bundle=loaded)
    assert [s.target.value for s in scores] == ["accrual_fail", "shortfall", "amend"]
    assert (scores[2].p, scores[2].ci_low, scores[2].ci_high, scores[2].top_drivers[0].feature) == (0.5, 0, 1, "no_model")
    for s in scores[:2]:
        assert isinstance(s, RiskScore) and s.ci_low <= s.p <= s.ci_high and s.confidence == "low"
        assert s.model_version == "vtest" and len(s.top_drivers) <= 4
    flags = P.flag_criteria(parsed_protocol, bundle=loaded)
    monkeypatch.setattr(T, "MODELS_DIR", tmp_path / "empty")
    with pytest.raises(NotImplementedError):  # no bundle: the API falls back to fixtures
        P.score(parsed_protocol)
    assert [f.rank for f in flags] == [1, 2, 3]
    locked = {r.rule_id for r in parsed_protocol.rules if r.locked or r.parse_status != "ok"}
    assert not locked & {f.rule_id for f in flags}
    assert flags[0].delta_p >= flags[1].delta_p - 1e-9 and all(f.where and f.why and f.what_to_do for f in flags)


def test_metric_helpers():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 200)
    p = np.clip(y * 0.3 + rng.random(200) * 0.7, 0, 1)
    ci = E.auc_ci(y, p, n_boot=200)
    assert 0.5 < ci.ci_low <= ci.value <= ci.ci_high <= 1
    d = E.paired_diff_ci(y, p, p, n_boot=50)
    assert d.value == d.ci_low == d.ci_high == 0
    bins = E.reliability(y, p)
    assert len(bins) == 10 and sum(b.n for b in bins) == 200
    assert all(b.mean_pred is None or b.bin_low <= b.mean_pred <= b.bin_high for b in bins)

