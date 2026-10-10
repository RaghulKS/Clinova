"""Protocol optimizer (owner: T23). See ARCHITECTURE.md 5.8 and docs/CONTRACTS.md."""

from __future__ import annotations

from clinova.contracts import Fix, ParsedProtocol


def optimize(protocol: ParsedProtocol, *, max_edits: int = 3, n_trials: int = 300, seed: int = 42) -> list[Fix]:
    """Search combinations of up to ``max_edits`` edits (Optuna NSGA-II; locked rules untouched)
    over three objectives: enrollment speed, dropout risk, diversity. Returns three Fixes labelled
    ``fastest``, ``lowest_dropout`` and ``balanced``, with ranges across 5 cohort seeds and the
    bootstrap models, the expected dollar value of avoided amendments and precedent trials.
    Target: under 10 seconds."""
    raise NotImplementedError("T23 implements clinova.optimize.search.optimize")
