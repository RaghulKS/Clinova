"""Time-machine replay (owner: T21). Writes docs/replay.json and docs/figures/replay.png."""

from __future__ import annotations

from datetime import date

from clinova.contracts import ReplayResult, Target


def run_replay(
    *,
    freeze: date = date(2019, 12, 31),
    start: str = "2020-01",
    end: str = "2023-12",
    horizon_months: int = 6,
    target: Target = Target.accrual_fail,
) -> ReplayResult:
    """Freeze a model on outcomes observed by ``freeze`` (first-version features only), then step
    monthly from ``start`` to ``end``: score trials whose outcome arrives within ``horizon_months``
    with the current model, log those predictions (context 'replay', predicted_at = the month),
    add the month's outcomes and retrain. Compare with the never-retrained model."""
    raise NotImplementedError("T21 implements clinova.loop.replay.run_replay")
