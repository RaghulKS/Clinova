"""Time-machine replay (owner: T21). Writes docs/replay.json and docs/figures/replay.png."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import pandas as pd
import psycopg
from sklearn.metrics import brier_score_loss, roc_auc_score

from clinova.common.config import REPO_ROOT, get_settings
from clinova.common.db import get_conn
from clinova.contracts import ReplayArm, ReplayMonth, ReplayResult, Target
from clinova.loop.data import (
    REPLAY_CFG,
    available_training,
    fit,
    load_features,
    probabilities,
    training_frame,
)


def _metrics(y, p) -> ReplayArm:
    return ReplayArm(auc=float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
                     brier=float(brier_score_loss(y, p)) if len(y) else None)


def _persist(frame, probabilities, *, month, target, version, arm, run_id):
    if frame.empty:
        return
    predictions, scores = [], []
    predicted = month.to_pydatetime().replace(tzinfo=UTC)
    for row, p in zip(frame.to_dict("records"), probabilities, strict=True):
        key = uuid5(NAMESPACE_URL, f"clinova:{run_id}:{target}:{month:%Y-%m}:{arm}:{row['nct_id']}").hex
        observed = datetime.combine(row["completion_date"], datetime.min.time(), UTC)
        predictions.append((key, row["nct_id"], version, target, float(p), "replay", predicted))
        scores.append((key, row["nct_id"], version, "replay", target, float(p),
                       float(row["label_" + target]), predicted.date(), observed))
    with get_conn() as conn, conn.cursor() as cursor:
        cursor.executemany("INSERT INTO predictions "
                           "(prediction_id,trial_id,model_version,target,p,context,predicted_at) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", predictions)
        cursor.executemany("INSERT INTO prediction_scores "
                           "(prediction_id,trial_id,model_version,context,target,p,y,month,scored_at) "
                           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", scores)


def _plot(result: ReplayResult, path: Path):
    os.environ.setdefault("MPLCONFIGDIR", str(get_settings().DATA_DIR / "loop" / "matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    months = [pd.Timestamp(m.month) for m in result.months]
    for arm, label in (("retrained", "Updated"), ("frozen", "Frozen")):
        for ax, metric in zip(axes, ("auc", "brier"), strict=True):
            ax.plot(months, [getattr(getattr(m, arm), metric) for m in result.months], label=label)
            ax.set_ylabel(metric.upper() if metric == "auc" else "Brier (lower is better)")
            ax.grid(alpha=0.2)
            ax.legend()
    axes[0].set_title("T22 LightGBM replay (fixed settings, no bootstraps) · first-version features · "
                      "event-time outcomes")
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_replay(
    *,
    freeze: date = date(2019, 12, 31),
    start: str = "2020-01",
    end: str = "2023-12",
    horizon_months: int = 6,
    target: Target = Target.accrual_fail,
    out: Path | None = None,
    persist: bool = True,
) -> ReplayResult:
    """Freeze a model on outcomes observed by ``freeze`` (first-version features only), then step
    monthly from ``start`` to ``end``: score trials whose outcome arrives within ``horizon_months``
    with the current model, log those predictions (context 'replay', predicted_at = the month),
    add the month's outcomes and retrain. Compare with the never-retrained model. Writes
    ``out``/replay.json and ``out``/figures/replay.png (``out`` defaults to docs/);
    ``persist=False`` skips the ledger writes (smoke runs)."""
    if horizon_months < 1:
        raise ValueError("horizon_months must be positive")
    first, last = pd.Timestamp(start + "-01"), pd.Timestamp(end + "-01")
    if first > last or first.date() <= freeze:
        raise ValueError("replay months must be ordered and start after freeze")
    target = Target(target).value
    frame = training_frame()
    if frame.empty or "label_" + target not in frame:
        raise ValueError("No verified first-version trials with dated outcomes available")
    frame = frame.loc[frame["label_" + target].notna()].copy()
    features = load_features(frame)
    initial = available_training(frame, freeze)
    frozen = current = fit(initial, features, REPLAY_CFG, "T22-replay-")
    snapshot = frame.sort_values("nct_id").to_json(date_format="iso", orient="records")
    run_id = hashlib.sha256(f"T22|{REPLAY_CFG.get('features')}|{snapshot}|{freeze}|{start}|{end}|{horizon_months}".encode()).hexdigest()[:16]
    results, retrained_p, frozen_p, labels = [], [], [], []
    unique_scored, skipped, reasons = set(), 0, set()
    for month in pd.date_range(first, last, freq="MS"):
        next_month = month + pd.DateOffset(months=1)
        horizon = month + pd.DateOffset(months=horizon_months)
        known_registration = frame["first_version_date"].notna() & (frame["first_version_date"] <= month.date())
        evaluation = frame.loc[known_registration & (frame["completion_date"] > month.date()) &
                               (frame["completion_date"] < horizon.date())]
        missing = [b["skipped"].get(target, "no model") for b in (current, frozen) if target not in b["models"]]
        if missing:  # train() skipped the target (too few labeled trials of one class): score nothing
            skipped += 1
            reasons.update(missing)
            evaluation = evaluation.iloc[:0]
        y = evaluation["label_" + target].astype(int).to_numpy()
        p_updated = probabilities(current, evaluation, features, target) if len(y) else np.array([])
        p_frozen = probabilities(frozen, evaluation, features, target) if len(y) else np.array([])
        for arm, bundle, p in (("retrained", current, p_updated), ("frozen", frozen, p_frozen)):
            if persist:
                _persist(evaluation, p, month=month, target=target,
                         version=bundle["version"] + ":" + arm + ":" + run_id, arm=arm, run_id=run_id)
        new = frame.loc[(frame["completion_date"] >= month.date()) &
                        (frame["completion_date"] < next_month.date())]
        results.append(ReplayMonth(month=month.strftime("%Y-%m"), n_scored=len(y), n_pos=int(y.sum()),
                                   n_new_outcomes=len(new), retrained=_metrics(y, p_updated),
                                   frozen=_metrics(y, p_frozen)))
        unique_scored.update(evaluation["nct_id"])
        labels.extend(y)
        retrained_p.extend(p_updated)
        frozen_p.extend(p_frozen)
        learned = available_training(frame, (next_month - pd.Timedelta(days=1)).date())
        # Never learn the static test split; monthly updates use newly observed pre-2018 trials only.
        if len(learned) > len(available_training(frame, (month - pd.Timedelta(days=1)).date())):
            current = fit(learned, features, REPLAY_CFG, "T22-replay-")
    updated = _metrics(labels, retrained_p)
    baseline = _metrics(labels, frozen_p)
    if labels:
        comparison = "beat" if updated.brier < baseline.brier else "did not beat"
        sentence = (f"T22 LightGBM monthly updates {comparison} the frozen model on pooled overlapping "
                    f"windows: Brier {updated.brier:.6f} vs {baseline.brier:.6f}; "
                    f"{len(labels)} scored windows, {len(unique_scored)} unique trials, "
                    f"{len(initial)} initial training trials.")
    else:
        sentence = "No trials were scoreable in the requested replay windows."
    if skipped:
        sentence += f" {skipped} months scored nothing because the target was skipped: {'; '.join(sorted(reasons))}."
    result = ReplayResult(target=target, freeze_date=freeze, horizon_months=horizon_months, months=results,
                          summary=sentence + " Retrospective completion dates proxy observation; "
                          "this is provisional event-time evidence, not a publication-time backtest.",
                          generated_at=datetime.now(UTC))
    out = Path(out) if out else REPO_ROOT / "docs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _plot(result, out / "figures" / "replay.png")
    if persist:  # Historical scores sit below Timescale's materialization watermark; CALL needs autocommit.
        with psycopg.connect(get_settings().DATABASE_URL.get_secret_value(), autocommit=True) as conn:
            conn.execute("CALL refresh_continuous_aggregate('prediction_scores_monthly', NULL, NULL)")
    return result
