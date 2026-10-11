"""Contract, property and measured performance checks for vectorized screening."""

from time import perf_counter

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from clinova.contracts import CriteriaRule, Edit
from clinova.simulate.screen import apply_edits, screen, screen_results


def rule(field="age", operator="ge", value=18, kind="inclusion", rule_id="I1", **kwargs):
    return CriteriaRule(rule_id=rule_id, kind=kind, field=field, operator=operator,
                        value=value, raw_text="Test rule", **kwargs)


def fixture_frame(personas):
    return pd.DataFrame([{**p.model_dump(exclude={"clinical"}), **p.clinical.model_dump()} for p in personas])


def test_golden_funnel(parsed_protocol, personas, funnel):
    frame = fixture_frame(personas)
    mask, actual = screen(parsed_protocol.rules, frame, trial_id=parsed_protocol.trial_id)
    assert actual.model_dump() == funnel.model_dump()
    assert mask.dtype == bool
    details = screen_results(parsed_protocol.rules, frame)
    assert np.array_equal(mask, [result.eligible for result in details])
    assert sum(result.eligible for result in details) == 56
    for result in details:
        assert result.first_failed_rule == (result.failed_rules[0] if result.failed_rules else None)
        assert "E6" not in result.failed_rules
    print(f"Golden fixture: {actual.n_eligible}/{actual.n_screened} = {actual.eligible_frac:.3f}")


@pytest.mark.parametrize("op, threshold, expected", [
    ("lt", 2, [True, False, False]), ("le", 2, [True, True, False]),
    ("gt", 2, [False, False, True]), ("ge", 2, [False, True, True]),
    ("eq", 2, [False, True, False]), ("ne", 2, [True, False, True]),
    ("in", [1, 3], [True, False, True]), ("not_in", [1, 3], [False, True, False]),
])
@pytest.mark.parametrize("kind", ["inclusion", "exclusion"])
def test_numeric_operators_and_missing(op, threshold, expected, kind):
    frame = pd.DataFrame({"age": pd.Series([1, 2, 3, None, pd.NA, np.nan], dtype="Float64")})
    mask, _ = screen([rule(operator=op, value=threshold, kind=kind)], frame)
    if kind == "exclusion":
        expected = [not value for value in expected]
    assert mask.tolist() == expected + [True] * 3


@pytest.mark.parametrize("op, value, expected", [
    ("present", None, [True, False]), ("absent", None, [False, True]),
    ("eq", True, [True, False]), ("eq", False, [False, True]),
    ("ne", True, [False, True]), ("in", [True], [True, False]),
    ("not_in", [True], [False, True]),
])
@pytest.mark.parametrize("kind", ["inclusion", "exclusion"])
def test_nullable_boolean_operators(op, value, expected, kind):
    frame = pd.DataFrame({"pregnancy": pd.Series([True, False, None], dtype="boolean")})
    mask, _ = screen([rule(field="pregnancy", operator=op, value=value, kind=kind)], frame)
    if kind == "exclusion":
        expected = [not value for value in expected]
    assert mask.tolist() == expected + [True]


@pytest.mark.parametrize("field, values, threshold, expected", [
    ("stage", ["I", "IIIA", "IIIB", "IIIC", "IVA", "IVB", None], "IIIB",
     [False, False, True, True, True, True, True]),
    ("brain_mets", ["none", "treated_stable", "untreated", None], "treated_stable",
     [False, True, True, True]),
])
def test_ordinal_order(field, values, threshold, expected):
    mask, _ = screen([rule(field=field, value=threshold)], pd.DataFrame({field: values}))
    assert mask.tolist() == expected


def test_categorical_membership_and_missing():
    frame = pd.DataFrame({"histology": ["squamous", "adenocarcinoma", None, pd.NA]})
    mask, _ = screen([rule(field="histology", operator="not_in", value=["squamous"])], frame)
    assert mask.tolist() == [False, True, True, True]


def test_skips_unparseable_without_accessing_columns():
    rules = [rule(field="other", operator="present", value=None, parse_status="unparseable"),
             rule(field="age", rule_id="I2", parse_status="unparseable")]
    mask, funnel = screen(rules, pd.DataFrame(index=range(3)))
    assert mask.tolist() == [True] * 3
    assert funnel.skipped_rules == ["I1", "I2"]
    assert len(funnel.stages) == 1
    assert funnel.marginal == []


def test_sequential_marginal_only_counts_and_row_alignment():
    frame = pd.DataFrame({"persona_id": ["a", "b", "c", "d"], "age": [17, 17, 40, 40],
                          "ecog": [0, 2, 2, 0]}, index=[9, 2, 7, 1])
    rules = [rule(), rule(field="ecog", operator="le", value=1, rule_id="I2")]
    mask, funnel = screen(rules, frame)
    assert mask.tolist() == [False, False, False, True]
    assert [stage.excluded for stage in funnel.stages] == [0, 2, 1]
    assert [(m.excluded_n, m.excluded_only_n) for m in funnel.marginal] == [(2, 1), (2, 1)]
    details = screen_results(rules, frame)
    assert [result.persona_id for result in details] == ["a", "b", "c", "d"]
    assert details[1].failed_rules == ["I1", "I2"]
    reverse_mask, reverse = screen(rules[::-1], frame)
    assert np.array_equal(mask, reverse_mask)
    assert [stage.rule_id for stage in reverse.stages] == [None, "I2", "I1"]


