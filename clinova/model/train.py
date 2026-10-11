"""Model training (owner: T22). LightGBM per label, isotonic calibration, logistic baseline, bootstraps.

Re-runnable as labels land: every target with enough labeled trials of both classes gets a model;
the others are listed in ``bundle["skipped"]`` and simply not scored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from clinova.common.config import get_settings
from clinova.model.features import LABELS, SIM_COLS, TUNE_DATE

TARGETS = tuple(LABELS)
META_COLS = ("split", "start_date", *LABELS.values())
MODELS_DIR = get_settings().MODELS_DIR  # default <main clone>/models, shared by every worktree; gitignored
MIN_SIM_TRIALS = 300

# G2: features computed from the first registered version's JSON alone (T11 mapping + visit estimate).
# Rule, screen and embedding features exist only for trials T12 has already parsed/embedded, and that
# processing order is label-correlated (train: 63% accrual failures among parsed trials vs 6% among the
# rest), so their missingness is a proxy for the outcome and every deployed (parsed) protocol would be
# scored as if it were in the high-risk "parsed" group. v1.0 therefore uses only these features.
# ponytail: switch back to the full set (feature_set="all") once every trial is parsed and embedded.
FIRST_VERSION_FEATURES = [
    "text_len", "phase_num", "n_arms", "randomized", "masking_level", "iv_drug", "iv_biological", "iv_radiation",
    "iv_other", "log_planned_enrollment", "n_sites", "n_countries", "us_share", "sponsor_industry", "sponsor_gov",
    "age_min", "age_max", "visit_count", "visit_hours", "visit_invasive", "visit_travel",
]
BASELINE_FEATURES = [  # up to ten interpretable features for the logistic baseline (those in the feature set)
    "phase_num", "log_planned_enrollment", "n_arms", "randomized", "sponsor_industry",
    "n_rules", "n_unparseable", "text_len", "visit_hours", "screen_eligible_frac",
]
BASE_PARAMS = dict(learning_rate=0.03, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                   verbose=-1, n_jobs=1)
GRID = [dict(num_leaves=nl, min_child_samples=mc, n_estimators=ne)
        for nl in (4, 8) for mc in (5, 10) for ne in (100, 300)]
DEFAULTS: dict[str, Any] = {
    "bootstraps": 30, "save": False, "version": None, "seed": 42, "min_class": 5, "cv_folds": 5,
    "tune_date": TUNE_DATE, "features": None, "use_sim": None, "ref": None, "models_dir": None, "grid": GRID,
    "allow_non_train": False,
}


def _lgbm(params: Mapping[str, Any], seed: int) -> LGBMClassifier:
    return LGBMClassifier(**BASE_PARAMS, **params, random_state=seed)


def _tune(d: pd.DataFrame, feats: list[str], label: str, cfg: Mapping[str, Any]) -> tuple[dict, dict]:
    """Time-ordered tuning inside train: fit on start < tune_date, validate on the rest."""
    cut = pd.Timestamp(cfg["tune_date"])
    start = pd.to_datetime(d["start_date"])
    fit, val = d[start < cut], d[start >= cut]
    info = {"n_fit": len(fit), "n_val": len(val), "val_auc": None, "tuned": False}
    yf, yv = fit[label].astype(int), val[label].astype(int)
    if yf.nunique() < 2 or yv.nunique() < 2 or len(fit) < 10:
        return dict(cfg["grid"][0]), info  # too little data on one side: simplest grid point
    best, best_auc = cfg["grid"][0], -1.0
    for params in cfg["grid"]:
        m = _lgbm(params, cfg["seed"]).fit(fit[feats], yf)
        auc = roc_auc_score(yv, m.predict_proba(val[feats])[:, 1])
        if auc > best_auc + 1e-9:  # ties keep the earlier (simpler) setting
            best, best_auc = params, auc
    info.update(val_auc=float(best_auc), tuned=True)
    return dict(best), info


def _oof(X: pd.DataFrame, y: np.ndarray, params: Mapping[str, Any], cfg: Mapping[str, Any]) -> np.ndarray:
    k = int(min(cfg["cv_folds"], np.bincount(y).min()))
    oof = np.zeros(len(y))
    for tr, te in StratifiedKFold(k, shuffle=True, random_state=cfg["seed"]).split(X, y):
        oof[te] = _lgbm(params, cfg["seed"]).fit(X.iloc[tr], y[tr]).predict_proba(X.iloc[te])[:, 1]
    return oof


def _bootstraps(X: pd.DataFrame, y: np.ndarray, params: Mapping[str, Any], cfg: Mapping[str, Any]) -> list:
    rng, out = np.random.default_rng(cfg["seed"]), []
    while len(out) < cfg["bootstraps"]:
        idx = rng.integers(0, len(y), len(y))
        if len(np.unique(y[idx])) == 2:
            out.append(_lgbm(params, cfg["seed"] + len(out) + 1).fit(X.iloc[idx], y[idx]))
    return out


def feature_list(df: pd.DataFrame, cfg: Mapping[str, Any]) -> list[str]:
    feats = ([c for c in cfg["features"] if c in df] if cfg.get("features")
             else [c for c in df.columns if c not in META_COLS])
    use_sim = cfg.get("use_sim")
    if use_sim is None:
        present = [c for c in SIM_COLS if c in df]
        use_sim = bool(present) and int(df[present].notna().any(axis=1).sum()) >= MIN_SIM_TRIALS
    if not use_sim:
        feats = [c for c in feats if c not in SIM_COLS]
    return feats


def train(df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Train on ``df`` (features from ``build_features`` plus label columns ``label_accrual_fail``,
    ``label_shortfall``, ``label_amend`` and ``start_date`` for time-ordered CV; train split only).

    Returns a bundle dict with at least ``{"version": str, "models": {target: ...},
    "calibrators": {...}, "baseline": {...}, "bootstraps": {...}, "feature_names": [...]}`` and
    saves it under ``models/<version>/`` when ``cfg["save"]`` is true. T21's replay calls this
    with smaller ``cfg`` (e.g. no bootstraps). Pass ``cfg["ref"]`` (``features.fit_ref``) so the
    bundle can score new protocols. Targets with fewer than ``min_class`` labeled trials of either
    class are skipped and listed in ``bundle["skipped"]``.

    Raises ValueError when ``df`` has a ``split`` column with any value other than ``train``, unless
    ``cfg["allow_non_train"]`` is true (T21's replay passes frames it already filtered by date)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if "split" in df and not cfg["allow_non_train"] and (bad := int((df["split"] != "train").sum())):
        raise ValueError(f"train() got {bad} rows whose split is not 'train'; pass training rows only "
                         "(or cfg['allow_non_train']=True for an already-filtered historical frame)")
    feats = feature_list(df, cfg)
    feats = [c for c in feats if df[c].notna().any()]  # all-NaN columns carry nothing
    version = cfg["version"] or "v0." + datetime.now(UTC).strftime("%Y%m%d%H%M")
    bundle: dict[str, Any] = {
        "version": version, "created_at": datetime.now(UTC).isoformat(), "feature_names": feats,
        "baseline_features": [c for c in BASELINE_FEATURES if c in feats], "ref": cfg["ref"],
        "use_sim": any(c in feats for c in SIM_COLS), "models": {}, "calibrators": {}, "baseline": {},
        "bootstraps": {}, "params": {}, "tuning": {}, "n_train": {}, "skipped": {}, "oof": {},
        "seed": cfg["seed"], "data_sha256": table_sha256(df, feats),
    }
    for target, label in LABELS.items():
        d = df[df[label].notna()]
        y = d[label].astype(int).to_numpy()
        n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
        bundle["n_train"][target] = {"n": len(y), "pos": n_pos}
        if min(n_pos, n_neg) < cfg["min_class"]:
            bundle["skipped"][target] = f"{n_pos} positive / {n_neg} negative labeled training trials (< {cfg['min_class']})"
            continue
        X = d[feats].astype(float)
        params, info = _tune(d, feats, label, cfg)
        oof = _oof(X, y, params, cfg)
        info["oof_auc"] = float(roc_auc_score(y, oof))
        bundle["calibrators"][target] = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(oof, y)
        bundle["models"][target] = _lgbm(params, cfg["seed"]).fit(X, y)
        bundle["bootstraps"][target] = _bootstraps(X, y, params, cfg) if cfg["bootstraps"] else []
        base = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                             LogisticRegression(C=1.0, max_iter=2000))
        bundle["baseline"][target] = base.fit(X[bundle["baseline_features"]], y)
        bundle["params"][target], bundle["tuning"][target] = params, info
        bundle["oof"][target] = {"ids": list(d.index), "p": oof.tolist(), "y": y.tolist()}
    if cfg["save"]:
        save_bundle(bundle, cfg["models_dir"])
    return bundle


# ---------------------------------------------------------------------------------------------
# Scoring helpers shared by evaluate and predict
# ---------------------------------------------------------------------------------------------


def table_sha256(df: pd.DataFrame, feats: Sequence[str]) -> str:
    """sha256 of the feature + label table (rows sorted by id, CSV text): identifies the exact data."""
    cols = [*feats, *(c for c in META_COLS if c in df)]
    return hashlib.sha256(df.reindex(columns=cols).sort_index().to_csv(lineterminator="\n").encode()).hexdigest()


def raw_proba(model, X: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(X.astype(float))[:, 1]


def calibrated(bundle: Mapping[str, Any], target: str, X: pd.DataFrame, model=None) -> np.ndarray:
    """Isotonic-calibrated probability from the main model (or ``model``, e.g. a bootstrap)."""
    X = X.reindex(columns=bundle["feature_names"])
    return bundle["calibrators"][target].predict(raw_proba(model or bundle["models"][target], X))


def models_of(bundle: Mapping[str, Any], target: str) -> list:
    return [bundle["models"][target], *bundle["bootstraps"].get(target, [])]


def draws(bundle: Mapping[str, Any], target: str, X: pd.DataFrame) -> np.ndarray:
    """Calibrated probabilities from the main and every bootstrap model, shape (models, rows)."""
    X = X.reindex(columns=bundle["feature_names"]).astype(float)
    key = (bundle["version"], bundle["created_at"], target, X.shape, X.to_numpy().tobytes())
    if key not in _DRAWS:
        if len(_DRAWS) > 4096:  # ponytail: crude bound; the optimizer re-scores identical feature rows
            _DRAWS.clear()
        _DRAWS[key] = np.stack([calibrated(bundle, target, X, m) for m in models_of(bundle, target)])
    return _DRAWS[key].copy()


_DRAWS: dict[tuple, np.ndarray] = {}


def predict_p(bundle: Mapping[str, Any], target: str, X: pd.DataFrame) -> np.ndarray:
    """The served probability: mean of the calibrated main and bootstrap models (smoother than
    one isotonic step function). ``score``, ``flag_criteria`` and the test metrics all use it."""
    return draws(bundle, target, X).mean(axis=0)


def baseline_proba(bundle: Mapping[str, Any], target: str, X: pd.DataFrame) -> np.ndarray:
    return bundle["baseline"][target].predict_proba(X.reindex(columns=bundle["baseline_features"]).astype(float))[:, 1]


# ---------------------------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------------------------


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return v


def save_bundle(bundle: Mapping[str, Any], models_dir: Path | None = None) -> Path:
    d = Path(models_dir or MODELS_DIR) / bundle["version"]
    d.mkdir(parents=True, exist_ok=True)
    joblib.dump(dict(bundle), d / "bundle.joblib", compress=3)
    meta = {k: bundle[k] for k in ("version", "created_at", "feature_names", "baseline_features", "use_sim", "params",
                                   "tuning", "n_train", "skipped")}
    (d / "meta.json").write_text(json.dumps(_json_safe(meta), indent=1), encoding="utf-8")
    return d


_CACHE: dict[tuple[str, float], dict] = {}


def bundle_path(version: str | None = None, models_dir: Path | None = None) -> Path:
    root = Path(models_dir or MODELS_DIR)
    pinned = get_settings().MODEL_VERSION  # served by default; a stray newer bundle must not replace it
    if not version and pinned and (root / pinned / "bundle.joblib").exists():
        version = pinned
    if version:
        p = root / version / "bundle.joblib"
    else:
        found: Sequence[Path] = sorted(root.glob("*/bundle.joblib"), key=lambda q: q.stat().st_mtime)
        if not found:
            raise FileNotFoundError(f"no model bundle under {root}; run `python -m clinova.model train`")
        p = found[-1]
    if not p.exists():
        raise FileNotFoundError(f"model bundle {p} not found")
    return p


def load_bundle(version: str | None = None) -> dict[str, Any]:
    """Load a saved bundle (``None`` = settings ``MODEL_VERSION`` when on disk, else the newest)."""
    p = bundle_path(version)
    key = (str(p), p.stat().st_mtime)
    if key not in _CACHE:
        _CACHE[key] = joblib.load(p)
    return _CACHE[key]
