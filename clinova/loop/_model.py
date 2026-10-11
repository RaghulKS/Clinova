"""Explicit temporary logistic trainer until T22 is merged; no outcome-side features."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

NUMERIC = ["planned_enrollment", "n_sites", "n_countries", "n_arms", "min_age_years", "max_age_years"]
CATEGORICAL = ["phase", "sponsor_class", "allocation", "masking"]


def features(df: pd.DataFrame) -> pd.DataFrame:
    """Use only T11's frozen first-registration columns, excluding empty originals."""
    x = df.reindex(columns=NUMERIC + CATEGORICAL).copy()
    for col in NUMERIC:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    for col in CATEGORICAL:
        x[col] = x[col].fillna("unknown").astype(str)
    return x


def train(df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Same bundle signature as T22; cfg save is handled by the caller, never shared paths."""
    models = {}
    x = features(df)
    for target in ("accrual_fail", "shortfall", "amend"):
        y = pd.to_numeric(df.get("label_" + target, pd.Series(index=df.index, dtype=float)), errors="coerce")
        known = y.notna()
        if not known.any():
            continue
        estimator = LogisticRegression(max_iter=1000, random_state=42)
        if y[known].nunique() == 1:
            estimator = DummyClassifier(strategy="prior")
        preprocess = ColumnTransformer([
            ("numeric", make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                                      StandardScaler()), NUMERIC),
            ("category", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
        ])
        models[target] = make_pipeline(preprocess, estimator).fit(x.loc[known], y[known].astype(int))
    digest = hashlib.sha256(pd.util.hash_pandas_object(pd.concat([
        x, df.reindex(columns=["nct_id", "label_accrual_fail", "label_shortfall", "label_amend"])], axis=1),
        index=False,
    ).values.tobytes()).hexdigest()[:12]
    return {"version": "T21-placeholder-logistic-" + digest, "models": models, "calibrators": {},
            "baseline": {}, "bootstraps": {}, "feature_names": list(x), "placeholder": True}


def probabilities(bundle: dict, df: pd.DataFrame, target: str):
    model = bundle["models"][target]
    classes = list(model.classes_)
    p = model.predict_proba(features(df))
    return p[:, classes.index(1)] if 1 in classes else p[:, 0] * 0
