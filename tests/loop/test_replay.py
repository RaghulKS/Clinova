from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from clinova.loop import _model, replay
from clinova.loop.data import available_training


def trial(nct, outcome, y, *, start=date(2010, 1, 1), registered=date(2009, 1, 1)):
    return dict(nct_id=nct, completion_date=outcome, label_accrual_fail=y, label_shortfall=None,
                label_amend=None, start_date=start, first_version_date=registered, planned_enrollment=100,
                n_sites=2, n_countries=1, n_arms=1, min_age_years=18, max_age_years=None,
                phase="phase2", sponsor_class="INDUSTRY", allocation="RANDOMIZED", masking="NONE")


def test_training_cutoff_excludes_future_and_test_split():
    rows = [trial("old", date(2019, 1, 1), 0), trial("future", date(2020, 1, 1), 1),
            trial("test", date(2019, 1, 1), 1, start=date(2018, 1, 1)),
            trial("unregistered", date(2019, 1, 1), 1, registered=date(2020, 1, 1))]
    assert available_training(pd.DataFrame(rows), date(2019, 12, 31)).nct_id.tolist() == ["old"]


def test_placeholder_ignores_outcome_side_features_and_unknown_labels():
    rows = pd.DataFrame([trial("a", date(2019, 1, 1), 0), trial("b", date(2019, 1, 1), 1),
                         trial("unknown", date(2019, 1, 1), None)])
    rows["why_stopped"] = ["Low accrual", "Other", "Unknown"]
    bundle = _model.train(rows)
    p = _model.probabilities(bundle, rows, "accrual_fail")
    changed = rows.assign(why_stopped="changed", actual_enrollment=99999, label_accrual_fail=1)
    np.testing.assert_allclose(p, _model.probabilities(bundle, changed, "accrual_fail"))
    assert bundle["placeholder"] and "amend" not in bundle["models"]
    assert np.isfinite(p).all()


@pytest.mark.parametrize("y", [0, 1])
def test_one_class_training_is_explicit_constant(y):
    frame = pd.DataFrame([trial("a", date(2019, 1, 1), y)])
    p = _model.probabilities(_model.train(frame), frame, "accrual_fail")
    assert p.tolist() == [y]
    assert replay._metrics([y], p).auc is None
    assert replay._metrics([], []).brier is None


def test_replay_scores_before_learning_and_excludes_unregistered_and_unknown(monkeypatch, tmp_path):
    frame = pd.DataFrame([trial("initial", date(2019, 1, 1), 0),
                          trial("january", date(2020, 1, 15), 1),
                          trial("test-feb", date(2020, 2, 15), 0, start=date(2018, 1, 1)),
                          trial("future-registration", date(2020, 1, 16), 0, registered=date(2020, 2, 1)),
                          trial("unknown", date(2020, 1, 15), None)])
    training, predictions = [], []

    def train(df, cfg):
        training.append(set(df.nct_id))
        return {"version": str(len(training)), "models": {"accrual_fail": object()}}

    def probabilities(bundle, df, target):
        # A model trained on January must not score January's still-future outcome.
        assert "january" not in training[int(bundle["version"]) - 1] or "january" not in set(df.nct_id)
        return np.full(len(df), 0.4)

    @contextmanager
    def connect(*args, **kwargs):
        yield SimpleNamespace(execute=lambda *args: None)

    monkeypatch.setattr(replay, "training_frame", lambda: frame)
    monkeypatch.setattr(replay._model, "train", train)
    monkeypatch.setattr(replay._model, "probabilities", probabilities)
    monkeypatch.setattr(replay, "_persist", lambda df, p, **kw: predictions.append((set(df.nct_id), kw)))
    monkeypatch.setattr(replay, "_plot", lambda result: None)
    monkeypatch.setattr(replay.psycopg, "connect", connect)
    monkeypatch.setattr(replay, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    result = replay.run_replay(start="2020-01", end="2020-02")
    assert predictions[0][0] == {"january", "test-feb"}
    assert predictions[2][0] == {"test-feb"}
    assert training[0] == {"initial"}
    assert all("test-feb" not in ids for ids in training)
    assert [m.n_scored for m in result.months] == [2, 1]
    assert "did not beat" in result.summary
    assert (tmp_path / "docs" / "replay.json").exists()


@pytest.mark.parametrize("kwargs", [{"horizon_months": 0}, {"start": "2019-01"},
                                        {"start": "2021-01", "end": "2020-01"}])
def test_invalid_replay_windows_fail_before_database_access(kwargs):
    with pytest.raises(ValueError):
        replay.run_replay(**kwargs)
