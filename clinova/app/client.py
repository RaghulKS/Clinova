"""The app's only door to the engine: every call goes to the Clinova API over HTTP.

Base URL from ``CLINOVA_API_URL`` (default ``http://127.0.0.1:8000``). Responses are validated against the
contract models, so a drifting API fails loudly here and not inside a chart.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

import httpx

from clinova.api.schemas import (
    AcceptResponse,
    AuditResponse,
    FeedbackIn,
    FeedbackResponse,
    OptimizeResponse,
    ParseResponse,
    PulseResponse,
    ReplayResponse,
    RunCreated,
    ValidationResponse,
)
from clinova.contracts import BacktestResult, ParsedProtocol, RunStatus

DEFAULT_URL = "http://127.0.0.1:8000"


class ApiError(RuntimeError):
    """The API was unreachable or answered with an error; ``str(exc)`` is safe to show the author."""


def _detail(r: httpx.Response) -> str:
    """One readable sentence from an error response: FastAPI's ``detail`` string, or its list of
    validation errors, never a raw body or a stack trace."""
    try:
        detail = r.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # request validation errors
        parts = []
        for d in detail[:3]:
            if isinstance(d, dict):
                where = ".".join(str(x) for x in d.get("loc", [])[1:])
                parts.append(f"{where}: {d.get('msg', '')}" if where else str(d.get("msg", "")))
        detail = "The request was not accepted (" + "; ".join(parts) + ")." if parts else None
    if not detail:
        detail = f"The Clinova API answered with an error (HTTP {r.status_code})."
    return str(detail)


class ApiClient:
    def __init__(self, base_url: str | None = None, *, timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.environ.get("CLINOVA_API_URL") or DEFAULT_URL).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    # -- plumbing
    def _call(self, method: str, path: str, **kw):
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach the Clinova API at {self.base_url} ({type(exc).__name__}).") from exc
        if r.status_code >= 400:
            raise ApiError(_detail(r))
        return r.json()

    # -- routes
    def health(self) -> dict:
        return self._call("GET", "/health")

    def parse(self, *, eligibility_text: str | None = None, schedule_text: str | None = None, nct_id: str | None = None) -> ParseResponse:
        body = {"eligibility_text": eligibility_text, "schedule_text": schedule_text, "nct_id": nct_id}
        return ParseResponse.model_validate(self._call("POST", "/protocols/parse", json=body))

    def start_run(self, protocol: ParsedProtocol | None = None, *, nct_id: str | None = None, edits: list | None = None) -> RunCreated:
        body: dict = {"nct_id": nct_id, "edits": [e.model_dump(mode="json") for e in (edits or [])]}
        if protocol is not None:
            body["protocol"] = protocol.model_dump(mode="json")
        return RunCreated.model_validate(self._call("POST", "/runs", json=body))

    def run_status(self, run_id: str) -> RunStatus:
        return RunStatus.model_validate(self._call("GET", f"/runs/{run_id}"))

    def wait_for_run(
        self,
        run_id: str,
        *,
        on_update: Callable[[RunStatus], None] | None = None,
        poll_s: float = 0.4,
        timeout_s: float = 900.0,
    ) -> RunStatus:
        end = time.monotonic() + timeout_s
        while True:
            st = self.run_status(run_id)
            if on_update:
                on_update(st)
            if st.stage in ("done", "failed"):
                return st
            if time.monotonic() > end:
                raise ApiError("The run is taking too long; check the API log.")
            time.sleep(poll_s)

    def backtest(self, nct_id: str) -> BacktestResult:
        # a first live backtest runs the whole pipeline, so allow far longer than a normal call
        return BacktestResult.model_validate(self._call("GET", f"/trials/{nct_id}/backtest", timeout=900.0))

    def optimize(self, protocol: ParsedProtocol) -> OptimizeResponse:
        return OptimizeResponse.model_validate(self._call("POST", "/optimize", json={"protocol": protocol.model_dump(mode="json")}))

    def feedback(self, fb: FeedbackIn) -> FeedbackResponse:
        return FeedbackResponse.model_validate(self._call("POST", "/feedback", json=fb.model_dump(mode="json")))

    def accept_fix(self, run_id: str, fix_id: str) -> AcceptResponse:
        body = {"run_id": run_id, "fix_id": fix_id}
        return AcceptResponse.model_validate(self._call("POST", "/fixes/accept", json=body))

    def validation(self) -> ValidationResponse:
        return ValidationResponse.model_validate(self._call("GET", "/validation"))

    def replay(self) -> ReplayResponse:
        return ReplayResponse.model_validate(self._call("GET", "/replay"))

    def pulse(self, nct_id: str) -> PulseResponse:
        return PulseResponse.model_validate(self._call("GET", f"/pulse/{nct_id}"))

    def audit(self) -> AuditResponse:
        return AuditResponse.model_validate(self._call("GET", "/audit"))
