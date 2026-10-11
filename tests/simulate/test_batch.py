from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from clinova.simulate import __main__ as cli
from clinova.simulate import batch
from clinova.simulate.surrogate import Surrogate, _matrix, burden_features


def test_sample_deterministic_stratified_and_mandatory():
    frame = pd.DataFrame([
        {"nct_id": f"NCT{i:08d}", "split": "train" if i % 2 else "test",
         "label_accrual_fail": i % 3 == 0, "label_shortfall": None, "label_amend": i in (2, 4)}
        for i in range(50)
    ])
    frame.loc[0, "nct_id"] = batch.DEMO_TRIALS[0]
    chosen = batch.select_sample(frame, 15)
    assert len(chosen) == 15 and chosen.nct_id.is_unique
    assert {batch.DEMO_TRIALS[0], "NCT00000002", "NCT00000004"} <= set(chosen.nct_id)
    assert set(chosen.split) == {"train", "test"}
    assert set(chosen.label_accrual_fail) == {True, False}
    pd.testing.assert_frame_equal(chosen, batch.select_sample(frame.sample(frac=1, random_state=3), 15))
    assert len(batch.select_sample(frame, 1)) == 3
    with pytest.raises(ValueError):
        batch.select_sample(pd.concat([frame, frame]), 10)


def test_append_amendments_preserves_sample(monkeypatch, tmp_path):
    path = tmp_path / "sample.csv"
    pd.DataFrame([["NCT00000001", "train", False, None, None]],
                 columns=["nct_id", "split", *batch.LABELS]).to_csv(path, index=False)
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: [
            ("NCT00000002", "test", True, False, True)]))
    monkeypatch.setattr(batch, "get_conn", connection)
    assert batch.include_new_amendments(path) == ["NCT00000002"]
    assert batch.include_new_amendments(path) == []
    assert pd.read_csv(path).nct_id.tolist() == ["NCT00000001", "NCT00000002"]


def test_frozen_protocol_rejects_change(monkeypatch, tmp_path, parsed_protocol):
    monkeypatch.setattr(batch, "get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    batch.save_protocol(parsed_protocol)
    batch.save_protocol(parsed_protocol)
    with pytest.raises(ValueError, match="changed"):
        batch.save_protocol(parsed_protocol.model_copy(update={"title": "Changed"}))


def test_vectorized_validation_matches_score(personas, parsed_protocol):
    frame = pd.DataFrame([{"eligible": True, "enrolled": i % 3 != 0, "dropout_visit": 2 if i % 2 else None,
                           "persona": p.model_dump(mode="json"), "protocol": parsed_protocol}
                          for i, p in enumerate(personas[:30])])
    model = Surrogate.fit(frame)
    traits = pd.DataFrame([{**p.model_dump(), **p.clinical.model_dump()} for p in personas[:30]])
    x = _matrix(traits, burden_features(parsed_protocol))
    enroll, dropout = model._probabilities(x)
    assert model.score(parsed_protocol, traits) == pytest.approx((enroll.mean(), np.dot(enroll, dropout) / enroll.sum()))


def test_feature_view_first_version_and_model_filter(monkeypatch):
    queries = []
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda sql: queries.append(sql.as_string()))
    monkeypatch.setattr(batch, "get_conn", connection)
    batch.create_features_view()
    assert "version_no=0" in queries[0] and "DISTINCT ON (trial_id, seed)" in queries[0]
    assert "trial_id LIKE 'NCT%'" in queries[0] and "model_version=" in queries[0]
    assert "dropout_curve->>3" in queries[0] and "stddev_pop(enroll_rate)" in queries[0]
    assert "FILTER (WHERE n_agents>0)" in queries[0] and "FILTER (WHERE n_agents*enroll_rate>0)" in queries[0]
    assert "FROM persona_outcomes p" in queries[0] and "ANY(p.burden_tags)" in queries[0]
    assert "nullif(jsonb_array_length(dropout_curve),0)" in queries[0]


def test_refit_all_observed_decisions_and_grouped_holdout(monkeypatch, tmp_path, parsed_protocol, personas):
    monkeypatch.setattr(batch, "get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    rows = []
    for trial in range(5):
        protocol = parsed_protocol.model_copy(update={"trial_id": f"NCT0000000{trial}" if trial < 2 else f"FIXTURE-{trial}"})
        batch.save_protocol(protocol)
        rows.extend((protocol.trial_id, True, i % 3 != 0, 2 if i % 2 else None,
                     persona.model_dump(mode="json"), "test-model") for i, persona in enumerate(personas[:30]))
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: rows))
    monkeypatch.setattr(batch, "get_conn", connection)
    monkeypatch.setattr(batch, "generate_cohort", lambda *args: personas[:30])
    metrics = batch.refit_surrogate()
    assert metrics["observed_decisions"] == 150 and metrics["trials"] == 5
    assert metrics["training_trials"] == 4 and metrics["held_out_trials"] == 1
    assert metrics["registered_trials"] == 2 and metrics["fixture_trials"] == 3
    assert 0 <= metrics["enrollment_accuracy"] <= 1
    assert (tmp_path / "models/surrogate.json").exists()


@pytest.mark.parametrize("quota", [False, True])
def test_batch_resumes_seeds_and_pauses_at_quota(monkeypatch, tmp_path, parsed_protocol, quota):
    sample = tmp_path / "sample.csv"
    pd.DataFrame({"nct_id": ["NCT00000001", "NCT00000002"]}).to_csv(sample, index=False)
    settings = SimpleNamespace(GEMINI_API_KEY=None, GEMINI_MODEL_FAST="test-model")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_features_view", lambda: None)
    monkeypatch.setattr(cli, "include_new_amendments", lambda path: [])
    monkeypatch.setattr(cli, "save_protocol", lambda protocol: None)
    parsed = []
    def protocols(limit, trial):
        parsed.append(trial)
        return [parsed_protocol.model_copy(update={"trial_id": trial})]
    monkeypatch.setattr(cli, "registered_protocols", protocols)
    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *args: [(1,)])
    monkeypatch.setattr(cli, "get_conn", connection)
    simulated = []
    class QuotaError(Exception):
        code = 429
    def simulate(protocol, seeds, max_agents):
        simulated.append((protocol.trial_id, seeds, max_agents))
        if quota:
            raise QuotaError()
        return [SimpleNamespace(model_dump_json=lambda: '{}')]
    monkeypatch.setattr(cli, "simulate_trial", simulate)
    result = cli.main(["batch", "--n-trials", "2", "--sample", str(sample), "--max-calls", "20"])
    assert result == (2 if quota else 0)
    assert all(seeds == [2] for _, seeds, _ in simulated)
    assert len(parsed) == (1 if quota else 2)
