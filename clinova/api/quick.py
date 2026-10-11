"""Accepting a fix: the fast re-run (no new Gemini calls) and the before and after numbers.

Fast path: re-screen the cached cohort with the edited rules, ask the surrogate how enrollment and
dropout change under the edited visits, scale the run's own agent-based rates by that change, and
re-score with the model. Everything here is local and deterministic. When a stage is not available yet
(a stub raises ``NotImplementedError``) the caller falls back to the optimizer's own deltas.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np

from clinova.api import live
from clinova.api.runs import apply_edits_local
from clinova.api.schemas import Measures
from clinova.contracts import Edit, Fix, ParsedProtocol, RunResult, SimRunSummary

log = logging.getLogger("clinova.api")


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _ratio_scaled(real: float, surrogate_before: float, surrogate_after: float) -> float:
    """Scale a measured rate by the surrogate's relative change. If the surrogate saw nothing before,
    trust its absolute value for the after state."""
    if surrogate_before > 1e-9:
        return _clip01(real * surrogate_after / surrogate_before)
    return _clip01(surrogate_after)


def _resample_curve(curve: list[float], n_visits: int, scale: float) -> list[float]:
    if n_visits <= 0:
        return []
    if not curve:
        return [0.0] * n_visits
    x_old = np.linspace(0, 1, len(curve))
    x_new = np.linspace(0, 1, n_visits)
    out = np.clip(np.interp(x_new, x_old, curve) * scale, 0, 1)
    return [float(v) for v in np.maximum.accumulate(out)]


def _after_sim(sim: SimRunSummary, before: ParsedProtocol, after: ParsedProtocol) -> tuple[SimRunSummary, object]:
    """One seed of the fast re-run. Returns the new summary and the eligible dataframe (for diversity)."""
    cohort = live.make_cohort(sim.n_personas, sim.seed, before.setting.value)
    mask_b, _ = live.screen(before.rules, cohort, before.trial_id)
    mask_a, funnel_a = live.screen(after.rules, cohort, after.trial_id)
    elig_b, elig_a = cohort[mask_b], cohort[mask_a]
    n_elig = int(mask_a.sum())
    if n_elig == 0:
        enroll, dropout = 0.0, 0.0
    else:
        enr_b, drop_b = live.surrogate_score(before, elig_b) if len(elig_b) else (0.0, 0.0)
        enr_a, drop_a = live.surrogate_score(after, elig_a)
        enroll = _ratio_scaled(sim.enroll_rate, enr_b, enr_a)
        real_drop = 1.0 - sim.completion_rate
        dropout = _ratio_scaled(real_drop, drop_b, drop_a) if drop_b > 1e-9 else _clip01(drop_a)
    scale = dropout / (1.0 - sim.completion_rate) if sim.completion_rate < 1.0 else 0.0
    new = sim.model_copy(
        update={
            "run_id": f"{sim.run_id}-quick",
            "version_no": sim.version_no + 1,
            "n_eligible": n_elig,
            "eligible_frac": n_elig / max(1, sim.n_personas),
            "n_agents": 0,
            "enroll_rate": enroll,
            "completion_rate": 1.0 - dropout,
            "dropout_curve": _resample_curve(sim.dropout_curve, len(after.visits), scale)
            if scale
            else [0.0] * len(after.visits),
            "funnel": funnel_a,
            "run_at": datetime.now(UTC),
        }
    )
    return new, elig_a


def quick_rerun(before: RunResult, edits: list[Edit]) -> tuple[RunResult, ParsedProtocol, float | None]:
    """Apply ``edits`` to the run's protocol and re-run screen, surrogate and score.

    Returns ``(after_result, after_protocol, diversity_after)``. Raises ``NotImplementedError`` or
    ``LiveUnavailable`` when a stage is not available (the caller falls back), ``ValueError`` for a bad edit.
    """
    if not before.sim:
        raise live.LiveUnavailable("the run has no simulation to scale")
    protocol_after = apply_edits_local(before.protocol, edits)
    sims_after, elig_first = [], None
    for sim in before.sim:
        s, elig = _after_sim(sim, before.protocol, protocol_after)
        sims_after.append(s)
        elig_first = elig if elig_first is None else elig_first
    risks = live.score(protocol_after, sims_after)
    div = None
    try:
        if elig_first is not None and len(elig_first):
            div = live.diversity(elig_first)
    except NotImplementedError:
        pass
    after = RunResult(
        protocol=protocol_after,
        funnel=sims_after[0].funnel,
        sim=sims_after,
        risks=risks,
        flags=[],
        fixes=[],
    )
    return after, protocol_after, div


def measures(res: RunResult, diversity_index: float | None = None) -> Measures:
    sims = res.sim
    risk = {r.target.value: r.p for r in res.risks}
    n = len(sims)
    return Measures(
        eligible_frac=(sum(s.eligible_frac for s in sims) / n) if n else (res.funnel.eligible_frac if res.funnel else None),
        enroll_rate=(sum(s.enroll_rate for s in sims) / n) if n else None,
        dropout_rate=(1.0 - sum(s.completion_rate for s in sims) / n) if n else None,
        diversity_index=diversity_index,
        accrual_fail_p=risk.get("accrual_fail"),
        shortfall_p=risk.get("shortfall"),
        amend_p=risk.get("amend"),
    )


def before_diversity(before: RunResult) -> float | None:
    """Diversity of the eligible virtual patients of the run's first seed, when the module exists."""
    try:
        sim = before.sim[0]
        cohort = live.make_cohort(sim.n_personas, sim.seed, before.protocol.setting.value)
        mask, _ = live.screen(before.protocol.rules, cohort, before.protocol.trial_id)
        return live.diversity(cohort[mask]) if mask.any() else None
    except (NotImplementedError, live.LiveUnavailable, ImportError):
        return None


def estimate_after(before_m: Measures, fix: Fix) -> Measures:
    """The optimizer's own deltas applied to the before numbers (used when the fast re-run is unavailable)."""

    def add(v: float | None, d: float) -> float | None:
        return None if v is None else _clip01(v + d)

    return Measures(
        eligible_frac=None,
        enroll_rate=None if before_m.enroll_rate is None else _clip01(before_m.enroll_rate * (1 + fix.delta_enroll)),
        dropout_rate=add(before_m.dropout_rate, fix.delta_dropout),
        diversity_index=None if before_m.diversity_index is None else before_m.diversity_index + fix.delta_diversity,
        accrual_fail_p=None,  # the optimizer only estimates amendment risk; never show an unchanged number as a result
        shortfall_p=None,
        amend_p=add(before_m.amend_p, fix.delta_p_amend),
    )
