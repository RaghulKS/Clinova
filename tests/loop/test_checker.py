from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from clinova.ingest.history import HistoryResult
from clinova.loop import checker


@pytest.mark.parametrize("kwargs", [{"limit": -1}, {"retrain_threshold": 0}])
def test_invalid_arguments_never_connect(kwargs):
    with pytest.raises(ValueError):
        checker.check_outcomes(**kwargs)


def test_score_only_predictions_before_outcome_and_idempotent_sql():
    calls = []
    conn = SimpleNamespace(execute=lambda sql, params: (calls.append((sql, params)) or SimpleNamespace(rowcount=2)))
    observed = datetime(2020, 2, 1, tzinfo=UTC)
    assert checker.score_predictions(conn, "NCT00000001", "shortfall", False, observed) == 2
    sql, params = calls[0]
    assert params == (0.0, observed, "NCT00000001", "shortfall", observed)
    assert "p.predicted_at < %s" in sql and "NOT EXISTS" in sql and "ON CONFLICT DO NOTHING" in sql
    assert "('fixture','replay')" in sql


def test_checker_preserves_original_and_uses_exact_completion_date(monkeypatch, tmp_path):
    original = {"original": True}
    saved = dict(nct_id="NCT00000001", first_version=original, first_version_date=date(2010, 1, 1))
    writes, mapped = [], []

    class Connection:
        def cursor(self, **kwargs):
            return self

        def execute(self, sql, params=()):
            if sql.startswith("SELECT * FROM trials"):
                return SimpleNamespace(fetchall=lambda: [saved])
            if sql.startswith("SELECT DISTINCT nct_id"):
                return []
            writes.append((sql, params))
            return SimpleNamespace(rowcount=1)

    @contextmanager
    def connection():
        yield Connection()

    @contextmanager
    def client(path):
        assert str(path).startswith(str(tmp_path))
        yield SimpleNamespace(get=lambda path: {"protocolSection": {"statusModule": {"overallStatus": "COMPLETED"}}})

    def map_trial(current, first, **kwargs):
        mapped.append(first)
        return SimpleNamespace(nct_id=saved["nct_id"], completion_date=date(2020, 2, 3),
                               label_accrual_fail=False, label_shortfall=None, label_amend=None,
                               status="COMPLETED", split="train")

    monkeypatch.setattr(checker, "get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    monkeypatch.setattr(checker, "get_conn", connection)
    monkeypatch.setattr(checker, "CTGovClient", client)
    monkeypatch.setattr(checker, "fetch_history", lambda *a, **kw: HistoryResult())
    monkeypatch.setattr(checker, "map_trial", map_trial)

    def label(trial, history):
        assert history.first == original
        return trial, {}

    monkeypatch.setattr(checker, "label_trial", label)
    result = checker.check_outcomes(limit=1)
    assert mapped == [original, original]
    assert result["ended"] == 1 and result["outcomes"] == 1 and result["scored"] == 1
    assert not any(sql.startswith("UPDATE trials") for sql, _ in writes)
    label_write = next(params for sql, params in writes if "VALUES (%s,%s,%s,'ctgov:T21',%s)" in sql)
    assert label_write == (saved["nct_id"], "accrual_fail", 0.0, datetime(2020, 2, 3, tzinfo=UTC))
    assert not list((tmp_path / "loop").glob("check-*"))


def test_retrain_threshold_accumulates_and_marks_only_success(monkeypatch, tmp_path):
    import pandas as pd

    arrived = {"NCT00000001"}
    trained = []

    class Connection:
        def cursor(self, **kwargs):
            return self

        def execute(self, sql, params=()):
            if sql.startswith("SELECT * FROM trials"):
                return SimpleNamespace(fetchall=lambda: [])
            return [(nct,) for nct in arrived]

    @contextmanager
    def connection():
        yield Connection()

    @contextmanager
    def client(*args):
        yield None

    monkeypatch.setattr(checker, "get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    monkeypatch.setattr(checker, "get_conn", connection)
    monkeypatch.setattr(checker, "CTGovClient", client)
    monkeypatch.setattr(checker, "training_frame", lambda: pd.DataFrame())
    monkeypatch.setattr(checker._model, "train", lambda *a, **kw:
                        (trained.append(1) or {"models": {"accrual_fail": 1}, "version": "test"}))
    assert checker.check_outcomes(retrain_threshold=2)["retrained"] == 0
    arrived.add("NCT00000002")
    assert checker.check_outcomes(retrain_threshold=2)["retrained"] == 1
    assert checker.check_outcomes(retrain_threshold=2)["retrained"] == 0
    assert len(trained) == 1
    assert (tmp_path / "loop" / "checker-model.joblib").exists()
