"""Offline checks; all threshold rows in this file are synthetic test observations."""

from contextlib import contextmanager

import pandas as pd
import pytest

from clinova.contracts import ClinicalField, CriteriaRule, Edit, ParsedProtocol, Visit
from clinova.optimize.edits import (
    apply_protocol_edits,
    build_edit_space,
    load_observed_rules,
    visit_can_be_remote,
)
from clinova.simulate.screen import screen


def rule(field="crcl_ml_min", operator="ge", value=60, kind="inclusion", rule_id="I1", **kwargs):
    return CriteriaRule(rule_id=rule_id, kind=kind, field=field, operator=operator,
                        value=value, raw_text="Synthetic test rule", **kwargs)


def visit(number=1, day=8, procedures=None, **kwargs):
    values = {"name": "Follow-up", "est_hours": 1.5, "remote_ok": True, **kwargs}
    return Visit(visit_no=number, day=day, procedures=procedures or ["blood_draw", "adverse_event_review"],
                 **values)


def protocol(rules=(), visits=()):
    return ParsedProtocol(trial_id="FIXTURE-OPTIMIZER-001", rules=list(rules), visits=list(visits))


def synthetic_observation(field="crcl_ml_min", operator="ge", value=45, kind="inclusion", **kwargs):
    return {"nct_id": "FIXTURE-OBSERVED-001", "field": field, "operator": operator,
            "value": value, "kind": kind, **kwargs}


def relaxations(edits):
    return [edit for edit in edits if edit.kind == "relax_rule"]


def test_synthetic_numeric_ladder_uses_only_injected_values_and_is_ordered():
    rows = [synthetic_observation(value=value) for value in [30, 60, 50, 45, 75, 45]]
    edits = build_edit_space(protocol([rule()]), observed_rows=rows)
    assert [edit.new_value for edit in relaxations(edits)] == [50, 45, 30]
    assert edits[-1] == Edit(kind="drop_rule", target_id="I1")
    assert all(edit.new_operator is None for edit in relaxations(edits))


def test_empty_injected_rows_are_offline_and_do_not_invent_thresholds(monkeypatch):
    def unexpected_database(**kwargs):
        pytest.fail("offline edit generation queried the database")
    monkeypatch.setattr("clinova.optimize.edits.load_observed_rules", unexpected_database)
    assert build_edit_space(protocol([rule()]), observed_rows=[]) == [Edit(kind="drop_rule", target_id="I1")]


def test_default_load_excludes_current_trial_and_uses_only_editable_fields(monkeypatch):
    captured = {}
    def recorded_load(**kwargs):
        captured.update(kwargs)
        return [synthetic_observation()]
    monkeypatch.setattr("clinova.optimize.edits.load_observed_rules", recorded_load)
    p = protocol([rule(), rule(field="ecog", operator="le", value=1, rule_id="I2", locked=True)])
    assert relaxations(build_edit_space(p))[0].new_value == 45
    assert captured == {"fields": {ClinicalField.crcl_ml_min}, "exclude_trial_id": p.trial_id}


def test_own_observation_and_malformed_rows_are_ignored():
    p = protocol([rule()])
    rows = [synthetic_observation(nct_id=p.trial_id), synthetic_observation(value=4),
            synthetic_observation(value=True), synthetic_observation(value=float("nan")),
            synthetic_observation(value=50, unit="mL/sec"), synthetic_observation(value=55, parse_status="unparseable"),
            {"field": "unknown", "kind": "inclusion", "operator": "ge", "value": 45}]
    assert not relaxations(build_edit_space(p, observed_rows=rows))


@pytest.mark.parametrize("kind, operator, old, observed, expected", [
    ("inclusion", "ge", 60, [45, 75], [45]),
    ("inclusion", "le", 60, [45, 75], [75]),
    ("exclusion", "ge", 60, [45, 75], [75]),
    ("exclusion", "le", 60, [45, 75], [45]),
])
def test_threshold_direction_respects_inclusion_and_exclusion(kind, operator, old, observed, expected):
    p = protocol([rule(kind=kind, operator=operator, value=old)])
    rows = [synthetic_observation(kind=kind, operator=operator, value=value) for value in observed]
    assert [edit.new_value for edit in relaxations(build_edit_space(p, observed_rows=rows))] == expected


