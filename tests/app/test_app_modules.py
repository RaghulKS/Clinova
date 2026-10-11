"""Tests for the app's pure modules: rules table, charts, wording helpers and the API client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clinova.api.main import app
from clinova.api.schemas import FeedbackIn
from clinova.app import charts
from clinova.app.client import ApiClient, ApiError
from clinova.app.panels import edit_in_words, money, signed_pct, signed_pts
from clinova.app.rules_table import df_to_rules, rules_to_df, text_to_value, value_to_text
from clinova.app.tabs import validation_sentences
from clinova.common.fixtures import load_fixture
from clinova.contracts import (
    Edit,
    Funnel,
    ReplayResult,
    SimRunSummary,
    ValidationMetrics,
)


def test_value_text_round_trip():
    assert value_to_text(None) == "" and value_to_text(60.0) == "60" and value_to_text(1.5) == "1.5"
    assert value_to_text(["IIIB", "IV"]) == "IIIB, IV" and value_to_text(True) == "true"
    assert text_to_value("60", ">=") == 60 and text_to_value("1.5", "<=") == 1.5
    assert text_to_value("IIIB, IV", "in") == ["IIIB", "IV"]
    assert text_to_value("anything", "present") is None
    assert text_to_value("", "=") is None
    assert text_to_value("true", "=") is True


def test_rules_table_round_trip_is_lossless(parsed_protocol):
    df = rules_to_df(parsed_protocol.rules)
    assert list(df["Status"]).count("Needs review") == 1
    rules, problems = df_to_rules(df, parsed_protocol.rules)
    assert problems == []
    assert [r.model_dump() for r in rules] == [r.model_dump() for r in parsed_protocol.rules]


def test_rules_table_edit_and_lock(parsed_protocol):
    df = rules_to_df(parsed_protocol.rules)
    i = df.index[df["ID"] == "I12"][0]
    df.loc[i, "Value"] = "45"
    df.loc[i, "Lock"] = True
    rules, problems = df_to_rules(df, parsed_protocol.rules)
    r = next(r for r in rules if r.rule_id == "I12")
    assert problems == [] and r.value == 45 and r.locked is True
    # the original is untouched
    assert next(x for x in parsed_protocol.rules if x.rule_id == "I12").value == 60


def test_rules_table_bad_row_is_reported(parsed_protocol):
    df = rules_to_df(parsed_protocol.rules)
    i = df.index[df["ID"] == "I7"][0]
    df.loc[i, "Field"] = "not_a_field"
    rules, problems = df_to_rules(df, parsed_protocol.rules)
    assert len(problems) == 1 and problems[0].startswith("I7")
    assert next(r for r in rules if r.rule_id == "I7").field.value == "ecog"


def test_unparseable_row_stays_flagged_until_fixed(parsed_protocol):
    df = rules_to_df(parsed_protocol.rules)
    rules, _ = df_to_rules(df, parsed_protocol.rules)
    assert next(r for r in rules if r.rule_id == "E6").parse_status == "unparseable"
    i = df.index[df["ID"] == "E6"][0]
    df.loc[i, "Field"] = "active_infection"
    rules, problems = df_to_rules(df, parsed_protocol.rules)
    assert problems == [] and next(r for r in rules if r.rule_id == "E6").parse_status == "ok"


def test_charts_build_from_fixtures():
    funnel = load_fixture("funnel.json", Funnel)
    sim = load_fixture("sim_run_summary.json", SimRunSummary)
    fig, steps = charts.funnel_steps(funnel, [sim])
    assert [s[0] for s in steps] == ["Screened", "Eligible", "Would enroll", "Would complete"]
    assert steps[1][1] == 56 and steps[2][1] == round(56 * sim.enroll_rate)
    fig = charts.exclusions(funnel)
    colors = list(fig.data[0].marker.color)
    assert colors[-1] == "#0B6E78" and colors.count("#0B6E78") == 1  # only the biggest exclusion is accented
    assert fig.data[0].y[-1].startswith("I12")
    charts.dropout_curve(sim)
    m = load_fixture("metrics.json", ValidationMetrics)
    charts.auc_compare(m)
    charts.reliability(m)
    assert charts.hit_rate(m) is not None
    charts.replay(load_fixture("replay.json", ReplayResult), "auc")


def test_wording_helpers(parsed_protocol):
    assert signed_pct(0.384) == "+38%" and signed_pts(-0.094) == "-9 pts" and money(19740) == "$19,740"
    txt = edit_in_words(Edit(kind="relax_rule", target_id="I12", new_value=45), parsed_protocol)
    assert txt.startswith("Relax I12") and "45" in txt
    assert edit_in_words(Edit(kind="drop_rule", target_id="E1"), parsed_protocol).startswith("Drop E1")
    assert "remotely" in edit_in_words(Edit(kind="make_remote", target_id="3"), parsed_protocol)


def test_validation_sentences_use_the_numbers():
    m = load_fixture("metrics.json", ValidationMetrics)
    sentences = validation_sentences(m)
    assert len(sentences) == len(m.targets)
    assert f"{m.targets[0].auc.value:.2f}" in sentences[0]


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("CLINOVA_API_NO_DB", "1")
    monkeypatch.setenv("CLINOVA_FIXTURE_DELAY", "0")
    c = ApiClient("http://testserver")
    c._http = TestClient(app, base_url="http://testserver")
    return c


def test_client_end_to_end_on_fixtures(api_client, parsed_protocol):
    assert api_client.health()["status"] == "ok"
    parsed = api_client.parse(nct_id="NCT99999901")
    assert parsed.source.value == "fixture"
    created = api_client.start_run(parsed.protocol)
    final = api_client.wait_for_run(created.run_id, poll_s=0.01, timeout_s=60)
    assert final.stage == "done" and final.result is not None
    fb = api_client.feedback(
        FeedbackIn(target_type="fix", target_id=final.result.fixes[0].fix_id, decision="accept")
    )
    assert fb.feedback.decision == "accept"
    assert api_client.audit().feedback
    assert api_client.validation().metrics.targets and api_client.replay().replay.months
    assert api_client.pulse("NCT99999901").status in ("not_run", "available")
    assert api_client.backtest("NCT99999901").events


def test_client_errors_are_plain(api_client):
    with pytest.raises(ApiError):
        api_client.backtest("NCT12345678")
    dead = ApiClient("http://127.0.0.1:1")
    with pytest.raises(ApiError, match="Cannot reach"):
        dead.health()


def test_accept_fix_through_the_client_and_before_after_rows(api_client, parsed_protocol):
    from clinova.app.panels import before_after_rows

    created = api_client.start_run(parsed_protocol)
    final = api_client.wait_for_run(created.run_id, poll_s=0.01, timeout_s=60)
    assert final.result is not None
    fix = final.result.fixes[0]
    resp = api_client.accept_fix(final.run_id, fix.fix_id)
    assert resp.feedback.decision == "accept"
    rows = before_after_rows(resp)
    assert {"Measure", "Before", "After", "Basis"} <= set(rows[0])
    assert any(r["Measure"] == "Amendment risk" for r in rows)
    assert all(r["Basis"] == "optimizer estimate" for r in rows)  # fixture engine: never presented as a re-run
    with pytest.raises(ApiError):
        api_client.accept_fix("nope", fix.fix_id)
