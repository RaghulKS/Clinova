"""Model training (owner: T22). LightGBM per label, isotonic calibration, logistic baseline, bootstraps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd


def train(df: pd.DataFrame, cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Train on ``df`` (features from ``build_features`` plus label columns ``label_accrual_fail``,
    ``label_shortfall``, ``label_amend`` and ``start_date`` for time-ordered CV; train split only).

    Returns a bundle dict with at least ``{"version": str, "models": {target: ...},
    "calibrators": {...}, "baseline": {...}, "bootstraps": {...}, "feature_names": [...]}`` and
    saves it under ``models/<version>/`` when ``cfg["save"]`` is true. T21's replay calls this
    with smaller ``cfg`` (e.g. no bootstraps)."""
    raise NotImplementedError("T22 implements clinova.model.train.train")


def load_bundle(version: str | None = None) -> dict[str, Any]:
    """Load a saved bundle (``None`` = latest, or ``v1.0`` after Gate 2)."""
    raise NotImplementedError("T22 implements clinova.model.train.load_bundle")
