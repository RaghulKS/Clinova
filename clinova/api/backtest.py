"""Live backtest: run Clinova on a registered trial's FIRST version, then reveal what happened.

Leakage rule: the run sees only the first registered eligibility text and design fields. The version
history, the current eligibility text and the outcome are attached afterwards, for the reveal only.
"""

from __future__ import annotations

import logging
import threading

from clinova.api import live
from clinova.api.runs import RUNS
from clinova.api.schemas import RunRequest
from clinova.contracts import BacktestResult, RuleChange, TrialRecord

log = logging.getLogger("clinova.api")

_CACHE: dict[str, BacktestResult] = {}
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def clear_cache() -> None:
    with _GUARD:
        _CACHE.clear()


def _rule_changes(trial: TrialRecord, first) -> list[RuleChange]:
    """Eligibility rule changes from the first to the last registered version. Empty when the text did not
    change or cannot be parsed right now (a missing Gemini budget must not break the reveal)."""
    current = (trial.eligibility_text_current or "").strip()
    if not current or current == (trial.eligibility_text_first or "").strip():
        return []
    try:
        parsed_now = live.parse_text(current, None, trial_id=f"{trial.nct_id}-current", phase=trial.phase)
        return live.diff_rules(first, parsed_now)
    except Exception as exc:
        log.warning("rule diff for %s unavailable: %s", trial.nct_id, type(exc).__name__)
        return []


def run_backtest(nct_id: str, *, refresh: bool = False) -> BacktestResult:
    """Build (or return the cached) backtest for ``nct_id``.

    Raises ``LookupError`` (trial not loaded), ``LiveUnavailable`` (no database) or
    ``NotImplementedError`` (parser not built yet); the route turns those into the fixture fallback or a 404.
    """
    nct = nct_id.upper()
    with _GUARD:
        lock = _LOCKS.setdefault(nct, threading.Lock())
    with lock:  # one build per trial at a time; a second caller waits and gets the cached result
        if not refresh and nct in _CACHE:
            return _CACHE[nct]
        trial = live.fetch_trial(nct)
        protocol = live.protocol_from_trial(trial)  # first registered version only
        run_id = RUNS.submit(RunRequest(protocol=protocol), background=False, context="backtest")
        status = RUNS.get(run_id)
        if status is None or status.stage != "done" or status.result is None:
            raise RuntimeError(f"Backtest run failed: {status.message if status else 'unknown'}")
        try:
            events = live.read_version_events(nct)
        except live.LiveUnavailable:
            events = []
        result = BacktestResult(
            trial=trial,
            run=status.result,
            events=events,
            changes=_rule_changes(trial, protocol),
            source=status.source,
        )
        _CACHE[nct] = result
        return result
