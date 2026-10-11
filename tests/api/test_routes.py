"""TestClient tests for every API route, on fixtures. No network, no database."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from clinova.api import live
from clinova.api.audit import MEMORY
from clinova.api.main import app
from clinova.api.runs import RUNS, apply_edits_local
from clinova.api.schemas import RunRequest
from clinova.contracts import (
    BacktestResult,
    Edit,
    ParsedProtocol,
    ReplayResult,
    RunStatus,
    ValidationMetrics,
)
from tests.conftest import FIXTURES, load_fixture


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("CLINOVA_API_NO_DB", "1")
    monkeypatch.setenv("CLINOVA_FIXTURE_DELAY", "0")
    MEMORY.clear()
    live._DB_CHECK = None


@pytest.fixture
def client():
    return TestClient(app)


def wait_done(client: TestClient, run_id: str, timeout: float = 90.0) -> RunStatus:
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/runs/{run_id}")
        assert r.status_code == 200
        st = RunStatus.model_validate(r.json())
        if st.stage in ("done", "failed"):
            return st
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["contracts_version"] == "contracts-v1"
    assert body["db"] is False


def test_parse_pasted_text_falls_back_to_fixture(client):
    text = (FIXTURES / "protocol_2L_eligibility.txt").read_text()
    r = client.post("/protocols/parse", json={"eligibility_text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fixture"
    protocol = ParsedProtocol.model_validate(body["protocol"])
    assert len(protocol.rules) == 22 and protocol.unparseable == ["E6"]


def test_parse_by_nct_fixture_trial(client):
    r = client.post("/protocols/parse", json={"nct_id": "NCT99999901"})
    assert r.status_code == 200
    assert r.json()["source"] == "fixture"
    assert r.json()["registered_on"] is not None


def test_parse_unknown_nct_is_404(client):
    assert client.post("/protocols/parse", json={"nct_id": "NCT12345678"}).status_code == 404


def test_parse_needs_input(client):
    assert client.post("/protocols/parse", json={}).status_code == 422


def test_parse_uses_real_parser_when_available(client, monkeypatch, parsed_protocol):
    monkeypatch.setattr("clinova.parse.parser.parse_protocol", lambda *a, **k: parsed_protocol)
    monkeypatch.setattr(live, "_need_db", lambda: None)
    r = client.post("/protocols/parse", json={"eligibility_text": "ECOG 0-1"})
    assert r.json()["source"] == "live"


def test_run_lifecycle_on_fixtures(client):
    r = client.post("/runs", json={})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    st = wait_done(client, run_id)
    assert st.stage == "done" and st.progress == 1.0
    assert st.source == "fixture"
    assert st.stages_done == ["parse", "cohort", "screen", "agents", "score", "flags", "optimize"]
    assert st.result is not None
    assert len(st.result.risks) == 3 and len(st.result.fixes) == 3 and len(st.result.flags) == 3
    # G1: cohort and screen are real modules now (no LLM, no DB), so the funnel is live.
    assert st.result.funnel is not None and st.result.funnel.n_screened > 0
    assert "Fixture" in (st.message or "")
    # fixture scores are logged in the in-memory ledger
    assert len(client.get("/audit").json()["predictions"]) == 3


def test_run_with_reviewed_protocol_keeps_it(client, parsed_protocol):
    edited = parsed_protocol.model_copy(
        update={"rules": [r.model_copy(update={"locked": True}) if r.rule_id == "I7" else r for r in parsed_protocol.rules]}
    )
    run_id = client.post("/runs", json={"protocol": edited.model_dump(mode="json")}).json()["run_id"]
    st = wait_done(client, run_id)
    assert st.result is not None
    assert next(r for r in st.result.protocol.rules if r.rule_id == "I7").locked is True


def test_run_with_edits_applies_them(client, parsed_protocol):
    body = {
        "protocol": parsed_protocol.model_dump(mode="json"),
        "edits": [{"kind": "relax_rule", "target_id": "I12", "new_value": 45}],
    }
    st = wait_done(client, client.post("/runs", json=body).json()["run_id"])
    assert st.result is not None
    assert next(r for r in st.result.protocol.rules if r.rule_id == "I12").value == 45


def test_run_failure_is_reported(client, parsed_protocol):
    body = {
        "protocol": parsed_protocol.model_dump(mode="json"),
        "edits": [{"kind": "relax_rule", "target_id": "E5", "new_value": False}],  # E5 is locked
    }
    st = wait_done(client, client.post("/runs", json=body).json()["run_id"])
    assert st.stage == "failed" and "locked" in (st.message or "")


def test_run_unknown_nct_fails_cleanly(client):
    st = wait_done(client, client.post("/runs", json={"nct_id": "NCT12345678"}).json()["run_id"])
    assert st.stage == "failed"


def test_get_unknown_run_404(client):
    assert client.get("/runs/nope").status_code == 404


def test_run_uses_real_modules_when_present(client, monkeypatch, parsed_protocol):
    """A module that is implemented wins over the fixture, and the run is then tagged live for that stage."""
    fixture_risks = load_fixture("risk_scores.json")
    from clinova.contracts import RiskScore

    real = [RiskScore.model_validate(x) for x in fixture_risks]
    try:
        import clinova.model.predict  # noqa: F401
    except OSError as exc:  # LightGBM's native library (libomp) cannot load on this machine
        pytest.skip(f"clinova.model.predict cannot load here: {type(exc).__name__}")
    monkeypatch.setattr("clinova.model.predict.score", lambda protocol, **k: real)
    st = wait_done(client, client.post("/runs", json={}).json()["run_id"])
    assert st.stage == "done"
    assert "score" not in (st.message or "")


def test_backtest_fixture(client):
    r = client.get("/trials/NCT99999901/backtest")
    assert r.status_code == 200
    bt = BacktestResult.model_validate(r.json())
    assert bt.source == "fixture" and bt.run is not None and len(bt.events) >= 3 and bt.changes
    assert client.get("/trials/NCT12345678/backtest").status_code == 404


def test_optimize_fixture(client, parsed_protocol):
    r = client.post("/optimize", json={"protocol": parsed_protocol.model_dump(mode="json")})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fixture"
    assert {f["label"] for f in body["fixes"]} == {"fastest", "lowest_dropout", "balanced"}


def test_feedback_then_audit(client):
    body = {"target_type": "fix", "target_id": "FIXTURE-2L-001-fix-balanced", "trial_id": "FIXTURE-2L-001", "decision": "accept"}
    r = client.post("/feedback", json=body)
    assert r.status_code == 200
    assert r.json()["source"] == "fixture" and r.json()["feedback"]["decision"] == "accept"
    a = client.get("/audit").json()
    assert a["source"] == "fixture"
    assert [f["target_id"] for f in a["feedback"]] == ["FIXTURE-2L-001-fix-balanced"]


def test_feedback_validation(client):
    bad = {"target_type": "fix", "target_id": "x", "decision": "rate", "rating": 9}
    assert client.post("/feedback", json=bad).status_code == 422
    assert client.post("/feedback", json={"target_type": "nope", "target_id": "x", "decision": "accept"}).status_code == 422


def test_validation_and_replay_fall_back_to_fixture(client, monkeypatch):
    monkeypatch.setattr("clinova.api.main._doc_file", lambda rel: None)
    v = client.get("/validation").json()
    assert v["source"] == "fixture"
    assert ValidationMetrics.model_validate(v["metrics"]).targets
    rp = client.get("/replay").json()
    assert rp["source"] == "fixture"
    assert ReplayResult.model_validate(rp["replay"]).months


def test_validation_reads_real_file(client, monkeypatch):
    monkeypatch.setattr("clinova.api.main._doc_file", lambda rel: FIXTURES / rel.split("/")[-1])
    assert client.get("/validation").json()["source"] == "live"
    assert client.get("/replay").json()["source"] == "live"


def test_pulse_not_run_and_available(client, monkeypatch, tmp_path):
    r = client.get("/pulse/NCT99999901")
    assert r.status_code == 200 and r.json()["status"] == "not_run"
    f = tmp_path / "NCT99999901.md"
    f.write_text("# Pulse\nPatients worry about travel.")
    monkeypatch.setattr("clinova.api.main._doc_file", lambda rel: f)
    r = client.get("/pulse/NCT99999901").json()
    assert r["status"] == "available" and "travel" in r["report_md"] and r["source"] == "cache"


def test_pulse_rejects_path_tricks(client):
    assert client.get("/pulse/..%2Fsecret").status_code in (404, 422)


def test_edits_helper_handles_visits(parsed_protocol):
    n = len(parsed_protocol.visits)
    p = apply_edits_local(parsed_protocol, [Edit(kind="cut_visit", target_id="2")])
    assert len(p.visits) == n - 1 and [v.visit_no for v in p.visits] == list(range(1, n))
    p = apply_edits_local(parsed_protocol, [Edit(kind="make_remote", target_id="3")])
    assert p.visits[2].requires_travel is False and parsed_protocol.visits[2].requires_travel is True
    p = apply_edits_local(parsed_protocol, [Edit(kind="merge_visits", target_id="2")])
    assert len(p.visits) == n - 1
    p = apply_edits_local(parsed_protocol, [Edit(kind="drop_rule", target_id="I7")])
    assert "I7" not in [r.rule_id for r in p.rules] and any(r.rule_id == "I7" for r in parsed_protocol.rules)


def test_run_manager_direct():
    run_id = RUNS.submit(RunRequest(), background=False)
    st = RUNS.get(run_id)
    assert st is not None and st.stage == "done"
