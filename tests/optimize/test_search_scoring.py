"""Search invariants and paired uncertainty using synthetic, offline evidence only."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from clinova.contracts import CriteriaRule, Edit, ParsedProtocol, RiskScore, Target, Visit
from clinova.optimize.scoring import ModelScorer, Score, StubScorer
from clinova.optimize.search import (
    Candidate,
    _Evaluation,
    _ranges,
    optimize,
    optimize_with_report,
    pareto_front,
    pick_representatives,
)


def protocol():
    return ParsedProtocol(
        trial_id="FIXTURE-SEARCH", phase="phase2",
        rules=[CriteriaRule(rule_id="I1", kind="inclusion", field="crcl_ml_min", operator="ge",
                            value=60, raw_text="Synthetic CrCl >= 60"),
               CriteriaRule(rule_id="I2", kind="inclusion", field="ecog", operator="le",
                            value=1, raw_text="Synthetic ECOG <= 1"),
               CriteriaRule(rule_id="I3", kind="inclusion", field="consent_capacity",
                            operator="present", raw_text="Synthetic consent", locked=True)],
        visits=[Visit(visit_no=1, name="Contact", day=8, procedures=["questionnaire"],
                      est_hours=2, remote_ok=True)],
    )


def frame(n=12, seed=42, setting="any"):
    return pd.DataFrame({
        "crcl_ml_min": [35, 45, 55, 65] * 3, "ecog": [0, 1, 2, 1] * 3,
        "consent_capacity": [True] * 12, "age": [40, 58, 70, 80] * 3,
        "sex": ["female", "male"] * 6,
        "race_ethnicity": ["white", "black", "asian", "hispanic"] * 3,
    })


def space():
    return [Edit(kind="relax_rule", target_id="I1", new_value=45),
            Edit(kind="drop_rule", target_id="I1"),
            Edit(kind="drop_rule", target_id="I2"),
            Edit(kind="make_remote", target_id="1")]


def run(**kwargs):
    return optimize_with_report(protocol(), scorer=StubScorer(), edit_space=space(),
                                cohort_factory=frame, cohort_size=12, precedent_fn=lambda *_: [],
                                n_trials=100, **kwargs)


def test_search_respects_locks_edit_bound_and_is_reproducible():
    original = protocol()
    snapshot = original.model_dump()
    result, repeated = run(), run()
    assert original.model_dump() == snapshot
    assert [f.model_dump() for f in result.fixes] == [f.model_dump() for f in repeated.fixes]
    assert [f.label for f in result.fixes] == ["fastest", "lowest_dropout", "balanced"]
    assert result.cohort_seeds == (42, 43, 44, 45, 46)
    assert result.bootstrap_models_n == 0
    assert all(1 <= len(c.edits) <= 3 for c in result.pareto_front)
    assert all(e.target_id != "I3" for f in result.fixes for e in f.edits)
    assert all("Uncalibrated heuristic" in f.summary for f in result.fixes)
    assert any("five cohort seeds only" in note for note in result.notes)
    assert all(f.dollar_value == pytest.approx(max(0, -f.delta_p_amend) * 141_000)
               for f in result.fixes)
    for fix in result.fixes:
        for name in ("delta_enroll", "delta_dropout", "delta_diversity", "delta_p_amend"):
            interval = getattr(fix.ranges, name)
            assert interval.low <= getattr(fix, name) <= interval.high


@pytest.mark.parametrize("options", [{"max_edits": 0}, {"max_edits": 4}, {"n_trials": 0},
                                     {"cohort_size": 0}])
def test_invalid_search_limits(options):
    with pytest.raises(ValueError, match="max_edits"):
        optimize_with_report(protocol(), **options)


def test_empty_edit_space_returns_no_fabricated_fixes():
    result = optimize_with_report(protocol(), scorer=StubScorer(), edit_space=[],
                                  cohort_factory=frame, precedent_fn=lambda *_: [])
    assert result.fixes == [] and result.pareto_front == [] and result.evaluated_n == 0


def test_frozen_api_fixture_fallback_never_loads_live_evidence(monkeypatch):
    def unexpected_live_report(*_, **__):
        pytest.fail("synthetic fixture path tried live optimization")

    monkeypatch.setattr("clinova.optimize.search.optimize_with_report", unexpected_live_report)
    with pytest.raises(NotImplementedError, match="explicitly offline"):
        optimize(protocol())


def candidate(index, speed, dropout, diversity):
    return Candidate((Edit(kind="drop_rule", target_id=f"I{index}"),), protocol(),
                     Score(1, speed, dropout, 0, .5), diversity)


def test_front_deduplicates_and_excludes_dominated_objective_vectors():
    a, b = candidate(1, .8, .3, .7), candidate(2, .6, .1, .8)
    dominated = candidate(3, .5, .4, .6)
    assert pareto_front([a, a, b, dominated]) == [a, b]


def test_shortfall_adjusts_search_objective_but_contract_delta_remains_raw_speed():
    raw_fast = candidate(1, .8, .1, .7)
    raw_fast = replace(raw_fast, score=replace(raw_fast.score, p_shortfall=.8))
    reliable = candidate(2, .6, .1, .7)
    assert raw_fast.score.enrollment_speed > reliable.score.enrollment_speed
    assert pareto_front([raw_fast, reliable]) == [reliable]


def test_three_representatives_are_distinct_when_dropout_ties_allow_it():
    front = [candidate(1, .8, .1, .6), candidate(2, .7, .1, .8), candidate(3, .6, .1, 1)]
    picks = pick_representatives(front)
    assert len({c.edits[0].target_id for _, c in picks}) == 3
    assert picks[0][1] == front[0]
    assert all(c in front for _, c in picks)


@pytest.mark.parametrize("fail_on_create", [False, True])
def test_nsgaii_failure_enumerates_single_and_pairwise_edits(monkeypatch, fail_on_create):
    class BrokenStudy:
        def enqueue_trial(self, *_):
            pass

        def optimize(self, *_, **__):
            raise RuntimeError("synthetic NSGA failure")

    def create(**_):
        if fail_on_create:
            raise RuntimeError("synthetic study creation failure")
        return BrokenStudy()

    monkeypatch.setattr("clinova.optimize.search.optuna.create_study", create)
    result = run()
    assert result.fixes and result.evaluated_n >= len(space())
    assert all(len(c.edits) <= 2 for c in result.pareto_front)
    assert any("enumerated single and pairwise" in note for note in result.notes)


def test_invalid_candidate_scores_are_not_silently_discarded_as_unsafe_edits():
    class InvalidScorer(StubScorer):
        def score(self, p, eligible, fraction):
            if len(p.rules) < 3:
                raise ValueError("synthetic invalid fitted score")
            return super().score(p, eligible, fraction)

    with pytest.raises(ValueError, match="invalid fitted score"):
        optimize_with_report(protocol(), scorer=InvalidScorer(), edit_space=space(),
                              cohort_factory=frame, precedent_fn=lambda *_: [], n_trials=20)


def test_rule_screen_and_diversity_cache_does_not_ignore_visit_burden(monkeypatch):
    calls = []
    def recorded_diversity(eligible):
        calls.append(len(eligible))
        return .5

    monkeypatch.setattr("clinova.optimize.search.diversity_index", recorded_diversity)
    evaluator = _Evaluation(frame(), StubScorer())
    original = protocol()
    remote = original.model_copy(deep=True)
    remote.visits[0].requires_travel = False
    before, after = evaluator(original), evaluator(remote)
    assert calls == [len(frame().query("crcl_ml_min >= 60 and ecog <= 1"))]
    assert after.score.dropout_risk < before.score.dropout_risk
    assert after.score.enroll_rate > before.score.enroll_rate


class PairedScorer:
    source = "synthetic paired bootstrap fixture"

    def score(self, p, eligible, fraction):
        # Deliberately separated before/after marginal distributions: a paired
        # difference is exactly -0.1, whereas endpoint subtraction is spurious.
        after = p.title == "after"
        draws = tuple((.2, p - (.1 if after else 0)) for p in (.2, .5, .8))
        return Score(fraction, .7, .2, .2, .4 if after else .5, draws)


def test_ranges_use_paired_bootstrap_draws_across_all_five_seeds():
    original = protocol()
    after = original.model_copy(update={"title": "after"})
    scorer = PairedScorer()
    evaluation = _Evaluation(frame(), scorer)
    ranges, draws_n = _ranges(original, evaluation(after), evaluation(original), scorer,
                              range(5), 12, frame)
    assert draws_n == 3
    assert ranges.delta_p_amend.low == pytest.approx(-.1)
    assert ranges.delta_p_amend.high == pytest.approx(-.1)


def test_ranges_reject_unpaired_bootstrap_counts():
    class UnpairedScorer(PairedScorer):
        def score(self, p, eligible, fraction):
            s = super().score(p, eligible, fraction)
            return replace(s, bootstrap_risks=s.bootstrap_risks[:1] if p.title == "after" else s.bootstrap_risks)

    original, scorer = protocol(), UnpairedScorer()
    after = original.model_copy(update={"title": "after"})
    evaluation = _Evaluation(frame(), scorer)
    with pytest.raises(ValueError, match="matching indices"):
        _ranges(original, evaluation(after), evaluation(original), scorer, range(5), 12, frame)


def test_ranges_reject_bootstrap_count_changes_across_cohort_seeds():
    class ChangingScorer(PairedScorer):
        def score(self, p, eligible, fraction):
            s = super().score(p, eligible, fraction)
            return replace(s, bootstrap_risks=s.bootstrap_risks[:2] if eligible.attrs["seed"] == 1
                           else s.bootstrap_risks)

    def seeded_frame(n, seed, setting):
        result = frame()
        result.attrs["seed"] = seed
        return result

    original, scorer = protocol(), ChangingScorer()
    after = original.model_copy(update={"title": "after"})
    evaluation = _Evaluation(seeded_frame(12, 0, "any"), scorer)
    with pytest.raises(ValueError, match="stable across cohort seeds"):
        _ranges(original, evaluation(after), evaluation(original), scorer, range(5), 12, seeded_frame)


def risk_fn(p, *, sim):
    assert sim[0].n_personas == 12 and sim[0].n_eligible == 3
    assert sim[0].dropout_curve == []
    return [RiskScore(target=t, p=value, ci_low=max(0, value - .1), ci_high=min(1, value + .1),
                      confidence="low", model_version="synthetic")
            for t, value in [(Target.accrual_fail, .7), (Target.shortfall, .6), (Target.amend, .5)]]


def test_model_adapter_uses_surrogate_model_and_actual_draws():
    surrogate = SimpleNamespace(score=lambda p, eligible: (.8, .1))
    scorer = ModelScorer(surrogate=surrogate, risk_fn=risk_fn,
                         bootstrap_fn=lambda p, sim: [(.65, .45), (.55, .55)])
    s = scorer.score(protocol(), frame().iloc[:3], .25)
    assert (s.enroll_rate, s.dropout_risk, s.p_shortfall, s.p_amend) == (.8, .1, .6, .5)
    assert s.bootstrap_risks == ((.65, .45), (.55, .55))
    assert "heuristic" not in scorer.source


def test_model_adapter_automatically_picks_up_t22_frozen_score_and_bootstrap_hook(monkeypatch):
    from clinova.model import predict
    monkeypatch.setattr(predict, "score", risk_fn)
    monkeypatch.setattr(predict, "score_samples", lambda p, sim: [(.6, .5)], raising=False)
    scorer = ModelScorer(surrogate=SimpleNamespace(score=lambda *_: (.8, .1)))
    s = scorer.score(protocol(), frame().iloc[:3], .25)
    assert s.bootstrap_risks == ((.6, .5),)
    assert "bootstrap models" in scorer.source and "heuristic" not in scorer.source


def test_missing_surrogate_artifact_uses_explicit_behavior_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr("clinova.optimize.scoring.get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    scorer = ModelScorer(risk_fn=risk_fn)
    scorer.score(protocol(), frame().iloc[:3], .25)
    assert "heuristic behavior" in scorer.source and "T22 risk model" in scorer.source


def test_unavailable_bootstrap_preserves_fitted_point_model():
    def missing(*_, **__):
        raise FileNotFoundError("synthetic missing bootstrap artifact")

    scorer = ModelScorer(surrogate=SimpleNamespace(score=lambda *_: (.8, .1)),
                         risk_fn=risk_fn, bootstrap_fn=missing)
    s = scorer.score(protocol(), frame().iloc[:3], .25)
    assert s.p_amend == .5 and s.p_shortfall == .6 and not s.bootstrap_risks
    assert "T22 risk model" in scorer.source and "no bootstrap models" in scorer.source


def test_missing_risk_model_is_tried_once_and_explicitly_labeled():
    calls = []
    def missing(*_, **__):
        calls.append(1)
        raise NotImplementedError("synthetic pending model")

    scorer = ModelScorer(surrogate=SimpleNamespace(score=lambda *_: (.8, .1)), risk_fn=missing)
    for _ in range(2):
        scorer.score(protocol(), frame().iloc[:3], .25)
    assert calls == [1]
    assert "heuristic risk (uncalibrated)" in scorer.source


@pytest.mark.parametrize("values", [dict(p_amend=np.nan), dict(dropout_risk=1.1),
                                    dict(bootstrap_risks=((.2,),)),
                                    dict(bootstrap_risks=((.2, -1),))])
def test_scorer_rejects_nonfinite_or_invalid_probabilities(values):
    defaults = dict(eligible_frac=.5, enroll_rate=.8, dropout_risk=.1, p_shortfall=.3, p_amend=.4)
    with pytest.raises(ValueError):
        Score(**(defaults | values))


def test_fix_identity_changes_when_original_protocol_changes():
    kwargs = dict(scorer=StubScorer(), edit_space=space(), cohort_factory=frame,
                  precedent_fn=lambda *_: [], n_trials=20)
    original = protocol()
    changed = original.model_copy(update={"title": "Different original protocol snapshot"})
    before, after = optimize_with_report(original, **kwargs), optimize_with_report(changed, **kwargs)
    assert {f.fix_id for f in before.fixes}.isdisjoint(f.fix_id for f in after.fixes)
