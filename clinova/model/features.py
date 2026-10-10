"""Model features (owner: T22). First-version data only. See ARCHITECTURE.md 5.6."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from clinova.contracts import ParsedProtocol


def build_features(
    trials: pd.DataFrame,
    protocols: Mapping[str, ParsedProtocol] | None = None,
    sim_features: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per trial, indexed by trial id.

    ``trials`` has ``trials`` table columns (first-version fields only are used); ``protocols``
    maps trial id -> first-version ParsedProtocol; ``sim_features`` is the ``sim_features`` view
    (NaN when absent). Excludes start year and anything observed after the first version.
    """
    raise NotImplementedError("T22 implements clinova.model.features.build_features")
