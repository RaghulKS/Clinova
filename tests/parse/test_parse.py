"""T12 tests: no network, no DB. The LLM is monkeypatched."""

import json
from pathlib import Path

from clinova.common.fixtures import load_fixture
from clinova.contracts import ClinicalField, CriteriaRule, Operator, ParsedProtocol, Phase, TrialRecord
from clinova.parse import evaluate, parser
from clinova.parse.diff import diff_rules
from clinova.parse.visits import estimate_visits

R = parser._RuleOut


def _out(*rules, setting="any"):
    return parser.ParseOut(setting=setting, rules=list(rules))


def test_sanity_checks():
    f, o = ClinicalField, Operator
    assert parser.sanity(f.ecog, o.le, 1) is None
    assert "range" in parser.sanity(f.ecog, o.le, 9)
    assert "invalid" in parser.sanity(f.ecog, o.present, None)
    assert "vocabulary" in parser.sanity(f.stage, o.in_, ["IIIB", "V"])
    assert parser.sanity(f.autoimmune_disease, o.present, None) is None
    assert "disagree" in parser.sanity(f.stage, o.in_, "IVA")


def test_convert_flags_locks_and_renumbers():
    out = _out(
        R(kind="exclusion", field="pregnancy", operator="present", value_json="null", raw_text="pregnant"),
        R(kind="inclusion", field="ecog", operator="le", value_json="1", unit="score", raw_text="ECOG 0-1"),
        R(kind="inclusion", field="ecog", operator="le", value_json="11", raw_text="bad"),
        R(kind="inclusion", field="other", operator="eq", value_json="null", raw_text="adequate organ function",
          unparseable_reason="vague"),
    )
    rules = parser._convert(out)
    assert [r.rule_id for r in rules] == ["I1", "I2", "I3", "E1"]
    assert rules[0].unit == "score" and rules[0].parse_status == "ok"
    assert rules[1].parse_status == "unparseable" and "range" in rules[1].note
    assert rules[2].field == ClinicalField.other and rules[2].note == "vague"
    assert rules[3].locked and rules[3].parse_status == "ok"
    ParsedProtocol(trial_id="x", rules=rules)


def test_parse_protocol_uses_llm(monkeypatch):
    monkeypatch.setattr(
        parser.llm, "generate_json",
        lambda *a, **k: _out(R(kind="inclusion", field="age", operator="ge", value_json="18", raw_text="18+"),
                             setting="first_line"),
    )
    p = parser.parse_protocol("Age >= 18", trial_id="NCT00000001", phase=Phase.phase3)
    assert p.setting == "first_line" and p.rules[0].value == 18 and p.rules[0].unit == "years"


def _r(i, kind, field, op, value=None):
    return CriteriaRule(rule_id=i, kind=kind, field=field, operator=op, value=value, raw_text="x")


def test_diff_rules():
    f, o = ClinicalField, Operator
    old = [_r("I1", "inclusion", f.ecog, o.le, 1), _r("I2", "inclusion", f.crcl_ml_min, o.ge, 60),
           _r("E1", "exclusion", f.brain_mets, o.in_, ["treated_stable", "untreated"]),
           _r("E2", "exclusion", f.autoimmune_disease, o.present), _r("I3", "inclusion", f.age, o.ge, 18)]
    new = [_r("I1", "inclusion", f.ecog, o.le, 2), _r("I2", "inclusion", f.crcl_ml_min, o.ge, 50),
           _r("E1", "exclusion", f.brain_mets, o.in_, ["untreated"]),
           _r("I3", "inclusion", f.age, o.ge, 18), _r("I4", "inclusion", f.measurable_disease, o.present),
           _r("I5", "inclusion", f.prior_lines, o.le, 1)]
    got = {c.field: c.change for c in diff_rules(old, new)}
    assert got == {f.ecog: "relaxed", f.crcl_ml_min: "relaxed", f.brain_mets: "relaxed",
                   f.autoimmune_disease: "removed", f.measurable_disease: "added", f.prior_lines: "added"}
    assert diff_rules(new, old)[0].change == "tightened"
    assert diff_rules(old, old) == []


def test_estimate_visits_from_fixture():
    t = load_fixture("trial_record.json", TrialRecord)
    vs = estimate_visits(t)
    assert vs and all(v.estimated for v in vs) and vs[0].day < 0
    assert [v.visit_no for v in vs] == list(range(1, len(vs) + 1))
    assert max(v.day for v in vs) <= 365 and any("ct_scan" in v.procedures for v in vs[1:])


def test_gold_set_and_scorer():
    gold = json.loads(evaluate.GOLD.read_text(encoding="utf-8"))
    assert len(gold) == 12
    for g in gold:  # gold annotations are themselves valid rules
        for r in g["rules"]:
            assert r["field"] in ClinicalField._value2member_map_
    perfect = evaluate.score(gold[0]["rules"], gold[0]["rules"])
    assert perfect["precision"] == perfect["recall"] == 1.0
    assert perfect["strict_value_correct"] == perfect["strict_value_total"]
    # equivalences: eq x == in [x]; stage ge IIIB == in [IIIB..IVB]; present == eq true
    g = [{"kind": "inclusion", "field": "stage", "operator": "ge", "value": "IIIB"}]
    p = [{"kind": "inclusion", "field": "stage", "operator": "in", "value": ["IIIB", "IIIC", "IVA", "IVB"]}]
    assert evaluate.score(g, p)["strict_value_correct"] == 1


def test_schema_json_is_current():
    schema = Path(parser.__file__).with_name("schema.json")
    assert json.loads(schema.read_text(encoding="utf-8")) == parser.ParseOut.model_json_schema()
