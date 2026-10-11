"""Hand-computed readiness checks that supplement the module-level fixture tests."""

import socket
from types import SimpleNamespace

import numpy as np
import pandas as pd
import psycopg
import pytest
from pydantic import ValidationError

from clinova.contracts import ClinicalField, CriteriaRule, Operator, ParsedProtocol
from clinova.model import evaluate, features, predict
from clinova.parse import parser
from clinova.parse.diff import diff_rules


def test_network_guard_allows_local_event_loop_wakeup_pipe():
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"local")
        assert right.recv(5) == b"local"


@pytest.mark.parametrize("attempt", [
    lambda: socket.getaddrinfo("127.0.0.1", 1),
    lambda: socket.gethostbyname("localhost"),
    lambda: socket.gethostbyname_ex("localhost"),
    lambda: socket.gethostbyaddr("127.0.0.1"),
    lambda: psycopg.connect("postgresql://test:test@127.0.0.1:1/test"),
    lambda: psycopg.Connection.connect("postgresql://test:test@127.0.0.1:1/test"),
])
def test_network_guard_blocks_resolution_and_native_database_connections(no_network, attempt):
    with pytest.raises(AssertionError, match="Tests must use fixtures"):
        attempt()
    assert no_network.pop() == "network"


@pytest.mark.parametrize("method", [m for m in ("connect", "connect_ex", "sendto", "sendmsg")
                                    if hasattr(socket.socket, m)])
@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
def test_network_guard_blocks_internet_sockets(no_network, method, family):
    with socket.socket(family) as sock, pytest.raises(AssertionError, match="Tests must use fixtures"):
        if method == "sendto":
            sock.sendto(b"offline", ("127.0.0.1", 1))
        elif method == "sendmsg":
            sock.sendmsg([b"offline"], [], 0, ("127.0.0.1", 1))
        else:
            getattr(sock, method)(("127.0.0.1", 1))
    assert no_network.pop() == "network"


@pytest.mark.parametrize(
    ("scores", "expected"),
    [([0.1, 0.4, 0.35, 0.8], 3 / 4), ([0.2, 0.6, 0.6, 0.9], 7 / 8),
     ([0.5] * 4, 1 / 2), ([1, 1, 0, 0], 0)],
)
def test_auc_counts_positive_negative_pairs_including_ties(scores, expected):
    result = evaluate.auc_ci(np.array([0, 0, 1, 1]), np.array(scores), n_boot=128)
    assert result.value == pytest.approx(expected)
    assert 0 <= result.ci_low <= result.value <= result.ci_high <= 1


def test_paired_auc_difference_uses_the_same_trials():
    y = np.array([0, 0, 1, 1])
    result = evaluate.paired_diff_ci(y, y.astype(float), 1 - y.astype(float), n_boot=64)
    assert result.value == result.ci_low == result.ci_high == 1


def test_target_metrics_brier_is_mean_squared_probability_error(monkeypatch):
    rows = pd.DataFrame({
        "label_accrual_fail": [0, 0, 1, 1, None],
        "split": ["test"] * 5,
        "start_date": ["2020-01-01"] * 5,
    }, index=["a", "b", "c", "d", "unlabeled"])
    seen = []

    def probabilities(bundle, target, labeled):
        seen.append(labeled.index.tolist())
        return np.array([0.1, 0.4, 0.35, 0.8])

    monkeypatch.setattr(evaluate, "predict_p", probabilities)
    monkeypatch.setattr(evaluate, "baseline_proba", lambda *args: np.full(4, 0.5))
    auc_ci = evaluate.auc_ci
    monkeypatch.setattr(evaluate, "auc_ci", lambda y, p: auc_ci(y, p, n_boot=64))
    metrics, note, predictions = evaluate.target_metrics(
        {"models": {"accrual_fail": object()}, "version": "hand-computed"}, rows, "accrual_fail",
    )
    assert note is None and metrics is not None
    assert seen == [["a", "b", "c", "d"]]
    assert (metrics.n_test, metrics.n_pos) == (4, 2)
    assert metrics.auc.value == pytest.approx(3 / 4)
    assert metrics.brier == pytest.approx((0.01 + 0.16 + 0.4225 + 0.04) / 4)
    assert metrics.baseline_auc.value == 0.5 and metrics.baseline_brier == 0.25
    assert predictions["nct_id"].tolist() == ["a", "b", "c", "d"]


def test_reliability_includes_endpoints_and_keeps_empty_bins_missing():
    bins = evaluate.reliability(np.array([0, 1, 0, 1]), np.array([0, 0.25, 0.5, 1]), bins=4)
    assert [(b.bin_low, b.bin_high, b.n) for b in bins] == [
        (0, 0.25, 2), (0.25, 0.5, 1), (0.5, 0.75, 0), (0.75, 1, 1),
    ]
    assert [b.mean_pred for b in bins] == [0.125, 0.5, None, 1]
    assert [b.frac_pos for b in bins] == [0.5, 0, None, 1]


