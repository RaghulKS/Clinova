"""Test-split validation (owner: T22): AUC with bootstrap CIs, Brier, reliability, flag hit rate,
simulation ablation, SHAP importance. Writes ``docs/metrics.json`` and ``docs/figures/*.png``.

Never tunes anything: the bundle is trained on the train split before this runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from clinova.common.config import REPO_ROOT
from clinova.contracts import (
    Ablation,
    HitRate,
    MetricCI,
    ReliabilityBin,
    Target,
    TargetMetrics,
    ValidationMetrics,
)
from clinova.model.features import FEATURE_NAMES, LABELS, SIM_COLS
from clinova.model.train import baseline_proba, predict_p, train

N_BOOT = 1000
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BLUE, ORANGE = "#2a78d6", "#eb6834"  # categorical slots 1 and 2 of the dataviz reference palette


def _boot_idx(y: np.ndarray, n_boot: int, seed: int):
    """Bootstrap index sets that keep both classes (AUC needs them)."""
    rng = np.random.default_rng(seed)
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            yield idx


def auc_ci(y: np.ndarray, p: np.ndarray, n_boot: int = N_BOOT, seed: int = 0) -> MetricCI:
    vals = [roc_auc_score(y[i], p[i]) for i in _boot_idx(y, n_boot, seed)]
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return MetricCI(value=float(roc_auc_score(y, p)), ci_low=float(lo), ci_high=float(hi))


def paired_diff_ci(y: np.ndarray, p1: np.ndarray, p2: np.ndarray, n_boot: int = N_BOOT, seed: int = 0) -> MetricCI:
    """AUC(p1) - AUC(p2) with a paired bootstrap (same resampled trials for both)."""
    vals = [roc_auc_score(y[i], p1[i]) - roc_auc_score(y[i], p2[i]) for i in _boot_idx(y, n_boot, seed)]
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return MetricCI(value=float(roc_auc_score(y, p1) - roc_auc_score(y, p2)), ci_low=float(lo), ci_high=float(hi))


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[ReliabilityBin]:
    edges = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges[1:-1], right=True), 0, bins - 1)
    out = []
    for b in range(bins):
        m = which == b
        out.append(ReliabilityBin(bin_low=float(edges[b]), bin_high=float(edges[b + 1]), n=int(m.sum()),
                                  mean_pred=float(p[m].mean()) if m.any() else None,
                                  frac_pos=float(y[m].mean()) if m.any() else None))
    return out


def target_metrics(bundle: Mapping[str, Any], test: pd.DataFrame, target: str) -> tuple[TargetMetrics | None, str | None]:
    label = LABELS[target]
    d = test[test[label].notna()]
    y = d[label].astype(int).to_numpy()
    if target not in bundle["models"]:
        return None, f"{target}: no model ({bundle['skipped'].get(target, 'not trained')})."
    if len(np.unique(y)) < 2:
        return None, f"{target}: test split has {len(y)} labeled trials of one class only; AUC undefined."
    p, pb = predict_p(bundle, target, d), baseline_proba(bundle, target, d)
    return TargetMetrics(
        target=Target(target), n_test=len(y), n_pos=int(y.sum()), auc=auc_ci(y, p), baseline_auc=auc_ci(y, pb),
        brier=float(brier_score_loss(y, p)), baseline_brier=float(brier_score_loss(y, pb)), reliability=reliability(y, p),
    ), None


def ablation(df: pd.DataFrame, ref: Mapping[str, Any], min_test: int = 10) -> tuple[Ablation | None, str]:
    """Accrual-failure AUC with vs without simulation features on the simulated test subset.
    Both models train on the same train split (no bootstraps); a paired bootstrap gives the CI."""
    label = LABELS["accrual_fail"]
    simmed = df[SIM_COLS].notna().any(axis=1)
    tr, te = df[(df["split"] == "train")], df[(df["split"] == "test") & simmed & df[label].notna()]
    n_sim_train = int((simmed & (df["split"] == "train") & df[label].notna()).sum())
    y = te[label].astype(int).to_numpy()
    if len(y) < min_test or len(np.unique(y)) < 2 or n_sim_train < 10:
        return None, (f"Simulation ablation not computed: {int(simmed.sum())} simulated trials, {n_sim_train} labeled "
                      f"in train and {len(y)} labeled in test ({int(y.sum())} positive); needs >= 10 of each with both classes.")
    with_sim = train(tr, {"ref": ref, "use_sim": True, "bootstraps": 0, "version": "ablation-with"})
    without = train(tr, {"ref": ref, "use_sim": False, "bootstraps": 0, "version": "ablation-without"})
    if "accrual_fail" not in with_sim["models"] or "accrual_fail" not in without["models"]:
        return None, "Simulation ablation not computed: too few labeled training trials."
    p1, p0 = predict_p(with_sim, "accrual_fail", te), predict_p(without, "accrual_fail", te)
    diff = paired_diff_ci(y, p1, p0)
    return Ablation(target=Target.accrual_fail, n_trials=len(y), auc_with=float(roc_auc_score(y, p1)),
                    auc_without=float(roc_auc_score(y, p0)), diff=diff), (
        f"Simulation ablation on {len(y)} simulated test trials ({n_sim_train} simulated labeled training trials).")


def hit_rate(bundle: Mapping[str, Any], df: pd.DataFrame, k: int = 3) -> tuple[HitRate | None, str]:
    """Among test trials with label_amend whose amended rules are parsed (criteria_rules version 1):
    share where a relaxed or removed field is among our top-k flags, vs k random rules (exact
    expectation) and the k rules with the largest marginal exclusion on the virtual cohort."""
    from clinova.model.features import load_protocols, screen_cohort, screenable
    from clinova.model.predict import flag_criteria
    from clinova.parse.diff import diff_rules
    from clinova.simulate.screen import screen

    ids = df.index[(df["split"] == "test") & (df["label_amend"] == True)].tolist()  # noqa: E712
    if not ids:
        return None, "Hit rate not computed: no test trials with label_amend yet (version histories not loaded)."
    first, amended = load_protocols(ids, 0), load_protocols(ids, 1)
    hits, rand, marg, n = 0, 0.0, 0, 0
    for nid in ids:
        if nid not in first or nid not in amended:
            continue
        widened = {c.field for c in diff_rules(first[nid].rules, amended[nid].rules) if c.change in ("relaxed", "removed")}
        rules = [r for r in screenable(first[nid].rules) if r.parse_status == "ok" and not r.locked]
        if not widened or not rules:
            continue
        n += 1
        flags = flag_criteria(first[nid], k=k, bundle=bundle)
        hits += any(f.field in widened for f in flags)
        m, total = sum(r.field in widened for r in rules), len(rules)
        rand += 1 - (math.comb(total - m, k) / math.comb(total, k) if total >= k else float(m == 0))
        _, funnel = screen(rules, screen_cohort())
        top = {x.field for x in funnel.marginal[:k]}
        marg += bool(top & widened)
    if not n:
        return None, f"Hit rate not computed: {len(ids)} amended test trials, none with both versions parsed and a widened field."
    return HitRate(n_trials=n, hit_rate=hits / n, random_baseline=rand / n, marginal_baseline=marg / n), (
        f"Hit rate over {n} amended test trials with a parseable widened field.")


# ---------------------------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------------------------


def _axes_style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_reliability(metrics: list[TargetMetrics], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, max(1, len(metrics)), figsize=(5 * max(1, len(metrics)), 4.6), squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax, m in zip(axes[0], metrics, strict=False):
        _axes_style(ax)
        ax.plot([0, 1], [0, 1], color=INK2, linewidth=1, linestyle="--", label="Perfect calibration")
        pts = [(b.mean_pred, b.frac_pos, b.n) for b in m.reliability if b.n]
        x, yv, n = zip(*pts, strict=True) if pts else ((), (), ())
        ax.plot(x, yv, color=BLUE, linewidth=2, marker="o", markersize=8, markeredgecolor=SURFACE, markeredgewidth=2,
                label="LightGBM, isotonic")
        for xi, yi, ni in pts:
            ax.annotate(f"n={ni}", (xi, yi), textcoords="offset points", xytext=(6, -12), fontsize=8, color=INK2)
        ax.set(xlim=(0, 1), ylim=(0, 1))
        ax.set_xlabel("Mean predicted probability", color=INK, fontsize=10)
        ax.set_ylabel("Observed share", color=INK, fontsize=10)
        ax.set_title(f"{m.target.value}: Brier {m.brier:.3f}, n={m.n_test} test trials", color=INK, fontsize=11, loc="left")
        ax.legend(frameon=False, fontsize=9, labelcolor=INK, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def plot_shap_importance(bundle: Mapping[str, Any], X: pd.DataFrame, target: str, path: Path, top: int = 15) -> list[dict]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from clinova.model.predict import shap_values

    Xf = X.reindex(columns=bundle["feature_names"])
    imp = np.abs(shap_values(bundle["models"][target], Xf)).mean(axis=0)
    order = [j for j in np.argsort(-imp)[:top] if imp[j] > 0]
    rows = [{"feature": Xf.columns[j], "label": FEATURE_NAMES.get(Xf.columns[j], Xf.columns[j]),
             "mean_abs_shap": float(imp[j])} for j in order]
    fig, ax = plt.subplots(figsize=(8.5, 0.38 * max(len(rows), 3) + 1.2))
    fig.patch.set_facecolor(SURFACE)
    _axes_style(ax)
    ax.grid(axis="y", visible=False)
    labels = [r["label"] for r in rows][::-1]
    vals = [r["mean_abs_shap"] for r in rows][::-1]
    ax.barh(labels, vals, color=BLUE, height=0.6)
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:.2f}", va="center", fontsize=8, color=INK2)
    ax.set_xlabel(f"Mean |SHAP| (log-odds) on the test split, model {bundle['version']}", color=INK, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.text(0.01, 0.98, f"What drives predicted {target.replace('_', ' ')} risk", color=INK, fontsize=12, va="top")
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return rows


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------


def evaluate(bundle: Mapping[str, Any], df: pd.DataFrame, *, out_dir: Path | None = None, with_hit_rate: bool = True,
             with_ablation: bool = True) -> ValidationMetrics:
    """Metrics for ``bundle`` on the test rows of ``df`` (features built with the bundle's ref).
    Writes ``<out_dir>/metrics.json`` and ``<out_dir>/figures/*.png`` (default: the repo's docs/)."""
    out_dir = Path(out_dir or REPO_ROOT / "docs")
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    test, train_rows = df[df["split"] == "test"], df[df["split"] == "train"]
    labeled = lambda d: int(d[list(LABELS.values())].notna().any(axis=1).sum())  # noqa: E731
    targets, notes = [], []
    for t in LABELS:
        m, note = target_metrics(bundle, test, t)
        if m:
            targets.append(m)
            tune = bundle["tuning"][t]
            notes.append(f"{t}: trained on {bundle['n_train'][t]['n']} labeled train trials ({bundle['n_train'][t]['pos']} positive); "
                         f"time-ordered tuning fit {tune['n_fit']} / validated {tune['n_val']} (val AUC "
                         f"{tune['val_auc'] if tune['val_auc'] is None else round(tune['val_auc'], 3)}), out-of-fold AUC {tune['oof_auc']:.3f}.")
        if note:
            notes.append(note)
    hr, note = hit_rate(bundle, df) if with_hit_rate else (None, "Hit rate skipped.")
    notes.append(note)
    ab, note = ablation(df, bundle["ref"]) if with_ablation else (None, "Ablation skipped.")
    notes.append(note)
    cov = {"parsed rules": "n_rules", "embedding": "emb_pc1", "first-registration sites": "n_sites"}
    notes.append("Feature coverage on test: " + ", ".join(
        f"{k} {test[c].notna().mean():.0%}" for k, c in cov.items() if c in test) + "; missing features are NaN, not imputed.")
    notes.append("Simulation features " + ("were used." if bundle["use_sim"] else
                 "were not used (fewer than 300 simulated trials); screen features on a fixed virtual cohort were."))
    notes.append("Data were still loading when this ran: numbers will move as more trials are labeled. Re-run "
                 "`python -m clinova.model train && python -m clinova.model evaluate`.")
    vm = ValidationMetrics(model_version=bundle["version"], generated_at=datetime.now(UTC), n_train=labeled(train_rows),
                           n_test=labeled(test), targets=targets, hit_rate=hr, ablation=ab, notes=notes)
    if targets:
        plot_reliability(targets, out_dir / "figures" / "reliability.png")
    for t in bundle["models"]:
        plot_shap_importance(bundle, test, t, out_dir / "figures" / f"shap_importance_{t}.png")
    (out_dir / "metrics.json").write_text(vm.model_dump_json(indent=1), encoding="utf-8")
    return vm
