"""T32 presentation helpers: number formatting, reveal milestones, stall criterion, flag dollars, charts."""

from __future__ import annotations

from datetime import date

import httpx

from clinova.api import fallbacks
from clinova.app import charts, fmt
from clinova.app import reveal as rv
from clinova.app.client import _detail
from clinova.app.panels import before_after_rows, flag_dollars, stall_rule
from clinova.app.tabs import reliability_sentence, replay_sentence
from clinova.common.fixtures import load_fixture
from clinova.contracts import ReplayResult, ValidationMetrics


def test_fmt_house_rules():
    assert fmt.pct(0.229) == "22.9%" and fmt.pct(0.46) == "46%" and fmt.pct(0.12345) == "12.3%"
    assert fmt.pct(0.5, 3) == "50%"  # never more than one decimal
    assert fmt.signed_pts(0.0001) == "±0 pts"
    assert fmt.pts(0.094) == "9.4 points"
    assert fmt.nice_date(date(2017, 7, 25)) == "25 Jul 2017" and fmt.nice_date("2019-09-11") == "11 Sep 2019"
    assert fmt.risk_level(0.46) == ("High", "high") and fmt.risk_level(0.2)[0] == "Moderate" and fmt.risk_level(0.1)[0] == "Low"


def test_reveal_milestones_and_matches():
    bt = fallbacks.backtest("NCT99999901")
    assert bt is not None and bt.run is not None
    points = rv.milestones(bt)
    assert [p.when for p in points] == sorted(p.when for p in points)
    titles = [p.title for p in points]
    assert titles[0] == "First registered" and "Eligibility widened" in titles and "Terminated" in titles
    assert titles.count("Terminated") == 1  # the status event, not doubled by completion_date
    assert {p.kind for p in points if p.title in ("Eligibility widened", "Terminated")} == {"key"}
    flagged = [f.rule_id for f in bt.run.flags]
    assert rv.flagged_and_widened(bt, flagged)
    assert rv.flagged_and_widened(bt, []) == []
    line = rv.enrollment_line(bt)
    assert line is not None and line.startswith("Enrolled 61 of 180")


def test_condition_words():
    assert rv.condition_words("crcl_ml_min ge 60") == "crcl ml min >= 60"
    assert rv.condition_words("not cardiac_condition present") == "no cardiac condition"
    assert rv.condition_words("prior_platinum present") == "prior platinum required"
    assert rv.condition_words(None) == ""


def test_reveal_without_history_uses_trial_dates():
    bt = fallbacks.backtest("NCT99999901")
    assert bt is not None
    bare = bt.model_copy(update={"events": []})
    titles = [p.title for p in rv.milestones(bare)]
    assert "Terminated" in titles and not any(t.startswith("Eligibility") for t in titles)
    assert not rv.has_dated_widening(bare)


def test_stall_rule_and_flag_dollars():
    res = fallbacks.run_result()
    assert res.funnel is not None
    rid, why = stall_rule(res)
    assert why == "flag" and rid == sorted(res.flags, key=lambda f: f.rank)[0].rule_id
    rid2, why2 = stall_rule(res.model_copy(update={"flags": []}))
    assert why2 == "marginal" and rid2 == max(res.funnel.marginal, key=lambda m: m.excluded_n).rule_id
    for flag in res.flags:
        got = flag_dollars(flag, res.fixes)
        if got is not None:
            assert any(e.target_id == flag.rule_id for e in got[1].edits)
    assert flag_dollars(res.flags[0], []) is None


def test_waterfall_and_timeline_charts():
    res = fallbacks.run_result()
    assert res.funnel is not None
    fig = charts.funnel_waterfall(res.funnel, res.sim, "I12")
    assert list(fig.data[1].marker.color).count("#0B6E78") == 1
    assert list(fig.data[2].x)[0] == "Eligible"
    bt = fallbacks.backtest("NCT99999901")
    assert bt is not None
    points = rv.milestones(bt)
    assert len(charts.timeline(points).layout.annotations) == len(points)
    charts.timeline([])


def test_sentences_come_from_the_numbers():
    m = load_fixture("metrics.json", ValidationMetrics)
    s = reliability_sentence(m.targets[0])
    assert f"{m.targets[0].brier:.3f}" in s
    r = load_fixture("replay.json", ReplayResult)
    assert "months" in replay_sentence(r, "brier") and "months" in replay_sentence(r, "auc")


def test_before_after_change_column():
    from clinova.api.schemas import AcceptResponse, Measures

    resp = AcceptResponse.model_validate(
        {
            "feedback": {
                "feedback_id": "x",
                "target_type": "fix",
                "target_id": "f",
                "decision": "accept",
                "created_at": "2026-01-01T00:00:00Z",
            },
            "feedback_source": "fixture",
            "basis": "quick_rerun",
            "before": Measures(eligible_frac=0.2, accrual_fail_p=0.5).model_dump(),
            "after": Measures(eligible_frac=0.3, accrual_fail_p=0.4).model_dump(),
            "protocol": fallbacks.parsed_protocol().model_dump(mode="json"),
            "source": "live",
        }
    )
    rows = {r["Measure"]: r for r in before_after_rows(resp)}
    assert rows["Eligible virtual patients"]["Change"] == "+10 pts"
    assert rows["Accrual-failure risk"]["After"] == "40%"


def test_error_detail_is_plain():
    r = httpx.Response(422, json={"detail": [{"loc": ["body", "nct_id"], "msg": "bad id", "type": "x"}]})
    assert _detail(r) == "The request was not accepted (nct_id: bad id)."
    assert "HTTP 502" in _detail(httpx.Response(502, text="<html>Traceback...</html>"))
