"""Every contract fixture validates, round-trips losslessly, and agrees with the other fixtures."""

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from clinova.contracts import (
    BIOMARKER_STATUSES,
    FIELD_SPECS,
    HISTOLOGIES,
    LOCKED_BY_DEFAULT,
    PROCEDURE_VOCAB,
    RACE_ETHNICITIES,
    STAGES,
    AgentDecision,
    ClinicalField,
    ClinicalTraits,
    CriteriaRule,
    CriterionFlag,
    Fix,
    Funnel,
    ParsedProtocol,
    Persona,
    ReplayResult,
    RiskScore,
    SimRunSummary,
    Target,
    TrialRecord,
    ValidationMetrics,
    VersionEvent,
)
from tests.conftest import FIXTURES, load_fixture
from tests.fixtures.make_funnel import build_funnel, screen, sim_summary

AMENDMENT_COST_PHASE2 = 141_000  # config.AMENDMENT_COST_PHASE2; the fixture protocol is phase2

MODELS = {
    "parsed_protocol.json": ParsedProtocol,
    "personas.json": list[Persona],
    "funnel.json": Funnel,
    "agent_decisions.json": list[AgentDecision],
    "sim_run_summary.json": SimRunSummary,
    "risk_scores.json": list[RiskScore],
    "criterion_flags.json": list[CriterionFlag],
    "fixes.json": list[Fix],
    "trial_record.json": TrialRecord,
    "version_events.json": list[VersionEvent],
    "metrics.json": ValidationMetrics,
    "replay.json": ReplayResult,
}


def load(name):
    return TypeAdapter(MODELS[name]).validate_python(load_fixture(name))


def test_every_json_fixture_has_a_model():
    assert {p.name for p in FIXTURES.glob("*.json")} == set(MODELS)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_fixture_validates_and_round_trips(name):
    adapter = TypeAdapter(MODELS[name])
    obj = adapter.validate_python(load_fixture(name))
    assert adapter.validate_json(adapter.dump_json(obj)) == obj


# --- protocol and personas ----------------------------------------------------------------------


def test_protocol_rules_follow_the_vocabulary(parsed_protocol):
    rules = parsed_protocol.rules
    kinds = [r.kind for r in rules]
    assert kinds == sorted(kinds, key=["inclusion", "exclusion"].index)  # inclusion then exclusion
    for prefix, kind in (("I", "inclusion"), ("E", "exclusion")):
        ids = [r.rule_id for r in rules if r.kind == kind]
        assert ids == [f"{prefix}{i}" for i in range(1, len(ids) + 1)]
    for r in rules:
        spec = FIELD_SPECS[r.field]
        assert r.locked == (r.field in LOCKED_BY_DEFAULT), r.rule_id
        assert r.unit == spec.unit, r.rule_id
        if spec.kind == "numeric":
            assert spec.min <= r.value <= spec.max, r.rule_id
        elif spec.kind in ("ordinal", "categorical"):
            assert set(r.value if isinstance(r.value, list) else [r.value]) <= set(spec.values), r.rule_id
        elif spec.kind == "boolean":
            assert r.operator in ("present", "absent") and r.value is None, r.rule_id
    assert parsed_protocol.unparseable == ["E6"]
    assert parsed_protocol.setting == "second_line_plus"


def test_protocol_visits(parsed_protocol):
    visits = parsed_protocol.visits
    assert [v.visit_no for v in visits] == list(range(1, 9))
    assert [v.day for v in visits] == sorted(v.day for v in visits) and visits[0].day < 0
    assert all(set(v.procedures) <= set(PROCEDURE_VOCAB) for v in visits)
    assert [v.visit_no for v in visits if v.invasive] == [6] and "biopsy" in visits[5].procedures
    assert not any(v.estimated for v in visits)


def test_personas_are_plausible(personas):
    assert [p.persona_id for p in personas] == [f"fx-{i:04d}" for i in range(1, 201)]
    for p in personas:
        c = p.clinical
        assert p.source == "fixture" and p.race_ethnicity in RACE_ETHNICITIES
        assert c.stage in STAGES and c.histology in HISTOLOGIES
        assert {c.egfr_status, c.alk_status, c.ros1_status} <= set(BIOMARKER_STATUSES)
        cockcroft_gault = (140 - p.age) * c.weight_kg / (72 * c.creatinine_mg_dl) * (0.85 if p.sex == "female" else 1)
        assert c.crcl_ml_min == pytest.approx(cockcroft_gault, rel=0.02), p.persona_id


# --- funnel, decisions, simulation summary ------------------------------------------------------


def test_funnel_arithmetic(funnel):
    first, rest = funnel.stages[0], funnel.stages[1:]
    assert (first.rule_id, first.label, first.remaining, first.excluded) == (None, "Screened", funnel.n_screened, 0)
    for prev, cur in zip(funnel.stages, rest, strict=False):
        assert cur.remaining == prev.remaining - cur.excluded
    assert funnel.stages[-1].remaining == funnel.n_eligible
    assert funnel.eligible_frac == funnel.n_eligible / funnel.n_screened
    assert 0.25 <= funnel.eligible_frac <= 0.45
    counts = [m.excluded_n for m in funnel.marginal]
    assert counts == sorted(counts, reverse=True)
    for m in funnel.marginal:
        assert m.excluded_frac == m.excluded_n / funnel.n_screened and m.excluded_only_n <= m.excluded_n


def test_funnel_matches_reference_screen(parsed_protocol, personas, funnel):
    assert funnel.skipped_rules == parsed_protocol.unparseable
    applied = [r.rule_id for r in parsed_protocol.rules if r.parse_status == "ok"]
    assert [s.rule_id for s in funnel.stages[1:]] == applied
    assert build_funnel(parsed_protocol, personas) == funnel


