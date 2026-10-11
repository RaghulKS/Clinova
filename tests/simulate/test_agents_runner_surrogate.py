from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from clinova.common import llm
from clinova.common.fixtures import load_fixture
from clinova.contracts import AgentDecision, TrialRecord
from clinova.simulate import __main__ as cli
from clinova.simulate import agents, runner
from clinova.simulate.__main__ import main
from clinova.simulate.surrogate import Surrogate, burden_features


def decision(persona_id, enroll=True, dropout_visit=None):
    return AgentDecision(persona_id=persona_id, enroll=enroll, dropout_visit=dropout_visit,
                         reason="I can arrange a lift to the clinic.", burden_tags=["travel"])


async def test_batches_concurrent_retry_order(monkeypatch, personas, parsed_protocol):
    active = peak = 0
    requests = []

    async def generate(prompt, schema, **kwargs):
        nonlocal active, peak
        data = json.loads(prompt.split("PERSONA DATA\n")[1])
        requests.append([p["persona_id"] for p in data])
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        chosen = data[1:] if len(data) == 25 else data
        return schema(decisions=[decision(p["persona_id"]) for p in reversed(chosen)])

    monkeypatch.setattr(llm, "agenerate_json", generate)
    result = await agents.arun_agents(personas[:26], parsed_protocol, 1)
    assert [d.persona_id for d in result] == [p.persona_id for p in personas[:26]]
    assert peak == 2
    assert sorted(map(len, requests)) == [1, 1, 25]


@pytest.mark.parametrize("bad", ["unknown", "duplicate", "dropout", "reason", "missing"])
async def test_response_boundary(monkeypatch, personas, parsed_protocol, bad):
    async def generate(prompt, schema, **kwargs):
        item = decision(personas[0].persona_id)
        if bad == "unknown":
            item.persona_id = "unknown"
        if bad == "dropout":
            item.dropout_visit = 999
        if bad == "reason":
            item.reason = " "
        return schema(decisions=[] if bad == "missing" else [item, item] if bad == "duplicate" else [item])
    monkeypatch.setattr(llm, "agenerate_json", generate)
    with pytest.raises(llm.LLMError):
        await agents.arun_agents(personas[:1], parsed_protocol, 1)


def test_offer_and_summary(parsed_protocol, funnel):
    offer = agents.trial_offer(parsed_protocol)
    assert "random" in offer and "Study day".lower() in offer.lower()
    assert "reimbursement" in offer and "Scheduled duration" in offer
    decisions = [decision("a"), decision("b", dropout_visit=2), decision("c", False)]
    result = runner.summarize(parsed_protocol, 1, funnel, decisions)
    assert result.enroll_rate == 2 / 3 and result.completion_rate == 0.5
    assert result.dropout_curve[0] == 0 and result.dropout_curve[-1] == 0.5
    assert result.median_dropout_visit == 2
    assert result.top_reasons[0].share == 1


def test_runner_sample_and_null_persistence(monkeypatch, personas, parsed_protocol, funnel):
    items = personas[:200]
    mask = np.zeros(200, dtype=bool)
    mask[:56] = True
    monkeypatch.setattr(runner, "generate_cohort", lambda *a: items)
    monkeypatch.setattr(runner, "cohort_frame", lambda p: pd.DataFrame(index=range(len(p))))
    monkeypatch.setattr(runner, "screen", lambda *a, **kw: (mask, funnel))
    monkeypatch.setattr(runner, "run_agents", lambda p, *a: [decision(x.persona_id) for x in p])
    saved = []
    class Cursor:
        def execute(self, *args):
            saved.append(args)
        def executemany(self, sql, rows):
            saved.extend(rows)
    @contextmanager
    def cursor():
        yield Cursor()
    class Conn:
        def cursor(self):
            return cursor()
    @contextmanager
    def connection():
        yield Conn()
    monkeypatch.setattr(runner, "get_conn", connection)
    result = runner.simulate_trial(parsed_protocol, n=200, max_agents=25, seeds=[1])
    assert result[0].n_agents == 25
    rows = saved[1:]
    assert len(rows) == 200
    assert sum(row[3] is True for row in rows) == 25
    assert sum(row[3] is None for row in rows) == 175
    assert all(row[3] is None for row in rows if not row[2])


def test_surrogate_filters_unsampled_roundtrip(tmp_path, parsed_protocol, personas):
    rows = [{"eligible": True, "enrolled": i % 3 != 0, "dropout_visit": 2 if i % 4 == 0 and i % 3 != 0 else None,
             "persona": p.model_dump(mode="json"), "protocol": parsed_protocol} for i, p in enumerate(personas[:30])]
    model = Surrogate.fit(pd.DataFrame(rows))
    rows.append({**rows[0], "enrolled": None})
    assert Surrogate.fit(pd.DataFrame(rows)).state == model.state
    cohort = pd.DataFrame([{**p.model_dump(), **p.clinical.model_dump()} for p in personas])
    score = model.score(parsed_protocol, cohort)
    assert all(0 <= value <= 1 for value in score)
    path = tmp_path / "surrogate.json"
    model.save(path)
    assert Surrogate.load(path).score(parsed_protocol, cohort) == score
    assert model.score(parsed_protocol, cohort.iloc[:0]) == (0, 0)
    assert burden_features(parsed_protocol)["visit_count"] == len(parsed_protocol.visits)


def test_cli_dry_run_no_network(monkeypatch, capsys):
    monkeypatch.setattr(llm, "_usage_count", lambda *a: pytest.fail("dry run spent a network call"))
    assert main(["batch", "--n-trials", "3", "--seeds", "2", "--max-calls", "20", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["nominal_calls"] == 51 and plan["estimated_input_tokens"] > 0


def test_cli_uses_first_version_only(monkeypatch, parsed_protocol):
    trial = load_fixture("trial_record.json", TrialRecord)
    row = trial.model_dump()
    cursor = SimpleNamespace(description=[SimpleNamespace(name=name) for name in row],
                             fetchall=lambda: [tuple(row.values())])
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *args: cursor)
    observed = []
    def parse(text, **kwargs):
        observed.append(text)
        return parsed_protocol.model_copy(update={"visits": []})
    monkeypatch.setattr(cli, "get_conn", connection)
    monkeypatch.setattr(cli, "parse_protocol", parse)
    monkeypatch.setattr(cli, "estimate_visits", lambda t: parsed_protocol.visits)
    result = cli.registered_protocols(1)
    assert observed == [trial.eligibility_text_first]
    assert result[0].title == trial.title and result[0].visits == parsed_protocol.visits
