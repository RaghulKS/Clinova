"""T25 wiring tests: accept-a-fix (fast re-run), live backtest, drafts. Built against contracts-v1 with the
engine modules faked at the ``clinova.api.live`` seam. No network, no database."""

from __future__ import annotations

import time
from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from clinova.api import backtest as backtest_mod
from clinova.api import live
from clinova.api.audit import MEMORY
from clinova.api.main import app
from clinova.api.runs import RUNS
from clinova.api.schemas import AcceptResponse, RunRequest
from clinova.common.fixtures import load_fixture
from clinova.contracts import (
    BacktestResult,
    Funnel,
    RiskScore,
    RuleChange,
    SimRunSummary,
    TrialRecord,
    VersionEvent,
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("CLINOVA_API_NO_DB", "1")
    monkeypatch.setenv("CLINOVA_FIXTURE_DELAY", "0")
    MEMORY.clear()
    backtest_mod.clear_cache()
    live._DB_CHECK = None


@pytest.fixture
def client():
    return TestClient(app)


def finished_run(client, **body) -> str:
    run_id = client.post("/runs", json=body).json()["run_id"]
    end = time.time() + 60
    while time.time() < end:
        st = client.get(f"/runs/{run_id}").json()
        if st["stage"] in ("done", "failed"):
            assert st["stage"] == "done", st["message"]
            return run_id
        time.sleep(0.02)
    raise AssertionError("run did not finish")


# --- accept a fix ------------------------------------------------------------------------------------


def test_accept_on_fixtures_uses_optimizer_estimate(client):
    run_id = finished_run(client)
    res = client.get(f"/runs/{run_id}").json()["result"]
    fix = next(f for f in res["fixes"] if f["label"] == "fastest")
    r = client.post("/fixes/accept", json={"run_id": run_id, "fix_id": fix["fix_id"]})
    assert r.status_code == 200, r.text
    body = AcceptResponse.model_validate(r.json())
    assert body.basis == "optimizer_estimate" and body.source == "fixture"
    assert body.feedback.decision == "accept" and body.feedback.target_id == fix["fix_id"]
    # the edits were applied to the returned protocol
    assert next(x for x in body.protocol.rules if x.rule_id == "I12").value == 45
    assert body.after.amend_p is not None and body.before.amend_p is not None
    assert body.after.amend_p < body.before.amend_p  # fixture fix lowers amendment risk
    # decision is in the audit trail
    audit = client.get("/audit").json()
    assert [f["target_id"] for f in audit["feedback"]] == [fix["fix_id"]]


def test_accept_unknown_run_or_fix(client):
    assert client.post("/fixes/accept", json={"run_id": "nope", "fix_id": "x"}).status_code == 404
    run_id = finished_run(client)
    assert client.post("/fixes/accept", json={"run_id": run_id, "fix_id": "nope"}).status_code == 404


@contextmanager
def fake_engine(monkeypatch, calls: list[str]):
    """A tiny stand-in for the real cohort, screen, surrogate, score and diversity modules."""
    cohort = pd.DataFrame({"crcl_ml_min": np.linspace(30, 130, 200), "ecog": ([0, 1, 2, 3] * 50)})

    def make_cohort(n, seed, setting):
        calls.append("cohort")
        return cohort

    def screen(rules, df, trial_id):
        calls.append("screen")
        mask = np.ones(len(df), dtype=bool)
        for r in rules:
            if r.field.value == "crcl_ml_min" and r.operator.value == "ge":
                mask &= df["crcl_ml_min"].to_numpy() >= float(r.value)
        funnel = load_fixture("funnel.json", Funnel).model_copy(update={"n_eligible": int(mask.sum()), "eligible_frac": float(mask.mean())})
        return mask, funnel

    def surrogate(protocol, eligible_df):
        calls.append("surrogate")
        burden = sum(v.est_hours for v in protocol.visits)
        return 0.6 - 0.002 * burden, 0.10 + 0.004 * burden

    def score(protocol, sim=None, **_):
        calls.append("score")
        e = sum(s.eligible_frac for s in sim) / len(sim)
        base = load_fixture("risk_scores.json", list[RiskScore])
        return [r.model_copy(update={"p": float(max(0.0, min(1.0, r.p - (e - 0.25))))}) for r in base]

    monkeypatch.setattr(live, "make_cohort", make_cohort)
    monkeypatch.setattr(live, "screen", screen)
    monkeypatch.setattr(live, "surrogate_score", surrogate)
    monkeypatch.setattr(live, "diversity", lambda df: 0.5 + len(df) / 1000)
    monkeypatch.setattr(live, "score", score)
    yield


def test_accept_quick_rerun_changes_numbers_and_makes_no_llm_calls(client, monkeypatch):
    run_id = finished_run(client)  # fixture run (200 personas, fixture sims)
    res = client.get(f"/runs/{run_id}").json()["result"]
    fix = next(f for f in res["fixes"] if f["label"] == "fastest")

    llm_calls: list[str] = []
    import clinova.common.llm as llm

    for name in ("generate_json", "agenerate_json", "embed", "aembed"):
        monkeypatch.setattr(llm, name, lambda *a, _n=name, **k: llm_calls.append(_n))
    calls: list[str] = []
    with fake_engine(monkeypatch, calls):
        r = client.post("/fixes/accept", json={"run_id": run_id, "fix_id": fix["fix_id"]})
    assert r.status_code == 200, r.text
    body = AcceptResponse.model_validate(r.json())
    assert body.basis == "quick_rerun"
    assert llm_calls == []
    assert {"cohort", "screen", "surrogate", "score"} <= set(calls)
    # relaxing the CrCl cut-off widens the eligible pool, so risk drops and eligibility rises
    assert body.after.eligible_frac > body.before.eligible_frac
    assert body.after.accrual_fail_p < body.before.accrual_fail_p
    assert body.after.diversity_index is not None
    assert 0.0 <= body.after.dropout_rate <= 1.0 and 0.0 <= body.after.enroll_rate <= 1.0
    assert body.protocol.rules != RUNS.get(run_id).result.protocol.rules  # type: ignore[union-attr]


def test_accept_quick_rerun_falls_back_when_surrogate_is_a_stub(client, monkeypatch):
    run_id = finished_run(client)
    res = client.get(f"/runs/{run_id}").json()["result"]
    fix = res["fixes"][0]
    monkeypatch.setattr(live, "make_cohort", lambda n, s, g: pd.DataFrame({"x": [1, 2, 3]}))
    monkeypatch.setattr(live, "screen", lambda rules, df, t: (np.ones(len(df), dtype=bool), load_fixture("funnel.json", Funnel)))

    def stub(*a, **k):
        raise NotImplementedError("T14")

    monkeypatch.setattr(live, "surrogate_score", stub)
    r = client.post("/fixes/accept", json={"run_id": run_id, "fix_id": fix["fix_id"]})
    assert r.status_code == 200 and r.json()["basis"] == "optimizer_estimate"


def test_accept_of_a_locked_rule_is_422(client, monkeypatch):
    run_id = finished_run(client)
    result = RUNS.get(run_id).result  # type: ignore[union-attr]
    bad = result.fixes[0].model_copy(update={"edits": [result.fixes[0].edits[0].model_copy(update={"target_id": "E5", "new_value": False})]})
    # patch the stored run so it carries the bad fix
    st = RUNS._runs[run_id]
    RUNS._runs[run_id] = st.model_copy(update={"result": result.model_copy(update={"fixes": [bad]})})
    r = client.post("/fixes/accept", json={"run_id": run_id, "fix_id": bad.fix_id})
    assert r.status_code == 422 and "locked" in r.json()["detail"]


# --- backtest ----------------------------------------------------------------------------------------


def fake_trial() -> TrialRecord:
    t = load_fixture("trial_record.json", TrialRecord)
    return t


def test_live_backtest_uses_first_version_only_and_logs_backtest_context(client, monkeypatch, parsed_protocol):
    trial = fake_trial()
    seen: dict = {"first": [], "texts": []}

    def fake_first(t):
        seen["first"].append(t.eligibility_text_first)  # protocol_from_trial reads only the first version
        return parsed_protocol.model_copy(update={"trial_id": t.nct_id})

    def fake_parse_text(text, schedule, *, trial_id, phase):
        seen["texts"].append(text)
        return parsed_protocol.model_copy(update={"trial_id": trial_id})

    monkeypatch.setattr(live, "fetch_trial", lambda nct: trial)
    monkeypatch.setattr(live, "protocol_from_trial", fake_first)
    monkeypatch.setattr(live, "parse_text", fake_parse_text)
    monkeypatch.setattr(live, "read_version_events", lambda nct: load_fixture("version_events.json", list[VersionEvent]))
    monkeypatch.setattr(live, "diff_rules", lambda a, b: [RuleChange(field="crcl_ml_min", change="relaxed", old_rule_id="I12", new_rule_id="I12", old="ge 60", new="ge 45")] if isinstance(a, list) and isinstance(b, list) else 1 / 0)
    monkeypatch.setattr(live, "score", lambda p, sim=None, **k: load_fixture("risk_scores.json", list[RiskScore]))
    monkeypatch.setattr(live, "simulate", lambda *a: [load_fixture("sim_run_summary.json", SimRunSummary)])
    monkeypatch.setattr(live, "optimize", lambda *a: [])
    monkeypatch.setattr(live, "_need_db", lambda: None)
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(live, "record_prediction", lambda trial_id, risk, context="live": recorded.append((trial_id, context)))

    r = client.get(f"/trials/{trial.nct_id}/backtest")
    assert r.status_code == 200, r.text
    bt = BacktestResult.model_validate(r.json())
    assert bt.run is not None and bt.run.protocol.trial_id == trial.nct_id
    assert len(bt.events) >= 3 and [c.field.value for c in bt.changes] == ["crcl_ml_min"]
    # leakage rule: the first parse (the run) saw the first-version text; the current text is parsed only for the diff
    assert seen["first"] == [trial.eligibility_text_first]
    assert seen["texts"] == [trial.eligibility_text_current.strip()]  # parsed only for the reveal diff, never for the run
    assert recorded and all(ctx == "backtest" for tid, ctx in recorded)
    assert {tid for tid, _ in recorded} == {trial.nct_id}
    # second call is served from cache: no further parses
    assert client.get(f"/trials/{trial.nct_id}/backtest").status_code == 200
    assert len(seen["first"]) == 1 and len(seen["texts"]) == 1


def test_backtest_unknown_trial_in_database_is_404_unless_fixture(client, monkeypatch):
    def missing(nct):
        raise LookupError(nct)

    monkeypatch.setattr(live, "fetch_trial", missing)
    assert client.get("/trials/NCT12345678/backtest").status_code == 404
    # the fixture trial still works for the demo when the database lacks it
    r = client.get("/trials/NCT99999901/backtest")
    assert r.status_code == 200 and r.json()["source"] == "fixture"


def test_backtest_reports_a_failed_run(client, monkeypatch, parsed_protocol):
    monkeypatch.setattr(live, "fetch_trial", lambda nct: fake_trial())
    monkeypatch.setattr(live, "protocol_from_trial", lambda t: parsed_protocol)
    monkeypatch.setattr(
        "clinova.api.backtest.RUNS.submit",
        lambda req, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    r = client.get("/trials/NCT99999901/backtest")
    assert r.status_code == 500 and "boom" in r.json()["detail"]


def test_demo_mode_cache_miss_falls_back_to_fixture_for_that_stage(client, monkeypatch):
    from clinova.common.llm import BudgetExceeded, CacheMiss

    for exc in (CacheMiss("demo"), BudgetExceeded("spent")):
        def boom(*a, _e=exc, **k):
            raise _e

        monkeypatch.setattr(live, "simulate", boom)
        run_id = finished_run(client)
        st = client.get(f"/runs/{run_id}").json()
        assert st["source"] == "fixture" and "agents" in st["message"]


def test_fixture_fixes_are_never_attached_to_a_real_protocol(client, parsed_protocol):
    """Fixture flags and fixes name the fixture protocol's rule ids, so on another protocol they are left empty."""
    other = parsed_protocol.model_copy(update={"trial_id": "NCT03232892"})
    run_id = finished_run(client, protocol=other.model_dump(mode="json"))
    res = client.get(f"/runs/{run_id}").json()["result"]
    assert res["fixes"] == [] and res["flags"] == []
    assert client.post("/fixes/accept", json={"run_id": run_id, "fix_id": "x"}).status_code == 404


# --- drafts ------------------------------------------------------------------------------------------


def test_save_draft_without_database_is_false(parsed_protocol):
    assert live.save_draft("draft-1", "ECOG 0-1", None, parsed_protocol) is False


def test_save_draft_writes_the_drafts_table(monkeypatch, parsed_protocol):
    executed: list[tuple[str, tuple]] = []

    class Conn:
        def execute(self, sql, params=()):
            executed.append((sql, params))

    @contextmanager
    def fake_conn():
        yield Conn()

    monkeypatch.setattr(live, "db_available", lambda *a, **k: True)
    monkeypatch.setattr("clinova.common.db.get_conn", fake_conn)
    assert live.save_draft("draft-abc", "ECOG 0-1", "Day 1", parsed_protocol) is True
    sql, params = executed[0]
    assert "INSERT INTO drafts" in sql and params[0] == "draft-abc" and params[1] == "ECOG 0-1" and params[2] == "Day 1"


def test_pasted_text_run_saves_a_draft(client, monkeypatch, parsed_protocol):
    saved: list[str] = []
    monkeypatch.setattr(live, "parse_text", lambda text, sched, *, trial_id, phase: parsed_protocol.model_copy(update={"trial_id": trial_id}))
    monkeypatch.setattr(live, "save_draft", lambda draft_id, text, sched, parsed: saved.append(draft_id) or True)
    run_id = finished_run(client, eligibility_text="ECOG 0-1")
    assert len(saved) == 1 and saved[0].startswith("draft-")
    assert client.get(f"/runs/{run_id}").json()["result"]["protocol"]["trial_id"] == saved[0]
    r = client.post("/protocols/parse", json={"eligibility_text": "ECOG 0-1"})
    assert r.status_code == 200 and len(saved) == 2


def test_run_request_model_still_accepts_edits_only_runs():
    assert RunRequest(nct_id="NCT99999901").edits == []

