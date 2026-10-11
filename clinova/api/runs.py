"""Run manager: POST /runs starts a background thread that walks the pipeline stage by stage.

Each stage calls the real module. A stage whose module is still a stub (``NotImplementedError``) or whose
database is unavailable uses the fixture for that stage; the run's ``source`` is then ``fixture`` and the
message names the stages that used fixtures. Any other exception fails the run with its message.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from clinova.api import fallbacks, live
from clinova.api.schemas import RunRequest
from clinova.contracts import (
    CriteriaRule,
    Edit,
    ParsedProtocol,
    RunResult,
    RunStage,
    RunStatus,
    Source,
    Visit,
)

log = logging.getLogger("clinova.api")

PIPELINE: list[RunStage] = ["parse", "cohort", "screen", "agents", "score", "flags", "optimize"]
_FALLBACK_ERRORS = live.FALLBACK_ERRORS


def stage_delay() -> float:
    """Seconds to pause on a fixture stage so progress is visible in the app (0 in tests)."""
    try:
        return float(os.environ.get("CLINOVA_FIXTURE_DELAY", "0.4"))
    except ValueError:
        return 0.4


def _now() -> datetime:
    return datetime.now(UTC)


# --- edits (local shim until simulate.screen.apply_edits and optimize land) --------------------------


def apply_edits_local(protocol: ParsedProtocol, edits: list[Edit]) -> ParsedProtocol:
    """Apply fix edits to a protocol without mutating it. Rule edits go through
    ``simulate.screen.apply_edits`` when it exists; otherwise a small local version is used. Locked rules
    raise ``ValueError``. Visit edits (cut, merge, remote) are always applied here."""
    rule_edits = [e for e in edits if e.kind in ("relax_rule", "drop_rule")]
    visit_edits = [e for e in edits if e.kind not in ("relax_rule", "drop_rule")]
    rules = [r.model_copy(deep=True) for r in protocol.rules]
    if rule_edits:
        try:
            rules = live.apply_edits(rules, rule_edits)
        except NotImplementedError:
            rules = _apply_rule_edits(rules, rule_edits)
    visits = [v.model_copy(deep=True) for v in protocol.visits]
    for e in visit_edits:
        visits = _apply_visit_edit(visits, e)
    return protocol.model_copy(update={"rules": rules, "visits": visits}, deep=True)


def _apply_rule_edits(rules: list[CriteriaRule], edits: list[Edit]) -> list[CriteriaRule]:
    by_id = {r.rule_id: r for r in rules}
    for e in edits:
        rule = by_id.get(e.target_id)
        if rule is None:
            raise ValueError(f"unknown rule {e.target_id}")
        if rule.locked:
            raise ValueError(f"rule {rule.rule_id} is locked and cannot be edited")
        if e.kind == "drop_rule":
            rules = [r for r in rules if r.rule_id != rule.rule_id]
        else:
            update: dict[str, Any] = {"value": e.new_value}
            if e.new_operator is not None:
                update["operator"] = e.new_operator
            rules = [r.model_copy(update=update) if r.rule_id == rule.rule_id else r for r in rules]
    return rules


def _apply_visit_edit(visits: list[Visit], e: Edit) -> list[Visit]:
    try:
        no = int(e.target_id)
    except ValueError as exc:
        raise ValueError(f"visit edit needs a visit number, got {e.target_id!r}") from exc
    idx = next((i for i, v in enumerate(visits) if v.visit_no == no), None)
    if idx is None:
        raise ValueError(f"unknown visit {no}")
    if e.kind == "cut_visit":
        out = [v for v in visits if v.visit_no != no]
    elif e.kind == "make_remote":
        out = [v.model_copy(update={"requires_travel": False, "remote_ok": True}) if v.visit_no == no else v for v in visits]
    elif e.kind == "merge_visits":
        if idx + 1 >= len(visits):
            raise ValueError("no next visit to merge with")
        a, b = visits[idx], visits[idx + 1]
        merged = a.model_copy(
            update={
                "procedures": list(dict.fromkeys([*a.procedures, *b.procedures])),
                "est_hours": a.est_hours + b.est_hours,
                "invasive": a.invasive or b.invasive,
            }
        )
        out = [*visits[:idx], merged, *visits[idx + 2 :]]
    else:
        raise ValueError(f"unsupported visit edit {e.kind}")
    return [v.model_copy(update={"visit_no": i}) for i, v in enumerate(out, start=1)]


# --- the manager -------------------------------------------------------------------------------------


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunStatus] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str) -> RunStatus | None:
        with self._lock:
            st = self._runs.get(run_id)
            return st.model_copy(deep=True) if st else None

    def _set(self, run_id: str, **changes: Any) -> None:
        with self._lock:
            st = self._runs[run_id]
            self._runs[run_id] = st.model_copy(update={**changes, "updated_at": _now()})

    def submit(self, req: RunRequest, *, background: bool = True, context: str = "live") -> str:
        run_id = uuid.uuid4().hex
        trial_id = (req.protocol.trial_id if req.protocol else req.nct_id) or fallbacks.FIXTURE_TRIAL_ID
        st = RunStatus(
            run_id=run_id,
            trial_id=trial_id,
            stage="queued",
            stages_done=[],
            progress=0.0,
            message="Queued",
            started_at=_now(),
            updated_at=_now(),
            source=Source.live,
        )
        with self._lock:
            self._runs[run_id] = st
        if background:
            threading.Thread(target=self._execute, args=(run_id, req, context), daemon=True, name=f"run-{run_id[:6]}").start()
        else:
            self._execute(run_id, req, context)
        return run_id

    # one stage: real call, fixture on fallback errors
    def _stage(self, run_id: str, name: RunStage, state: dict, live_fn: Callable[[], Any], fixture_fn: Callable[[], Any]) -> Any:
        done: list[RunStage] = list(state["done"])
        self._set(run_id, stage=name, progress=len(done) / len(PIPELINE), message=f"Running {name}")
        try:
            value = live_fn()
        except _FALLBACK_ERRORS as exc:
            log.info("stage %s: fixture fallback (%s)", name, exc)
            state["fixture"].append(name)
            time.sleep(stage_delay())
            value = fixture_fn()
        state["done"].append(name)
        self._set(run_id, stages_done=list(state["done"]), progress=len(state["done"]) / len(PIPELINE))
        return value

    def _execute(self, run_id: str, req: RunRequest, context: str = "live") -> None:
        state: dict[str, Any] = {"done": [], "fixture": []}
        try:
            result = self._pipeline(run_id, req, state, context)
        except Exception as exc:
            log.exception("run %s failed", run_id)
            self._set(run_id, stage="failed", message=f"{type(exc).__name__}: {exc}")
            return
        fixture = state["fixture"]
        source = Source.fixture if fixture else Source.live
        msg = "Done" if not fixture else f"Done. Fixture data used for: {', '.join(fixture)}."
        self._set(run_id, stage="done", stages_done=[*PIPELINE], progress=1.0, message=msg, source=source, result=result)

    def _pipeline(self, run_id: str, req: RunRequest, state: dict, context: str = "live") -> RunResult:
        # parse: a reviewed protocol skips parsing; an NCT id loads the first registered version
        def live_parse() -> ParsedProtocol:
            if req.protocol is not None:
                return req.protocol
            if req.nct_id:
                return live.protocol_from_trial(live.fetch_trial(req.nct_id))
            if req.eligibility_text:
                draft_id = f"draft-{uuid.uuid4().hex[:8]}"
                parsed = live.parse_text(req.eligibility_text, req.schedule_text, trial_id=draft_id, phase=req.phase)
                live.save_draft(draft_id, req.eligibility_text, req.schedule_text, parsed)
                return parsed
            raise live.LiveUnavailable("nothing to parse")

        def fixture_parse() -> ParsedProtocol:
            if req.nct_id and req.nct_id.upper() != fallbacks.FIXTURE_NCT_ID:
                raise ValueError(f"No fixture for {req.nct_id}; only {fallbacks.FIXTURE_NCT_ID} works while the engine is offline.")
            return fallbacks.parsed_protocol()

        protocol: ParsedProtocol = self._stage(run_id, "parse", state, live_parse, fixture_parse)
        if req.edits:
            protocol = apply_edits_local(protocol, req.edits)
        seeds = req.seeds or [1, 2]

        cohort = self._stage(
            run_id, "cohort", state, lambda: live.make_cohort(req.n_personas, seeds[0], protocol.setting.value), lambda: None
        )

        def live_screen():
            if cohort is None:
                raise live.LiveUnavailable("no cohort")
            _mask, funnel = live.screen(protocol.rules, cohort, protocol.trial_id)
            return funnel

        funnel = self._stage(run_id, "screen", state, live_screen, fallbacks.funnel)
        sims = self._stage(
            run_id, "agents", state, lambda: live.simulate(protocol, req.n_personas, seeds), lambda: fallbacks.sims(seeds)
        )
        risks = self._stage(run_id, "score", state, lambda: live.score(protocol, sims), fallbacks.risks)
        for r in risks:
            self._log_prediction(protocol.trial_id, r, fixture="score" in state["fixture"], context=context)
        # Fixture flags and fixes name the fixture protocol's rules (I12 ...). On any other protocol they would point at
        # the wrong criteria and accepting one would edit the wrong rule, so there the fallback is "none yet".
        is_fixture_protocol = protocol.trial_id == fallbacks.FIXTURE_TRIAL_ID
        flags = self._stage(
            run_id, "flags", state, lambda: live.flag_criteria(protocol), fallbacks.flags if is_fixture_protocol else list
        )
        fixes = self._stage(
            run_id, "optimize", state, lambda: live.optimize(protocol), fallbacks.fixes if is_fixture_protocol else list
        )
        return RunResult(protocol=protocol, funnel=funnel, sim=sims, risks=risks, flags=flags, fixes=fixes)

    def _log_prediction(self, trial_id: str, risk, *, fixture: bool, context: str = "live") -> None:
        """Log a score in the ledger (context live or backtest; fixture scores are tagged fixture).
        While the ledger or database is unavailable the score goes to the in-memory audit instead."""
        from clinova.api.audit import MEMORY

        ctx = "fixture" if fixture else context
        try:
            live._need_db()
            live.record_prediction(trial_id, risk, ctx)
        except Exception as exc:  # the ledger must never break a run
            log.info("prediction not stored in the ledger (%s)", type(exc).__name__)
            MEMORY.add_prediction(trial_id, risk, ctx)


RUNS = RunManager()
