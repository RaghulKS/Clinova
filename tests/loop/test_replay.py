from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from clinova.loop import data, replay
from clinova.loop.data import available_training


def trial(nct, outcome, y, *, start=date(2010, 1, 1), registered=date(2009, 1, 1), split=None):
    return dict(nct_id=nct, completion_date=outcome, label_accrual_fail=y, label_shortfall=None,
                label_amend=None, start_date=start, first_version_date=registered, first_version={},
                split=split or ("train" if start < date(2018, 1, 1) else "test"))


def initial(n_pos=6, n_neg=6):
    return [trial(f"init{i}", date(2019, 1 + i % 12, 1), int(i < n_pos), start=date(2010 + i % 7, 3, 1))
            for i in range(n_pos + n_neg)]


def fake_features(frame):
    """Synthetic raw features (one informative column) so the real T22 trainer runs offline."""
    y = pd.to_numeric(frame["label_accrual_fail"], errors="coerce").fillna(0.5).to_numpy()
    raw = pd.DataFrame({"text_len": y + 0.01 * np.arange(len(frame)), "n_rules": 10.0}, index=frame["nct_id"])
    return raw, {}, {}


def spy(monkeypatch, name, log):
    original = getattr(data, name)

    def wrapped(*args, **kwargs):
        log.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(data, name, wrapped)


def setup(monkeypatch, frame):
    monkeypatch.setattr(replay, "training_frame", lambda: pd.DataFrame(frame))
    monkeypatch.setattr(replay, "load_features", fake_features)
    monkeypatch.setattr(replay, "_plot", lambda result, path: None)


def test_training_cutoff_excludes_future_and_test_split():
    rows = [trial("old", date(2019, 1, 1), 0), trial("future", date(2020, 1, 1), 1),
            trial("test", date(2019, 1, 1), 1, start=date(2018, 1, 1)),
            trial("mislabeled-test", date(2019, 1, 1), 1, split="test"),
            trial("unregistered", date(2019, 1, 1), 1, registered=date(2020, 1, 1))]
    assert available_training(pd.DataFrame(rows), date(2019, 12, 31)).nct_id.tolist() == ["old"]


def test_metrics_handle_one_class_and_empty():
    assert replay._metrics([1], [0.3]).auc is None
    assert replay._metrics([], []).brier is None


def test_real_t22_replay_never_trains_on_test_split_or_future_outcomes(monkeypatch, tmp_path):
    frame = initial() + [
        trial("january", date(2020, 1, 15), 1),
        trial("test-feb", date(2020, 2, 15), 0, start=date(2018, 3, 1), registered=date(2018, 1, 1)),
        trial("mislabeled-test", date(2019, 6, 1), 1, split="test"),
        trial("future-registration", date(2020, 1, 16), 0, registered=date(2020, 2, 1)),
        trial("unknown", date(2019, 5, 1), None),
    ]
    completion = {r["nct_id"]: r["completion_date"] for r in frame}
    trains, refs, scores, persisted = [], [], [], []
    spy(monkeypatch, "train", trains)
    spy(monkeypatch, "fit_ref", refs)
    spy(monkeypatch, "predict_p", scores)

    @contextmanager
    def connect(*args, **kwargs):
        yield SimpleNamespace(execute=lambda *args: None)

    setup(monkeypatch, frame)
    monkeypatch.setattr(replay, "_persist", lambda df, p, **kw: persisted.append((set(df.nct_id), kw)))
    monkeypatch.setattr(replay.psycopg, "connect", connect)
    result = replay.run_replay(start="2020-01", end="2020-02", out=tmp_path)

    trained = {args[1]["version"]: set(args[0].index) for args, _ in trains}
    cutoffs = [date(2019, 12, 31), date(2020, 1, 31), date(2020, 2, 29)]
    assert len(trains) == 3  # freeze, after January (january arrives), after February (future-registration)
    for (args, _), cutoff, (ref_args, _) in zip(trains, cutoffs, refs, strict=True):
        x, cfg = args
        ids = set(x.index)
        assert not ids & {"test-feb", "mislabeled-test", "unknown"}
        assert set(x["split"]) == {"train"} and set(ref_args[0].index) == ids  # ref sees these rows only
        assert max(completion[i] for i in ids) <= cutoff
        assert cfg["bootstraps"] == 0 and not cfg["save"] and len(cfg["grid"]) == 1
    assert "january" not in set(trains[0][0][0].index) and "january" in set(trains[1][0][0].index)
    for (bundle, target, x), _ in scores:  # every scored trial is outside its model's training rows
        assert target == "accrual_fail" and not set(x.index) & trained[bundle["version"]]
    assert persisted[0][0] == {"january", "test-feb"} and persisted[2][0] == {"test-feb"}
    versions = [kw["version"] for _, kw in persisted]
    assert all(v.startswith("T22-replay-") for v in versions)
    assert ":retrained:" in versions[0] and ":frozen:" in versions[1]
    assert [m.n_scored for m in result.months] == [2, 1]
    assert result.months[0].retrained.brier is not None and "T22 LightGBM" in result.summary
    assert (tmp_path / "replay.json").exists()


def test_skipped_target_scores_nothing_and_is_counted(monkeypatch, tmp_path):
    # Only 3 positives before freeze: train() skips accrual_fail, so no arm can score.
    frame = initial(n_pos=3) + [trial("january", date(2020, 1, 15), 1),
                                trial("march", date(2020, 3, 15), 0)]
    scores = []
    spy(monkeypatch, "predict_p", scores)
    setup(monkeypatch, frame)
    monkeypatch.setattr(replay.psycopg, "connect", lambda *a, **kw: pytest.fail("persist=False wrote"))
    result = replay.run_replay(start="2020-01", end="2020-02", out=tmp_path, persist=False)
    assert not scores
    assert [m.n_scored for m in result.months] == [0, 0]
    assert "2 months scored nothing" in result.summary and "3 positive" in result.summary


@pytest.mark.parametrize("kwargs", [{"horizon_months": 0}, {"start": "2019-01"},
                                    {"start": "2021-01", "end": "2020-01"}])
def test_invalid_replay_windows_fail_before_database_access(kwargs):
    with pytest.raises(ValueError):
        replay.run_replay(**kwargs)