def test_empty_frame():
    mask, funnel = screen([rule()], pd.DataFrame({"age": pd.Series(dtype=float)}))
    assert len(mask) == 0
    assert funnel.eligible_frac == 0
    assert funnel.marginal[0].excluded_frac == 0
    assert screen_results([], pd.DataFrame({"persona_id": []})) == []


@given(st.lists(st.one_of(st.none(), st.integers(0, 120)), max_size=100))
def test_no_rules_everyone_eligible(ages):
    frame = pd.DataFrame({"persona_id": [str(i) for i in range(len(ages))], "age": ages})
    mask, funnel = screen([], frame)
    assert mask.tolist() == [True] * len(ages)
    assert funnel.n_eligible == len(ages)
    assert all(result.eligible for result in screen_results([], frame))


@given(ages=st.lists(st.one_of(st.none(), st.integers(0, 120)), max_size=100),
       threshold=st.integers(0, 120), increment=st.integers(0, 120),
       op=st.sampled_from(["lt", "le", "gt", "ge"]),
       kind=st.sampled_from(["inclusion", "exclusion"]))
def test_relaxing_threshold_never_shrinks_pool(ages, threshold, increment, op, kind):
    # Inclusion upper bounds rise and lower bounds fall; exclusion does the reverse.
    grows_upward = (op in ("lt", "le")) == (kind == "inclusion")
    new_threshold = threshold + increment if grows_upward else threshold - increment
    rules = [rule(operator=op, value=threshold, kind=kind)]
    edited = apply_edits(rules, [Edit(kind="relax_rule", target_id="I1", new_value=new_threshold)])
    frame = pd.DataFrame({"age": pd.Series(ages, dtype="Float64")})
    before, _ = screen(rules, frame)
    after, _ = screen(edited, frame)
    assert np.all(~before | after)


def test_apply_edits_deep_immutable_ignores_visits():
    rules = [rule(field="stage", operator="in", value=["IVA", "IVB"]),
             rule(rule_id="I2"), rule(rule_id="I3")]
    edits = [Edit(kind="make_remote", target_id="100"),
             Edit(kind="relax_rule", target_id="I1", new_value=["IIIB", "IVA", "IVB"]),
             Edit(kind="relax_rule", target_id="I2", new_operator="le", new_value=90),
             Edit(kind="drop_rule", target_id="I3")]
    original = [r.model_dump() for r in rules]
    result = apply_edits(rules, edits)
    assert len(result) == 2
    assert result[1].operator == "le"
    result[0].value.append("IIIC")
    assert [r.model_dump() for r in rules] == original
    assert edits[1].new_value == ["IIIB", "IVA", "IVB"]
    unchanged = apply_edits(rules, [])
    unchanged[0].value.append("IIIC")
    assert [r.model_dump() for r in rules] == original


@pytest.mark.parametrize("kind", ["relax_rule", "drop_rule"])
def test_locked_and_unknown_edits_raise(kind):
    for rules, target, match in [([rule(locked=True)], "I1", "locked"),
                                 ([rule()], "no-such-rule", "unknown")]:
        with pytest.raises(ValueError, match=match):
            apply_edits(rules, [Edit(kind=kind, target_id=target, new_value=30)])


def test_edit_batch_failure_does_not_mutate_input():
    rules = [rule()]
    with pytest.raises(ValueError, match="unknown"):
        apply_edits(rules, [Edit(kind="drop_rule", target_id="I1"),
                            Edit(kind="relax_rule", target_id="I1", new_value=20)])
    assert len(rules) == 1
    assert rules[0].value == 18


@pytest.mark.parametrize("bad_rule", [
    rule(operator="in", value=1), rule(operator="present", value=None),
    rule(field="stage", value="III"), rule(value="18"),
    rule(field="sex", operator="gt", value="male"),
])
def test_malformed_rules_raise(bad_rule):
    with pytest.raises(ValueError, match="rule I1"):
        screen([bad_rule], pd.DataFrame({"age": [20]}))


def test_unknown_ordinal_and_missing_column_raise():
    with pytest.raises(ValueError, match="unknown ordinal"):
        screen([rule(field="stage", value="IIIB")], pd.DataFrame({"stage": ["III"]}))
    with pytest.raises(ValueError, match="missing field"):
        screen([rule()], pd.DataFrame(index=[0]))
    with pytest.raises(ValueError, match="duplicate"):
        screen([rule(), rule()], pd.DataFrame({"age": [18]}))


def test_10k_25_rule_benchmark(parsed_protocol, personas):
    frame = pd.concat([fixture_frame(personas)] * 50, ignore_index=True)
    rules = [r for r in parsed_protocol.rules if r.parse_status == "ok"]
    rules += [rule(field="brain_mets", operator="le", value="treated_stable", rule_id="I17"),
              rule(field="stage", value="IIIB", rule_id="I18"),
              rule(field="histology", operator="not_in", value=["other"], rule_id="I19"),
              rule(field="sex", operator="in", value=["female", "male"], rule_id="I20")]
    assert len(frame) == 10_000 and len(rules) == 25
    screen(rules, frame)  # warm imports/caches before timing the actual public API
    timings = []
    for _ in range(7):
        start = perf_counter()
        screen(rules, frame)
        timings.append((perf_counter() - start) * 1000)
    elapsed_ms = float(np.median(timings))
    print(f"screen 10,000 x 25: median={elapsed_ms:.3f} ms; max={max(timings):.3f} ms")
    assert elapsed_ms < 50