def test_decisions_come_from_eligible_personas(parsed_protocol, personas):
    decisions = load("agent_decisions.json")
    failed = screen(parsed_protocol, personas)
    ids = [d.persona_id for d in decisions]
    assert len(ids) == len(set(ids)) == 25
    assert all(pid in failed and not failed[pid] for pid in ids)
    assert all(d.dropout_visit is None or d.dropout_visit <= len(parsed_protocol.visits) for d in decisions)
    assert 0.55 <= sum(d.enroll for d in decisions) / len(decisions) <= 0.70


def test_sim_summary_is_derived_from_funnel_and_decisions(parsed_protocol, funnel):
    sim = load("sim_run_summary.json")
    assert sim == sim_summary(parsed_protocol, funnel, load("agent_decisions.json"))
    assert re.fullmatch(r"[0-9a-f]{32}", sim.run_id) and sim.run_at.utcoffset().total_seconds() == 0
    assert len(sim.dropout_curve) == len(parsed_protocol.visits)
    assert sim.dropout_curve == sorted(sim.dropout_curve)
    assert sim.dropout_curve[-1] == pytest.approx(1 - sim.completion_rate)


# --- model outputs, flags, fixes ----------------------------------------------------------------


def test_risk_scores():
    scores = load("risk_scores.json")
    assert sorted(s.target for s in scores) == sorted(Target)
    for s in scores:
        assert s.ci_low <= s.p <= s.ci_high
        assert 3 <= len(s.top_drivers) <= 5
        sizes = [abs(d.contribution) for d in s.top_drivers]
        assert sizes == sorted(sizes, reverse=True)


def test_flags_and_fixes_reference_the_protocol(parsed_protocol):
    rules = {r.rule_id: r for r in parsed_protocol.rules}
    visit_nos = {v.visit_no for v in parsed_protocol.visits}

    def check_edit(edit):
        if edit.kind in ("relax_rule", "drop_rule"):
            assert edit.target_id in rules and not rules[edit.target_id].locked
        else:
            assert int(edit.target_id) in visit_nos

    flags = load("criterion_flags.json")
    assert [f.rank for f in flags] == [1, 2, 3]
    for f in flags:
        assert rules[f.rule_id].field == f.field and f.suggested_edit.target_id == f.rule_id
        check_edit(f.suggested_edit)

    fixes = load("fixes.json")
    assert [f.label for f in fixes] == ["fastest", "lowest_dropout", "balanced"]
    for fix in fixes:
        assert fix.trial_id == parsed_protocol.trial_id and parsed_protocol.phase == "phase2"
        for edit in fix.edits:
            check_edit(edit)
        for name in ("delta_enroll", "delta_dropout", "delta_diversity", "delta_p_amend"):
            interval = getattr(fix.ranges, name)
            assert interval.low <= getattr(fix, name) <= interval.high, (fix.fix_id, name)
        assert fix.dollar_value == pytest.approx(max(0.0, -fix.delta_p_amend) * AMENDMENT_COST_PHASE2)
        assert all(p.nct_id.startswith("NCT9999") for p in fix.precedents)


# --- trial history, validation, replay ----------------------------------------------------------


def test_trial_record_and_events():
    trial, events = load("trial_record.json"), load("version_events.json")
    assert trial.eligibility_text_first == load_fixture("protocol_2L_eligibility.txt")
    assert trial.eligibility_text_current == trial.eligibility_text_first.replace(">= 60 mL/min", ">= 45 mL/min").replace(
        "status of 0 or 1.", "status of 0, 1 or 2.")
    assert trial.split == "test" and trial.start_date.year >= 2018
    assert trial.label_shortfall == (trial.actual_enrollment < 0.8 * trial.planned_enrollment)
    assert all(e.nct_id == trial.nct_id for e in events)
    widened = [e for e in events if e.field == "eligibility" and e.change_class in ("widened", "mixed")]
    assert trial.label_amend and widened and widened[0].version_date > trial.start_date
    assert all(e.change_class is None for e in events if e.field != "eligibility")


def test_metrics_and_replay_shapes():
    metrics, replay = load("metrics.json"), load("replay.json")
    assert sorted(t.target for t in metrics.targets) == sorted(Target)
    for t in metrics.targets:
        assert len(t.reliability) == 10 and sum(b.n for b in t.reliability) == t.n_test
    assert metrics.hit_rate and metrics.ablation and any("FIXTURE" in n for n in metrics.notes)
    assert [m.month for m in replay.months] == [f"{y}-{m:02d}" for y in range(2020, 2024) for m in range(1, 13)]
    assert replay.summary.startswith("FIXTURE")


# --- the contracts themselves -------------------------------------------------------------------


def test_field_specs_cover_every_clinical_field():
    assert set(FIELD_SPECS) == set(ClinicalField)
    assert set(ClinicalField) - {"age", "sex", "other"} <= set(ClinicalTraits.model_fields)


def test_extra_key_is_rejected():
    data = load_fixture("personas.json")[0] | {"favourite_color": "blue"}
    with pytest.raises(ValidationError):
        Persona.model_validate(data)


def test_other_field_must_be_unparseable():
    with pytest.raises(ValidationError):
        CriteriaRule(rule_id="E9", kind="exclusion", field="other", operator="present", raw_text="x", parse_status="ok")


def test_dropout_without_enroll_is_rejected():
    with pytest.raises(ValidationError):
        AgentDecision(persona_id="fx-0001", enroll=False, dropout_visit=3, reason="I said no.")
