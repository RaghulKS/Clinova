"""Cached multiobjective protocol search, uncertainty ranges and expected amendment value."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import optuna
import pandas as pd

from clinova.cohort.generate import generate_cohort_df
from clinova.common.config import amendment_cost
from clinova.contracts import Edit, Fix, FixRanges, Interval, ParsedProtocol
from clinova.optimize.edits import apply_protocol_edits, build_edit_space
from clinova.optimize.scoring import ModelScorer, Score, Scorer
from clinova.simulate.diversity import diversity_index
from clinova.simulate.screen import screen


@dataclass(frozen=True)
class Candidate:
    edits: tuple[Edit, ...]
    protocol: ParsedProtocol
    score: Score
    diversity: float

    @property
    def objectives(self) -> tuple[float, float, float]:
        return self.score.adjusted_speed, -self.score.dropout_risk, self.diversity


@dataclass
class OptimizationResult:
    fixes: list[Fix]
    pareto_front: list[Candidate]
    evaluated_n: int
    elapsed_s: float
    scorer_source: str
    cohort_seeds: tuple[int, ...]
    bootstrap_models_n: int
    notes: list[str]


class _InadmissibleEdit(ValueError):
    """A candidate cannot be applied; scorer errors must remain visible."""


def _key(edits: Sequence[Edit]) -> str:
    return json.dumps(sorted((e.model_dump(mode="json") for e in edits),
                             key=lambda e: (e["kind"], e["target_id"])), sort_keys=True)


@lru_cache(maxsize=16)
def _cohort(n: int, seed: int, setting: str) -> pd.DataFrame:
    # Every evaluation reads these cached frames and never mutates them.
    return generate_cohort_df(n, seed, setting)


class _Evaluation:
    def __init__(self, frame: pd.DataFrame, scorer: Scorer):
        self.frame, self.scorer = frame, scorer
        self.rule_masks: dict[str, np.ndarray] = {}
        self.screen_cache: dict[bytes, tuple[pd.DataFrame, float, float]] = {}

    def __call__(self, protocol: ParsedProtocol, edits: Sequence[Edit] = ()) -> Candidate:
        mask = np.ones(len(self.frame), dtype=bool)
        for rule in protocol.rules:
            key = rule.model_dump_json()
            if key not in self.rule_masks:
                self.rule_masks[key] = screen([rule], self.frame, trial_id=protocol.trial_id)[0]
            mask &= self.rule_masks[key]
        key = mask.tobytes()
        if key not in self.screen_cache:
            eligible = self.frame.loc[mask]
            fraction = float(mask.mean()) if len(mask) else 0.0
            self.screen_cache[key] = eligible, fraction, diversity_index(eligible)
        eligible, fraction, diversity = self.screen_cache[key]
        score = self.scorer.score(protocol, eligible, fraction)
        return Candidate(tuple(edits), protocol, score, diversity)


def pareto_front(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Deduplicate edit combinations and retain nondominated objective vectors."""
    unique = {_key(c.edits): c for c in candidates}
    values = list(unique.values())
    front = []
    for candidate in values:
        x = candidate.objectives
        if not any(all(a >= b for a, b in zip(other.objectives, x, strict=True))
                   and any(a > b for a, b in zip(other.objectives, x, strict=True)) for other in values):
            front.append(candidate)
    return sorted(front, key=lambda c: _key(c.edits))


def pick_representatives(front: Sequence[Candidate]) -> list[tuple[str, Candidate]]:
    """Pick each objective's representative; small fronts may share a combination."""
    if not front:
        return []
    values = np.array([c.objectives for c in front])
    span = np.ptp(values, axis=0)
    normalized = np.divide(values - values.min(axis=0), span,
                           out=np.ones_like(values), where=span > 0)
    # Smallest distance to the ideal gives each objective equal weight.
    balance = np.linalg.norm(1 - normalized, axis=1)
    fastest = max(range(len(front)), key=lambda i: (values[i, 0], values[i, 1], -len(front[i].edits)))
    # Prefer distinct edit combinations among exact ties, preserving the extreme
    # objective. Balance chooses the closest remaining point when one exists.
    dropout = max(range(len(front)), key=lambda i: (values[i, 1], i != fastest,
                                                   values[i, 0], -len(front[i].edits)))
    remaining = [i for i in range(len(front)) if i not in {fastest, dropout}]
    balanced = min(remaining or list(range(len(front))),
                   key=lambda i: (balance[i], len(front[i].edits), _key(front[i].edits)))
    return [("fastest", front[fastest]), ("lowest_dropout", front[dropout]), ("balanced", front[balanced])]


