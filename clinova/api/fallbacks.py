"""Fixture fallbacks: what a route returns while the real module is still a stub.

Everything here comes from ``tests/fixtures`` (synthetic, illustrative). Routes that use these tag the
response ``source == "fixture"`` so the app shows a "fixture data" tag. Nothing here is a result.
"""

from __future__ import annotations

from datetime import UTC, datetime

from clinova.common.fixtures import load_fixture
from clinova.contracts import (
    BacktestResult,
    CriterionFlag,
    Fix,
    Funnel,
    ParsedProtocol,
    ReplayResult,
    RiskScore,
    RuleChange,
    RunResult,
    SimRunSummary,
    TrialRecord,
    ValidationMetrics,
    VersionEvent,
)

FIXTURE_TRIAL_ID = "FIXTURE-2L-001"
FIXTURE_NCT_ID = "NCT99999901"


def parsed_protocol() -> ParsedProtocol:
    return load_fixture("parsed_protocol.json", ParsedProtocol)


def funnel() -> Funnel:
    return load_fixture("funnel.json", Funnel)


def sim_summary() -> SimRunSummary:
    return load_fixture("sim_run_summary.json", SimRunSummary)


def sims(seeds: list[int]) -> list[SimRunSummary]:
    """One fixture summary per requested seed (same numbers; only seed and id differ)."""
    base = sim_summary()
    out = []
    for i, seed in enumerate(seeds or [1]):
        out.append(base.model_copy(update={"seed": seed, "run_id": f"{base.run_id}-s{seed}-{i}"}))
    return out


def risks() -> list[RiskScore]:
    return load_fixture("risk_scores.json", list[RiskScore])


def flags() -> list[CriterionFlag]:
    return load_fixture("criterion_flags.json", list[CriterionFlag])


def fixes() -> list[Fix]:
    return load_fixture("fixes.json", list[Fix])


def trial_record() -> TrialRecord:
    return load_fixture("trial_record.json", TrialRecord)


def version_events() -> list[VersionEvent]:
    return load_fixture("version_events.json", list[VersionEvent])


def validation() -> ValidationMetrics:
    return load_fixture("metrics.json", ValidationMetrics)


def replay() -> ReplayResult:
    return load_fixture("replay.json", ReplayResult)


def run_result(protocol: ParsedProtocol | None = None, seeds: list[int] | None = None) -> RunResult:
    """A complete fixture RunResult. ``protocol`` (what the author reviewed) replaces the fixture one so
    the result still says which protocol was run."""
    return RunResult(
        protocol=protocol or parsed_protocol(),
        funnel=funnel(),
        sim=sims(seeds or [1, 2]),
        risks=risks(),
        flags=flags(),
        fixes=fixes(),
    )


def rule_changes() -> list[RuleChange]:
    """First -> last version eligibility changes of the fixture trial (matches version_events.json)."""
    return [
        RuleChange(
            field="crcl_ml_min",
            change="relaxed",
            old_rule_id="I12",
            new_rule_id="I12",
            old="crcl_ml_min ge 60",
            new="crcl_ml_min ge 45",
        ),
        RuleChange(
            field="ecog",
            change="relaxed",
            old_rule_id="I7",
            new_rule_id="I7",
            old="ecog le 1",
            new="ecog le 2",
        ),
    ]


def backtest(nct_id: str) -> BacktestResult | None:
    """The fixture backtest, only for the fixture trial id."""
    if nct_id.upper() != FIXTURE_NCT_ID:
        return None
    return BacktestResult(
        trial=trial_record(),
        run=run_result(),
        events=version_events(),
        changes=rule_changes(),
    )


def now() -> datetime:
    return datetime.now(UTC)
