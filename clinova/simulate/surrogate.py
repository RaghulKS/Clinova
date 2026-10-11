"""Fast fitted surrogate of synthetic persona-agent decisions."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from clinova.common.config import get_settings
from clinova.contracts import ParsedProtocol

BURDEN_COLUMNS = ("visit_count", "site_visits", "site_hours", "invasive_visits", "duration_days")
TRAITS = ("age", "ecog", "distance_km", "employed", "caregiver", "travel_access", "income_band", "risk_tolerance")
CATEGORIES = {"travel_access": {"easy": 0, "moderate": 1, "hard": 2},
              "income_band": {"low": 0, "middle": 1, "high": 2},
              "risk_tolerance": {"low": 0, "medium": 1, "high": 2}}


def burden_features(protocol: ParsedProtocol) -> dict[str, float]:
    visits = protocol.visits
    return dict(zip(BURDEN_COLUMNS, (len(visits), sum(v.requires_travel for v in visits),
                sum(v.est_hours for v in visits if v.requires_travel), sum(v.invasive for v in visits),
                max((v.day for v in visits), default=0) - min((min(0, v.day) for v in visits), default=0)), strict=True))


def _matrix(frame: pd.DataFrame, burden: dict[str, float] | None = None) -> np.ndarray:
    columns = []
    for name in (*BURDEN_COLUMNS, *TRAITS):
        if burden is not None and name in burden:
            columns.append(np.full(len(frame), burden[name], dtype=float))
            continue
        values = frame[name] if name in frame else pd.Series(np.nan, index=frame.index)
        if name in CATEGORIES:
            values = values.map(CATEGORIES[name])
        columns.append(pd.to_numeric(values, errors="coerce").to_numpy(dtype=float, na_value=np.nan))
    return np.column_stack(columns)


class Surrogate:
    """Regularized logistic fits; dropout is conditional on enrollment."""
    def __init__(self, state: dict):
        self.state = state

    @classmethod
    def fit(cls, outcomes: pd.DataFrame) -> Surrogate:
        """Accept persona_outcomes plus BURDEN_COLUMNS or a ParsedProtocol/dict `protocol` column.

        Unsampled rows (enrolled=NULL) and ineligible personas never become negative labels.
        """
        frame = outcomes.loc[outcomes["eligible"].eq(True) & outcomes["enrolled"].notna()].copy()
        if frame.empty:
            raise ValueError("no observed eligible agent decisions to fit")
        if "persona" in frame:
            traits = pd.DataFrame([{**p, **p.get("clinical", {})} for p in frame["persona"]], index=frame.index)
            for column in TRAITS:
                if column not in frame and column in traits:
                    frame[column] = traits[column]
        if "protocol" in frame:
            for column in BURDEN_COLUMNS:
                frame[column] = [burden_features(p if isinstance(p, ParsedProtocol) else ParsedProtocol.model_validate(p))[column]
                                 for p in frame["protocol"]]
        if not set(BURDEN_COLUMNS).issubset(frame.columns):
            raise ValueError("join protocol visit-burden columns before fitting")
        x = _matrix(frame)
        fill = np.array([np.median(c[np.isfinite(c)]) if np.isfinite(c).any() else 0 for c in x.T])
        x = np.where(np.isfinite(x), x, fill)
        mean, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale == 0] = 1
        x = (x - mean) / scale
        enrolled = frame["enrolled"].astype(bool).to_numpy()
        if not enrolled.any():
            raise ValueError("no enrolled agents: conditional dropout cannot be fitted")

        def fit_target(features, labels):
            if len(np.unique(labels)) == 1:
                return {"constant": float(labels[0])}
            model = LogisticRegression(C=1, max_iter=1000, random_state=42).fit(features, labels)
            return {"coef": model.coef_[0].tolist(), "intercept": float(model.intercept_[0])}

        return cls({"version": 1, "fill": fill.tolist(), "mean": mean.tolist(), "scale": scale.tolist(),
                    "enroll": fit_target(x, enrolled),
                    "dropout": fit_target(x[enrolled], frame.loc[enrolled, "dropout_visit"].notna().to_numpy())})

    def score(self, protocol: ParsedProtocol, cohort: pd.DataFrame) -> tuple[float, float]:
        if cohort.empty:
            return 0.0, 0.0
        x = _matrix(cohort, burden_features(protocol))
        enroll, dropout = self._probabilities(x)
        return float(enroll.mean()), float(np.sum(enroll * dropout) / enroll.sum()) if enroll.sum() else 0.0

    def _probabilities(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Per-decision probabilities, also used by grouped held-out validation."""
        x = (np.where(np.isfinite(x), x, self.state["fill"]) - self.state["mean"]) / self.state["scale"]

        def predict(model):
            if "constant" in model:
                return np.full(len(x), model["constant"])
            return 1 / (1 + np.exp(-np.clip(np.sum(x * np.asarray(model["coef"]), axis=1) + model["intercept"], -40, 40)))

        return predict(self.state["enroll"]), predict(self.state["dropout"])

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> Surrogate:
        path = Path(path) if path is not None else get_settings().DATA_DIR / "models" / "surrogate.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != 1:
            raise ValueError("unsupported surrogate version")
        return cls(state)