def _relative(after: float, before: float) -> float:
    # Relative speed is undefined at zero. Never fabricate an infinite or percentage gain.
    return after / before - 1 if before > 0 else 0.0


def _interval(values: Sequence[float], point: float) -> Interval:
    low, high = np.quantile(values, [.025, .975])
    return Interval(low=min(float(low), point), high=max(float(high), point))


def _ranges(protocol: ParsedProtocol, candidate: Candidate, base: Candidate,
            scorer: Scorer, seeds: Sequence[int], n: int, cohort_factory: Callable) -> tuple[FixRanges, int]:
    enroll, dropout, diversity, amend = [], [], [], []
    bootstrap_n = 0
    expected_draws_n = None
    for seed in seeds:
        evaluation = _Evaluation(cohort_factory(n, seed, protocol.setting), scorer)
        before, after = evaluation(protocol), evaluation(candidate.protocol, candidate.edits)
        enroll.append(_relative(after.score.enrollment_speed, before.score.enrollment_speed))
        dropout.append(after.score.dropout_risk - before.score.dropout_risk)
        diversity.append(after.diversity - before.diversity)
        before_draws, after_draws = before.score.bootstrap_risks, after.score.bootstrap_risks
        if bool(before_draws) != bool(after_draws) or len(before_draws) != len(after_draws):
            raise ValueError("before/after bootstrap models must have matching indices")
        if expected_draws_n is not None and len(before_draws) != expected_draws_n:
            raise ValueError("bootstrap model indices must remain stable across cohort seeds")
        expected_draws_n = len(before_draws)
        if before_draws:
            bootstrap_n = len(before_draws)
            amend.extend(a[1] - b[1] for b, a in zip(before_draws, after_draws, strict=True))
        else:
            amend.append(after.score.p_amend - before.score.p_amend)
    return FixRanges(
        delta_enroll=_interval(enroll, _relative(candidate.score.enrollment_speed, base.score.enrollment_speed)),
        delta_dropout=_interval(dropout, candidate.score.dropout_risk - base.score.dropout_risk),
        delta_diversity=_interval(diversity, candidate.diversity - base.diversity),
        delta_p_amend=_interval(amend, candidate.score.p_amend - base.score.p_amend)), bootstrap_n


