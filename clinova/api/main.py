"""Clinova API (owner: T15, then T25 and T32).

Every route calls the real module and falls back to ``tests/fixtures`` when that module is still a stub
(``NotImplementedError``) or the database is unreachable. Fallback responses carry ``source: "fixture"``.
Clinova advises protocol authors; nothing here approves a protocol.
"""

from __future__ import annotations

import importlib
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from clinova.api import fallbacks, live
from clinova.api.audit import MEMORY
from clinova.api.runs import RUNS
from clinova.api.schemas import (
    AuditResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    OptimizeRequest,
    OptimizeResponse,
    ParseRequest,
    ParseResponse,
    PulseResponse,
    ReplayResponse,
    RunCreated,
    RunRequest,
    ValidationResponse,
)
from clinova.common.config import MAIN_ROOT, REPO_ROOT, get_settings
from clinova.contracts import (
    CONTRACTS_VERSION,
    BacktestResult,
    Feedback,
    ReplayResult,
    RunStatus,
    Source,
    ValidationMetrics,
)

log = logging.getLogger("clinova.api")

def _warm_imports() -> None:
    """Import the heavy engine modules (pandas, lightgbm, shap) once, off the request path."""
    for mod in (
        "clinova.cohort.generate",
        "clinova.simulate.screen",
        "clinova.simulate.runner",
        "clinova.model.predict",
        "clinova.optimize.search",
        "clinova.loop.ledger",
    ):
        try:
            importlib.import_module(mod)
        except Exception as exc:
            log.info("warm-up import of %s failed: %s", mod, type(exc).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_imports, daemon=True, name="warm-imports").start()
    yield


app = FastAPI(
    title="Clinova API",
    version="0.1.0",
    description="Predicts where a draft NSCLC trial protocol may stall enrollment or lose patients. Advisory only.",
    lifespan=lifespan,
)

_FALLBACK_ERRORS = (NotImplementedError, live.LiveUnavailable)
_NCT_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


def _check_id(nct_id: str) -> str:
    if not _NCT_RE.match(nct_id):
        raise HTTPException(422, "Trial id may only contain letters, digits, '-' and '_'.")
    return nct_id


def _doc_file(rel: str):
    """First existing file among this tree's docs and the main clone's docs."""
    for root in (REPO_ROOT, MAIN_ROOT):
        p = root / rel
        if p.is_file():
            return p
    return None


# --- health ---------------------------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(contracts_version=CONTRACTS_VERSION, mode=get_settings().CLINOVA_MODE, db=live.db_available())


# --- parse ----------------------------------------------------------------------------------------------


@app.post("/protocols/parse", response_model=ParseResponse)
def parse_protocol(req: ParseRequest) -> ParseResponse:
    if req.nct_id:
        nct = _check_id(req.nct_id)
        try:
            trial = live.fetch_trial(nct)
            return ParseResponse(
                protocol=live.protocol_from_trial(trial),
                source=Source.live,
                registered_on=trial.first_version_date,
                note=f"Protocol as originally registered on {trial.first_version_date}.",
            )
        except LookupError:
            raise HTTPException(404, f"{nct} is not in the Clinova trial database.") from None
        except _FALLBACK_ERRORS:
            pass
        if nct.upper() != fallbacks.FIXTURE_NCT_ID:
            raise HTTPException(
                404,
                f"No trial data for {nct} while the engine is offline. Try {fallbacks.FIXTURE_NCT_ID} for the fixture trial.",
            )
        trial = fallbacks.trial_record()
        return ParseResponse(
            protocol=fallbacks.parsed_protocol(),
            source=Source.fixture,
            registered_on=trial.first_version_date,
            note=f"Fixture trial, as originally registered on {trial.first_version_date}.",
        )
    if not (req.eligibility_text and req.eligibility_text.strip()):
        raise HTTPException(422, "Give eligibility_text or an nct_id.")
    try:
        protocol = live.parse_text(
            req.eligibility_text, req.schedule_text, trial_id=f"draft-{uuid.uuid4().hex[:8]}", phase=req.phase
        )
        return ParseResponse(protocol=protocol, source=Source.live)
    except _FALLBACK_ERRORS:
        return ParseResponse(
            protocol=fallbacks.parsed_protocol(),
            source=Source.fixture,
            note="The parser is not available yet, so this is the fixture protocol, not your pasted text.",
        )


# --- runs -----------------------------------------------------------------------------------------------