def _rule(rule_id, field, operator, value, **kwargs):
    return CriteriaRule(rule_id=rule_id, kind="inclusion", field=field,
                        operator=operator, value=value, raw_text="fixture", **kwargs)


def test_hit_rate_and_random_baseline_have_hand_computed_denominators(monkeypatch):
    rules = [_rule("I1", "age", "ge", 18), _rule("I2", "ecog", "le", 1),
             _rule("I3", "crcl_ml_min", "ge", 60), _rule("I4", "hemoglobin", "ge", 9)]
    first = {nid: ParsedProtocol(trial_id=nid, rules=rules) for nid in ("hit", "miss")}
    amended = {
        "hit": ParsedProtocol(trial_id="hit", rules=[
            r.model_copy(update={"value": 2}) if r.field == ClinicalField.ecog else r for r in rules]),
        "miss": ParsedProtocol(trial_id="miss", rules=[
            r.model_copy(update={"value": 45}) if r.field == ClinicalField.crcl_ml_min else r for r in rules]),
    }
    monkeypatch.setattr(features, "load_protocols", lambda ids, version: amended if version else first)
    monkeypatch.setattr(features, "screen_cohort", lambda: pd.DataFrame())
    monkeypatch.setattr(predict, "flag_criteria", lambda *a, **k: [
        SimpleNamespace(field=ClinicalField.age), SimpleNamespace(field=ClinicalField.ecog)])
    monkeypatch.setattr("clinova.simulate.screen.screen", lambda *a: (None, SimpleNamespace(marginal=[
        SimpleNamespace(field=ClinicalField.crcl_ml_min), SimpleNamespace(field=ClinicalField.ecog)])))
    rows = pd.DataFrame({"split": ["test", "test", "train", "test"],
                         "label_amend": [True, True, True, False]},
                        index=["hit", "miss", "train-amended", "not-amended"])
    result, _, denominator = evaluate.hit_rate({}, rows, k=2)
    assert result is not None and result.n_trials == 2
    assert result.hit_rate == 1 / 2 and result.marginal_baseline == 1
    # Two random flags among four rules hit the one widened field in 3 of 6 combinations.
    assert result.random_baseline == 3 / 6
    assert (denominator["n_amended_test"], denominator["n_evaluated"], denominator["hits"]) == (2, 2, 1)


@pytest.mark.parametrize("bad", [
    {"setting": "unknown", "rules": []},
    {"setting": "any", "rules": [], "invented": True},
    {"setting": "any", "rules": [{"kind": "inclusion", "field": "invented", "operator": "ge",
                                   "value_json": "18", "raw_text": "18+"}]},
])
def test_parser_schema_rejects_unknown_vocabulary_and_extra_fields(bad):
    with pytest.raises(ValidationError):
        parser.ParseOut.model_validate(bad)


@pytest.mark.parametrize("value_json", ["not JSON", "true", '"60"', "[]", "null"])
def test_parser_downgrades_invalid_numeric_values_without_losing_source(value_json):
    out = parser.ParseOut(setting="any", rules=[parser._RuleOut(
        kind="inclusion", field="crcl_ml_min", operator="ge", value_json=value_json,
        raw_text="Source text with an invalid threshold",
    )])
    rule, = parser._convert(out)
    assert rule.field == ClinicalField.other and rule.parse_status == "unparseable"
    assert rule.note and rule.value is None and rule.raw_text == out.rules[0].raw_text


def test_rule_diff_uses_combined_bounds_and_ignores_renumbering():
    old = [_rule("I1", "age", "ge", 18), _rule("I2", "age", "le", 75)]
    new = [_rule("I8", "age", "ge", 18), _rule("I9", "age", "le", 80)]
    change, = diff_rules(old, new)
    assert (change.field, change.change, change.old_rule_id, change.new_rule_id) == (
        ClinicalField.age, "relaxed", "I1", "I8",
    )
    assert diff_rules(old, [r.model_copy(update={"rule_id": "I7"}) for r in old]) == []


def test_rule_diff_recognizes_equivalent_ordinal_rules_and_skips_unparseable():
    old = [_rule("I1", "stage", "ge", "IIIB")]
    new = [_rule("I8", "stage", "in", ["IIIB", "IIIC", "IVA", "IVB"]),
           _rule("I9", "other", "eq", None, parse_status="unparseable", note="vague")]
    assert diff_rules(old, new) == []


def test_rule_diff_detects_a_strict_threshold_boundary():
    old = [_rule("I1", "crcl_ml_min", Operator.ge, 60)]
    new = [_rule("I1", "crcl_ml_min", Operator.gt, 60)]
    change, = diff_rules(old, new)
    assert change.change == "tightened"
