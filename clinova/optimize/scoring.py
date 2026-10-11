"""Optimizer scoring adapters; unavailable fitted components are explicitly identified."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np
import pandas as pd

from clinova.common.config import get_settings
from clinova.contracts import ParsedProtocol, RiskScore, SimRunSummary, Target


@dataclass(frozen=True)
class Score:
    eligible_frac: float
    enroll_rate: float
    dropout_risk: float
    p_shortfall: float
    p_amend: float
    # Paired model draws: the same index must identify the same fitted bootstrap model.
    bootstrap_risks: tuple[tuple[float, float], ...] = ()

    def __post_init__(self):
        values = (self.eligible_frac, self.enroll_rate, self.dropout_risk, self.p_shortfall, self.p_amend)
        if any(not np.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("scorer probabilities must be finite and in [0, 1]")
        if any(len(pair) != 2 for pair in self.bootstrap_risks):
            raise ValueError("bootstrap draws must contain (shortfall, amend) pairs")
        if any(not np.isfinite(v) or not 0 <= v <= 1 for pair in self.bootstrap_risks for v in pair):
            raise ValueError("bootstrap probabilities must be finite and in [0, 1]")

    @property
    def enrollment_speed(self) -> float:
        return self.eligible_frac * self.enroll_rate

    @property
    def adjusted_speed(self) -> float:
        return self.enrollment_speed * (1 - self.p_shortfall)


class Scorer(Protocol):
    """Score rules/visits against their already-screened, eligible virtual cohort."""

    source: str

    def score(self, protocol: ParsedProtocol, eligible: pd.DataFrame, eligible_frac: float) -> Score: ...


class StubScorer:
    """Uncalibrated monotone development stub, never presented as a fitted model."""

    source = "heuristic stub (uncalibrated; no bootstrap models)"

    def score(self, protocol: ParsedProtocol, eligible: pd.DataFrame, eligible_frac: float) -> Score:
        visits = protocol.visits
        hours = sum(v.est_hours for v in visits if v.requires_travel)
        travel = sum(v.requires_travel for v in visits)
        invasive = sum(v.invasive for v in visits)
        enroll = float(1 / (1 + np.exp(-(1.8 - .08 * travel - .025 * hours - .12 * invasive))))
        dropout = float(np.clip(.08 + .018 * travel + .007 * hours + .03 * invasive, 0, .95))
        if eligible.empty:
            enroll, dropout = 0.0, 0.0
        shortfall = float(np.clip(.55 - .45 * eligible_frac + .2 * dropout, .02, .98))
        amend = float(np.clip(.1 + .5 * (1 - eligible_frac) + .25 * dropout + .003 * len(protocol.rules), .02, .98))
        return Score(eligible_frac, enroll, dropout, shortfall, amend)


class ModelScorer:
    """Use the fitted behavior surrogate and T22 risk model, with explicit interim fallbacks.

    ``bootstrap_fn`` returns paired (shortfall, amend) probabilities for actual bootstrap
    models. Risk-score CI endpoints are never substituted for model draws.
    """

    def __init__(self, *, surrogate=None, risk_fn: Callable | None = None,
                 bootstrap_fn: Callable | None = None):
        self.stub = StubScorer()
        self._model_missing = False
        self._surrogate_missing = False
        if surrogate is None:
            path = get_settings().DATA_DIR / "models" / "surrogate.json"
            # Missing artifacts are common before T24. Avoid importing sklearn just
            # to discover that no fitted surrogate exists (several seconds cold).
            if not path.exists():
                self._surrogate_missing = True
            else:
                from clinova.simulate.surrogate import Surrogate
                surrogate = Surrogate.load(path)
        self.surrogate = surrogate
        if risk_fn is None:
            from clinova.model import predict
            risk_fn = predict.score
            bootstrap_fn = bootstrap_fn or getattr(predict, "score_samples", None)
        self.risk_fn = risk_fn
        self.bootstrap_fn = bootstrap_fn
        self._refresh_source()

    def _refresh_source(self):
        behavior = "heuristic behavior" if self._surrogate_missing else "fitted Surrogate"
        risk = "heuristic risk (uncalibrated)" if self._model_missing else "T22 risk model"
        samples = "bootstrap models" if self.bootstrap_fn and not self._model_missing else "no bootstrap models"
        self.source = f"{behavior}; {risk}; {samples}"

    def score(self, protocol: ParsedProtocol, eligible: pd.DataFrame, eligible_frac: float) -> Score:
        fallback = self.stub.score(protocol, eligible, eligible_frac)
        enroll, dropout = (self.surrogate.score(protocol, eligible) if self.surrogate is not None
                           else (fallback.enroll_rate, fallback.dropout_risk))
        sim = [SimRunSummary(
            run_id="optimizer-ephemeral", trial_id=protocol.trial_id, version_no=protocol.version_no,
            seed=0, model_version="optimizer/surrogate", n_personas=(round(len(eligible) / eligible_frac)
                if eligible_frac else 0), n_eligible=len(eligible),
            eligible_frac=eligible_frac, n_agents=0, enroll_rate=enroll, completion_rate=1 - dropout,
            # Aggregate surrogate predictions establish no visit-level timing curve.
            dropout_curve=[], run_at=datetime.now(UTC),
        )]
        risks = None
        samples: tuple[tuple[float, float], ...] = ()
        if not self._model_missing:
            try:
                scores: Sequence[RiskScore] = self.risk_fn(protocol, sim=sim)
                risks = {s.target: s.p for s in scores}
            except (NotImplementedError, FileNotFoundError):
                self._model_missing = True
                self._refresh_source()
        if risks is not None and self.bootstrap_fn is not None:
            try:
                samples = tuple(tuple(map(float, pair)) for pair in self.bootstrap_fn(protocol, sim=sim))
            except (NotImplementedError, FileNotFoundError):
                # A missing bootstrap artifact does not invalidate a fitted point model.
                self.bootstrap_fn = None
                self._refresh_source()
        return Score(eligible_frac, float(enroll), float(dropout),
                     risks[Target.shortfall] if risks is not None else fallback.p_shortfall,
                     risks[Target.amend] if risks is not None else fallback.p_amend, samples)
