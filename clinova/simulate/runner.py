"""Simulation runner (owner: T14, then T24). Tables ``sim_runs`` and ``persona_outcomes``."""

from __future__ import annotations

from collections.abc import Sequence

from clinova.contracts import ParsedProtocol, SimRunSummary


def simulate_trial(
    protocol: ParsedProtocol,
    n: int = 1000,
    seeds: Sequence[int] = (1, 2),
    max_agents: int = 200,
    *,
    persist: bool = True,
) -> list[SimRunSummary]:
    """For each seed: cohort (``generate_cohort(n, seed, protocol.setting)``) -> ``screen`` ->
    sample up to ``max_agents`` eligible personas -> ``run_agents`` -> SimRunSummary. When
    ``persist`` is True, writes one ``sim_runs`` row per seed and one ``persona_outcomes`` row per
    screened persona. Returns one summary per seed, in seed order."""
    raise NotImplementedError("T14 implements clinova.simulate.runner.simulate_trial")
