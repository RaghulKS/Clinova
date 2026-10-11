"""Read-only trial snapshots and the T22 model adapter used by the learning loop."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date
from typing import Any

import pandas as pd
from psycopg.rows import dict_row

from clinova.common.db import get_conn
from clinova.contracts import ENDED_STATUSES
from clinova.model.features import (
    LABELS,
    apply_ref,
    build_features,
    fit_ref,
    load_embeddings,
    load_protocols,
    load_sim_features,
)
from clinova.model.train import FIRST_VERSION_FEATURES, predict_p, train

# Replay arms share one fixed setting: no tuning (one-point grid), no bootstraps, nothing saved, and
# v1.0's first-registration feature set (see model.train.FIRST_VERSION_FEATURES).
REPLAY_CFG: dict[str, Any] = {"save": False, "bootstraps": 0, "use_sim": False, "features": FIRST_VERSION_FEATURES,
                              "grid": [{"num_leaves": 4, "min_child_samples": 5, "n_estimators": 100}]}
CHECKER_CFG: dict[str, Any] = {"save": False, "features": FIRST_VERSION_FEATURES}


def training_frame() -> pd.DataFrame:
    """Exact completion dates are retrospective event times, not CT.gov publication times."""
    with get_conn() as conn:
        rows = conn.cursor(row_factory=dict_row).execute(
            "SELECT * FROM trials t WHERE first_version <> '{}'::jsonb AND "
            "(status = ANY(%s) OR EXISTS (SELECT 1 FROM outcomes o WHERE o.nct_id=t.nct_id "
            "AND o.label='status' AND o.source LIKE 'ctgov:T21%%')) "
            "AND start_date IS NOT NULL ORDER BY nct_id",
            (list(ENDED_STATUSES),),
        ).fetchall()
        # Checker updates belong to outcomes; T11 retains ownership of trials.
        updates = conn.execute(
            "SELECT DISTINCT ON (nct_id,label) nct_id,label,value,observed_at FROM outcomes "
            "WHERE source LIKE 'ctgov:T21%' AND label <> 'status' "
            "ORDER BY nct_id,label,observed_at DESC"
        ).fetchall()
    by_id = {r["nct_id"]: r for r in rows}
    for nct, label, value, observed in updates:
        if nct in by_id and value is not None:
            by_id[nct]["label_" + label] = bool(value)
            by_id[nct]["completion_date"] = observed.date()
    return pd.DataFrame([r for r in rows if r["completion_date"] is not None])


def available_training(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Preserve the registered pre-2018 training split and the replay observation cutoff."""
    if df.empty:
        return df.copy()
    mask = (df["start_date"] < date(2018, 1, 1)) & (df["split"] != "test") & (df["completion_date"] <= cutoff)
    mask &= df["first_version_date"].notna() & (df["first_version_date"] <= cutoff)
    return df.loc[mask].copy()


def load_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    """T22's raw first-version features (``build_features`` reads only nct_id and first_version),
    plus embeddings and parsed protocols for ``fit_ref``. No Gemini calls."""
    ids = frame["nct_id"].tolist()
    protocols, embeddings = load_protocols(ids), load_embeddings(ids)
    return build_features(frame, protocols, load_sim_features(ids)), embeddings, protocols


def fit(rows: pd.DataFrame, features: tuple, cfg: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """``model.train.train`` on ``rows`` (already ``available_training``). The reference (medians,
    embedding PCA) is fitted on these rows only, never on later or test-split trials."""
    raw, embeddings, protocols = features
    ids = rows["nct_id"].tolist()
    ref = fit_ref(raw.loc[ids], embeddings, {i: protocols[i] for i in ids if i in protocols})
    x = apply_ref(raw.loc[ids], embeddings, ref)
    # T11's own rule: an ended trial with an exact pre-2018 start is train split. Checker-observed
    # endings keep T11's stale 'pending' in trials until re-ingested; available_training dropped 'test'.
    x["split"] = "train"
    x["start_date"] = rows["start_date"].to_numpy()
    for label in LABELS.values():
        x[label] = rows[label].astype(float).to_numpy()  # unknown (None) stays NaN and is excluded
    version = prefix + hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()[:12]
    return train(x, {**cfg, "ref": ref, "version": version})


def probabilities(bundle: Mapping[str, Any], rows: pd.DataFrame, features: tuple, target: str):
    """T22's served probability (calibrated main model; mean over bootstraps when present)."""
    raw, embeddings, _ = features
    return predict_p(bundle, target, apply_ref(raw.loc[rows["nct_id"].tolist()], embeddings, bundle["ref"]))