@app.post("/runs", response_model=RunCreated, status_code=202)
def create_run(req: RunRequest) -> RunCreated:
    if req.nct_id:
        _check_id(req.nct_id)
    run_id = RUNS.submit(req)
    return RunCreated(run_id=run_id, status_url=f"/runs/{run_id}")


@app.get("/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str) -> RunStatus:
    st = RUNS.get(run_id)
    if st is None:
        raise HTTPException(404, f"Unknown run {run_id}.")
    return st


# --- backtest ---------------------------------------------------------------------------------------------


@app.get("/trials/{nct_id}/backtest", response_model=BacktestResult)
def backtest(nct_id: str) -> BacktestResult:
    nct = _check_id(nct_id)
    try:
        return live.backtest(nct)
    except _FALLBACK_ERRORS:
        pass
    result = fallbacks.backtest(nct)
    if result is None:
        raise HTTPException(
            404, f"No backtest for {nct} while the engine is offline. Try {fallbacks.FIXTURE_NCT_ID} for the fixture trial."
        )
    return result.model_copy(update={"source": Source.fixture})


# --- optimize ---------------------------------------------------------------------------------------------


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    try:
        return OptimizeResponse(fixes=live.optimize(req.protocol, req.max_edits), source=Source.live)
    except _FALLBACK_ERRORS:
        return OptimizeResponse(fixes=fallbacks.fixes(), source=Source.fixture)


# --- feedback and audit -----------------------------------------------------------------------------------


@app.post("/feedback", response_model=FeedbackResponse)
def post_feedback(body: FeedbackIn) -> FeedbackResponse:
    fb = Feedback(feedback_id=uuid.uuid4().hex, created_at=datetime.now(UTC), **body.model_dump())
    try:
        live._need_db()
        stored = live.record_feedback(fb)
        return FeedbackResponse(feedback=stored, source=Source.live)
    except _FALLBACK_ERRORS:
        MEMORY.add_feedback(fb)
        return FeedbackResponse(feedback=fb, source=Source.fixture)


@app.get("/audit", response_model=AuditResponse)
def audit() -> AuditResponse:
    mem_fb, mem_pred = MEMORY.snapshot()
    try:
        fbs, preds = live.read_feedback(), live.read_predictions()
        source = Source.live
    except _FALLBACK_ERRORS:
        fbs, preds, source = [], [], Source.fixture
    except Exception as exc:  # a half-built table must not take the tab down
        log.warning("audit read failed: %s", type(exc).__name__)
        fbs, preds, source = [], [], Source.fixture
    if mem_fb or mem_pred:
        source = Source.fixture  # some rows only live in memory
    fbs = sorted([*fbs, *mem_fb], key=lambda f: f.created_at, reverse=True)
    preds = sorted([*preds, *mem_pred], key=lambda p: p.predicted_at, reverse=True)
    return AuditResponse(feedback=fbs, predictions=preds, source=source)


# --- validation, replay, pulse ----------------------------------------------------------------------------


@app.get("/validation", response_model=ValidationResponse)
def validation() -> ValidationResponse:
    path = _doc_file("docs/metrics.json")
    if path is not None:
        try:
            return ValidationResponse(metrics=ValidationMetrics.model_validate_json(path.read_bytes()), source=Source.live)
        except ValueError as exc:
            log.warning("docs/metrics.json does not match the contract: %s", exc)
    return ValidationResponse(metrics=fallbacks.validation(), source=Source.fixture)


@app.get("/replay", response_model=ReplayResponse)
def replay() -> ReplayResponse:
    path = _doc_file("docs/replay.json")
    if path is not None:
        try:
            return ReplayResponse(replay=ReplayResult.model_validate_json(path.read_bytes()), source=Source.live)
        except ValueError as exc:
            log.warning("docs/replay.json does not match the contract: %s", exc)
    return ReplayResponse(replay=fallbacks.replay(), source=Source.fixture)


@app.get("/pulse/{nct_id}", response_model=PulseResponse)
def pulse(nct_id: str) -> PulseResponse:
    """Community Pulse report written by T16 to docs/pulse/<id>.md. Absent means 'not run'."""
    nct = _check_id(nct_id)
    path = _doc_file(f"docs/pulse/{nct}.md")
    if path is None:
        return PulseResponse(
            nct_id=nct,
            status="not_run",
            source=Source.live,
            note="The community pulse has not been run for this trial.",
        )
    return PulseResponse(nct_id=nct, status="available", report_md=path.read_text(encoding="utf-8"), source=Source.cache)