def test_synthetic_ecog_and_brain_metastases_operator_relaxations():
    p = protocol([rule(field="ecog", operator="le", value=1),
                  rule(field="brain_mets", operator="ne", value="none", kind="exclusion", rule_id="E1")])
    rows = [synthetic_observation(field="ecog", operator="le", value=2),
            synthetic_observation(field="brain_mets", operator="eq", value="untreated", kind="exclusion")]
    edits = relaxations(build_edit_space(p, observed_rows=rows))
    assert edits == [Edit(kind="relax_rule", target_id="I1", new_value=2),
                     Edit(kind="relax_rule", target_id="E1", new_value="untreated", new_operator="eq")]
    updated = apply_protocol_edits(p, edits)
    frame = pd.DataFrame({"ecog": [0, 1, 2, 2, 3],
                          "brain_mets": ["none", "none", "none", "treated_stable", "untreated"]})
    before, _ = screen(p.rules, frame)
    after, _ = screen(updated.rules, frame)
    assert (before <= after).all()
    assert after.tolist() == [True, True, True, True, False]


def test_categorical_membership_relaxations_and_semantic_deduplication():
    original = rule(field="histology", operator="in", value=["adenocarcinoma"])
    rows = [synthetic_observation(field="histology", operator="in", value=["adenocarcinoma", "squamous"]),
            synthetic_observation(field="histology", operator="in", value=["squamous", "adenocarcinoma"]),
            synthetic_observation(field="histology", operator="eq", value="squamous")]
    candidates = relaxations(build_edit_space(protocol([original]), observed_rows=rows))
    assert len(candidates) == 1
    assert candidates[0].new_value == ["adenocarcinoma", "squamous"]
    assert candidates == relaxations(build_edit_space(protocol([original]), observed_rows=rows[::-1]))


@pytest.mark.parametrize("original, rows", [
    (rule(field="brain_mets", operator="eq", value="none"),
     [synthetic_observation(field="brain_mets", operator="le", value="treated_stable"),
      synthetic_observation(field="brain_mets", operator="in", value=["none", "treated_stable"])]),
    (rule(field="prior_platinum", operator="present", value=None),
     [synthetic_observation(field="prior_platinum", operator="not_in", value=[]),
      synthetic_observation(field="prior_platinum", operator="in", value=[True, False])]),
])
def test_equivalent_observation_encodings_choose_same_edit_independent_of_row_order(original, rows):
    p = protocol([original])
    forward = relaxations(build_edit_space(p, observed_rows=rows))
    assert len(forward) == 1
    assert forward == relaxations(build_edit_space(p, observed_rows=rows[::-1]))
    updated = apply_protocol_edits(p, forward)
    values = ["none", "treated_stable", "untreated"] if original.field == "brain_mets" else [True, False]
    frame = pd.DataFrame({original.field: [*values, None]})
    before, _ = screen(p.rules, frame)
    after, _ = screen(updated.rules, frame)
    assert (before <= after).all() and (before != after).any()


def test_strict_boundary_and_numeric_membership_relaxations():
    original = rule(operator="gt", value=60)
    rows = [synthetic_observation(operator="ge", value=60), synthetic_observation(operator="eq", value=75)]
    assert relaxations(build_edit_space(protocol([original]), observed_rows=rows)) == [
        Edit(kind="relax_rule", target_id="I1", new_value=60, new_operator="ge")]
    membership = rule(field="ecog", operator="in", value=[0, 1])
    rows = [synthetic_observation(field="ecog", operator="in", value=[0, 1, 2])]
    assert len(relaxations(build_edit_space(protocol([membership]), observed_rows=rows))) == 1


@pytest.mark.parametrize("field", ["pregnancy", "consent_capacity"])
def test_default_locked_fields_cannot_be_changed_even_when_locked_flag_false(field):
    original = rule(field=field, operator="present", value=None, locked=False)
    p = protocol([original])
    assert build_edit_space(p, observed_rows=[synthetic_observation(field=field, operator="absent", value=None)]) == []
    for edit in [Edit(kind="drop_rule", target_id="I1"),
                 Edit(kind="relax_rule", target_id="I1", new_operator="absent")]:
        with pytest.raises(ValueError, match="locked"):
            apply_protocol_edits(p, [edit])


def test_author_locked_and_unparseable_rules_have_no_candidates():
    p = protocol([rule(locked=True), rule(field="other", operator="present", value=None,
                                         rule_id="I2", parse_status="unparseable")])
    assert build_edit_space(p, observed_rows=[]) == []
    with pytest.raises(ValueError, match="locked"):
        apply_protocol_edits(p, [Edit(kind="drop_rule", target_id="I1")])