def optimize_with_report(protocol: ParsedProtocol, *, max_edits: int = 3, n_trials: int = 300,
                         seed: int = 42, scorer: Scorer | None = None,
                         edit_space: Sequence[Edit] | None = None,
                         observed_rows: Sequence[Mapping[str, Any]] | None = None,
                         cohort_size: int = 1000, cohort_factory: Callable = _cohort,
                         precedent_fn: Callable | None = None) -> OptimizationResult:
    """NSGA-II with an enumeration fallback, exposing provenance and the full Pareto front.

    Only ``fixes`` are returned by the frozen ``optimize`` API. This richer API supports
    offline injected evidence, deterministic benchmarks and explicit scoring provenance.
    """
    started = time.perf_counter()
    if not 1 <= max_edits <= 3 or n_trials < 1 or cohort_size < 1:
        raise ValueError("max_edits must be 1..3, n_trials and cohort_size positive")
    scorer = scorer or ModelScorer()
    space = list(edit_space) if edit_space is not None else build_edit_space(protocol, observed_rows=observed_rows)
    seeds = tuple(seed + i for i in range(5))
    evaluator = _Evaluation(cohort_factory(cohort_size, seed, protocol.setting), scorer)
    baseline = evaluator(protocol)
    cache: dict[str, Candidate] = {}
    notes = []
    if baseline.score.enrollment_speed == 0:
        notes.append("Baseline enrollment speed is zero; relative enrollment deltas are undefined and reported as 0.")

    def evaluate(edits: Sequence[Edit]):
        key = _key(edits)
        if key not in cache:
            try:
                edited = apply_protocol_edits(protocol, edits)
            except ValueError as exc:
                raise _InadmissibleEdit(str(exc)) from exc
            cache[key] = evaluator(edited, edits)
        return cache[key].objectives

    if space:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        def objective(trial):
            chosen, targets = [], set()
            for j in range(max_edits):
                index = trial.suggest_int(f"edit_{j}", 0, len(space))
                if index == len(space):
                    continue
                edit = space[index]
                target = ("rule" if edit.kind in {"relax_rule", "drop_rule"} else "visit", edit.target_id)
                if target not in targets:
                    targets.add(target)
                    chosen.append(edit)
            if not chosen:
                return (-1.0, -1.0, -1.0)
            try:
                return evaluate(chosen)
            except _InadmissibleEdit:
                # Overlapping visit edits or unsafe externally supplied edits are inadmissible.
                return (-1.0, -1.0, -1.0)
        try:
            study = optuna.create_study(directions=["maximize"] * 3,
                sampler=optuna.samplers.NSGAIISampler(seed=seed, population_size=min(50, max(2, n_trials))))
            for i in range(min(len(space), n_trials)):
                study.enqueue_trial({f"edit_{j}": i if j == 0 else len(space) for j in range(max_edits)})
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        except (RuntimeError, FloatingPointError) as exc:
            notes.append(f"NSGA-II unavailable ({type(exc).__name__}); enumerated single and pairwise edits.")
            from itertools import combinations
            for size in range(1, min(2, max_edits) + 1):
                for combination in combinations(space, size):
                    try:
                        evaluate(combination)
                    except _InadmissibleEdit:
                        continue
    front = pareto_front(cache.values())
    picks = pick_representatives(front)
    if len({_key(c.edits) for _, c in picks}) < len(picks):
        notes.append("Some representatives share the same edit combination because the Pareto front is small.")
    if precedent_fn is None:
        from clinova.optimize.precedents import find_precedents
        precedent_fn = find_precedents
    fixes, bootstrap_n, range_cache, precedent_cache = [], 0, {}, {}
    protocol_key = hashlib.sha256(protocol.model_dump_json().encode()).hexdigest()
    for label, candidate in picks:
        key = _key(candidate.edits)
        if key not in range_cache:
            range_cache[key] = _ranges(protocol, candidate, baseline, scorer, seeds, cohort_size, cohort_factory)
        ranges, count = range_cache[key]
        bootstrap_n = max(bootstrap_n, count)
        if key not in precedent_cache:
            precedent_cache[key] = precedent_fn(protocol, candidate.edits)
        delta_amend = candidate.score.p_amend - baseline.score.p_amend
        summary = f"Consider {len(candidate.edits)} edit(s) for {label.replace('_', ' ')}; amendment dollars are expected value."
        if "heuristic" in scorer.source:
            summary += " Uncalibrated heuristic estimate."
        if not count:
            summary += " Ranges cover five cohort seeds; fitted bootstrap models unavailable."
        if not precedent_cache[key]:
            summary += " No verified completed threshold precedent in the stored evidence."
        fix = Fix(fix_id="opt-" + hashlib.sha256(f"{protocol_key}:{seed}:{label}:{key}".encode()).hexdigest()[:24],
                  trial_id=protocol.trial_id, label=label, summary=summary, edits=list(candidate.edits),
                  delta_enroll=_relative(candidate.score.enrollment_speed, baseline.score.enrollment_speed),
                  delta_dropout=candidate.score.dropout_risk - baseline.score.dropout_risk,
                  delta_diversity=candidate.diversity - baseline.diversity, delta_p_amend=delta_amend,
                  ranges=ranges, dollar_value=max(0, -delta_amend) * amendment_cost(protocol.phase),
                  precedents=precedent_cache[key])
        fixes.append(fix)
    if not bootstrap_n:
        notes.append("Ranges include five cohort seeds only; fitted bootstrap model draws are unavailable.")
    return OptimizationResult(fixes, front, len(cache), time.perf_counter() - started,
                              scorer.source, seeds, bootstrap_n, notes)


def optimize(protocol: ParsedProtocol, *, max_edits: int = 3, n_trials: int = 300, seed: int = 42) -> list[Fix]:
    """Return fastest, lowest-dropout and balanced Pareto picks; no Gemini calls."""
    if protocol.trial_id.startswith("FIXTURE-"):
        # The frozen API has no provenance/injected-evidence arguments. Let the
        # existing API fixture handler label this synthetic case accurately and
        # avoid querying production data from fixture-only API tests. Explicit
        # offline optimizer runs use optimize_with_report (including CLI --fixture).
        raise NotImplementedError("Synthetic fixtures require an explicitly offline optimizer report")
    return optimize_with_report(protocol, max_edits=max_edits, n_trials=n_trials, seed=seed).fixes