@pytest.mark.parametrize("procedure", ["infusion", "injection", "biopsy", "bronchoscopy", "pk_sampling",
                                       "ct_scan", "mri_brain", "ecg", "physical_exam", "other", "unknown"])
def test_unsafe_procedures_cannot_be_remote_even_if_mislabeled(procedure):
    v = visit(procedures=[procedure])
    assert not visit_can_be_remote(v)
    assert not any(edit.kind == "make_remote" for edit in build_edit_space(protocol(visits=[v]), observed_rows=[]))
    with pytest.raises(ValueError, match="cannot be made remote"):
        apply_protocol_edits(protocol(visits=[v]), [Edit(kind="make_remote", target_id="1")])


def test_remote_local_labs_and_contact_preserve_duration_and_input():
    v = visit(procedures=["vitals", "blood_draw", "urine_test", "adverse_event_review", "phone_call"])
    p = protocol(visits=[v])
    snapshot = p.model_dump()
    assert visit_can_be_remote(v)
    edits = build_edit_space(p, observed_rows=[])
    assert edits == [Edit(kind="make_remote", target_id="1")]
    updated = apply_protocol_edits(p, edits)
    assert not updated.visits[0].requires_travel
    assert updated.visits[0].est_hours == v.est_hours
    assert p.model_dump() == snapshot
    assert build_edit_space(updated, observed_rows=[]) == []


@pytest.mark.parametrize("overrides", [{"remote_ok": False}, {"invasive": True}, {"procedures": []}])
def test_remote_permission_requires_compatible_flags_and_nonempty_procedures(overrides):
    v = visit().model_copy(update=overrides)
    assert not visit_can_be_remote(v)


def test_only_explicitly_optional_contacts_can_be_cut():
    optional = visit(name="Optional contact", procedures=["phone_call"])
    required = visit(number=2, day=15, name="Required contact", procedures=["phone_call"])
    safety = visit(number=3, day=22, name="Optional safety visit", procedures=["adverse_event_review"])
    p = protocol(visits=[optional, required, safety])
    edits = build_edit_space(p, observed_rows=[])
    assert [edit.target_id for edit in edits if edit.kind == "cut_visit"] == ["1"]
    updated = apply_protocol_edits(p, [Edit(kind="cut_visit", target_id="1")])
    assert [v.visit_no for v in updated.visits] == [1, 2]
    assert [v.name for v in updated.visits] == [required.name, safety.name]
    with pytest.raises(ValueError, match="cannot cut"):
        apply_protocol_edits(p, [Edit(kind="cut_visit", target_id="2")])


@pytest.mark.parametrize("name", ["Non-optional contact", "Non optional questionnaire", "Not optional call",
                                  "Required optional contact", "Mandatory optional questionnaire"])
def test_required_or_negated_optional_contacts_cannot_be_cut(name):
    p = protocol(visits=[visit(name=name, procedures=["phone_call"])])
    assert not any(edit.kind == "cut_visit" for edit in build_edit_space(p, observed_rows=[]))
    with pytest.raises(ValueError, match="cannot cut"):
        apply_protocol_edits(p, [Edit(kind="cut_visit", target_id="1")])


def test_same_day_merge_preserves_all_procedure_occurrences_and_hours():
    p = protocol(visits=[visit(1, 8, ["blood_draw"]), visit(2, 8, ["blood_draw", "questionnaire"], estimated=True),
                         visit(3, 15, ["infusion"], remote_ok=False)])
    snapshot = p.model_dump()
    assert [edit.target_id for edit in build_edit_space(p, observed_rows=[]) if edit.kind == "merge_visits"] == ["1"]
    updated = apply_protocol_edits(p, [Edit(kind="merge_visits", target_id="1")])
    merged = updated.visits[0]
    assert merged.procedures == ["blood_draw", "blood_draw", "questionnaire"]
    assert merged.est_hours == 3
    assert merged.day == 8 and merged.requires_travel and merged.remote_ok and merged.estimated
    assert [v.visit_no for v in updated.visits] == [1, 2]
    assert p.model_dump() == snapshot


def test_original_visit_ids_survive_unrelated_cuts_in_a_batch():
    p = protocol(visits=[visit(1, 8, ["phone_call"], name="Optional call"),
                         visit(2, 15), visit(3, 22)])
    updated = apply_protocol_edits(p, [Edit(kind="cut_visit", target_id="1"),
                                        Edit(kind="make_remote", target_id="3")])
    assert [v.visit_no for v in updated.visits] == [1, 2]
    assert updated.visits[0].requires_travel
    assert not updated.visits[1].requires_travel
    reversed_order = apply_protocol_edits(p, [Edit(kind="make_remote", target_id="3"),
                                               Edit(kind="cut_visit", target_id="1")])
    assert updated == reversed_order


@pytest.mark.parametrize("edit, message", [
    (Edit(kind="merge_visits", target_id="1"), "across study days"),
    (Edit(kind="merge_visits", target_id="2"), "last visit"),
    (Edit(kind="make_remote", target_id="99"), "unknown visit_no"),
    (Edit(kind="drop_rule", target_id="I99"), "unknown rule_id"),
    (Edit(kind="relax_rule", target_id="I1", new_value=75), "not a strict relaxation"),
    (Edit(kind="relax_rule", target_id="I1", new_value=60), "not a strict relaxation"),
])
def test_invalid_batches_are_rejected_without_mutation(edit, message):
    p = protocol([rule()], [visit(1, 8), visit(2, 15)])
    snapshot = p.model_dump()
    with pytest.raises(ValueError, match=message):
        apply_protocol_edits(p, [edit])
    assert p.model_dump() == snapshot


def test_overlapping_visit_edits_and_duplicate_rule_edits_are_rejected():
    p = protocol([rule()], [visit(1, 8), visit(2, 8), visit(3, 8)])
    for edits in [
        [Edit(kind="merge_visits", target_id="1"), Edit(kind="make_remote", target_id="2")],
        [Edit(kind="merge_visits", target_id="1"), Edit(kind="merge_visits", target_id="2")],
    ]:
        with pytest.raises(ValueError, match="overlapping"):
            apply_protocol_edits(p, edits)
    with pytest.raises(ValueError, match="multiple edits"):
        apply_protocol_edits(p, [Edit(kind="relax_rule", target_id="I1", new_value=45),
                                 Edit(kind="drop_rule", target_id="I1")])


def test_rule_edit_and_result_are_deep_copies_and_unparseable_index_recomputed():
    original = rule(field="histology", operator="in", value=["adenocarcinoma"])
    unparsed = rule(field="other", operator="present", value=None, rule_id="I2", parse_status="unparseable")
    p = protocol([original, unparsed])
    edit = Edit(kind="relax_rule", target_id="I1", new_value=["adenocarcinoma", "squamous"])
    updated = apply_protocol_edits(p, [edit, Edit(kind="drop_rule", target_id="I2")])
    assert updated.unparseable == []
    updated.rules[0].value.append("nsclc_nos")
    assert original.value == ["adenocarcinoma"]
    assert edit.new_value == ["adenocarcinoma", "squamous"]
    assert p.unparseable == ["I2"]


def test_loader_filters_real_nsclc_rows_and_preserves_query_parameters(monkeypatch):
    captured = {}
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql, params):
            captured.update(sql=sql, params=params)
        def fetchall(self):
            return [{"nct_id": "NCT00000001", "field": "ecog"}]
    class Connection:
        def cursor(self, **kwargs):
            return Cursor()
    @contextmanager
    def connection():
        yield Connection()
    monkeypatch.setattr("clinova.optimize.edits.get_conn", connection)
    rows = load_observed_rules(fields=[ClinicalField.ecog], exclude_trial_id="NCT00000002")
    assert rows == [{"nct_id": "NCT00000001", "field": "ecog"}]
    assert captured["params"] == ("NCT00000002", "NCT00000002", ["ecog"], ["ecog"])
    assert "JOIN trials" in captured["sql"] and "unnest(t.conditions)" in captured["sql"]
    assert "^NCT[0-9]{8}$" in captured["sql"]
    assert "nsclc" in captured["sql"] and "r.nct_id <> %s" in captured["sql"]


def test_empty_field_loader_skips_database(monkeypatch):
    def unexpected_database():
        pytest.fail("queried the database for an empty field set")
    monkeypatch.setattr("clinova.optimize.edits.get_conn", unexpected_database)
    assert load_observed_rules(fields=[]) == []


def test_duplicate_or_nonchronological_visits_are_rejected():
    for visits, message in [([visit(1, 8), visit(1, 15)], "duplicate visit_no"),
                            ([visit(1, 15), visit(2, 8)], "chronological")]:
        p = protocol(visits=visits)
        with pytest.raises(ValueError, match=message):
            build_edit_space(p, observed_rows=[])
        with pytest.raises(ValueError, match=message):
            apply_protocol_edits(p, [])
