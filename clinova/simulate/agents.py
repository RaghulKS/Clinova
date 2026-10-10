"""Persona agents (owner: T14, then T24). See ARCHITECTURE.md 5.5 and docs/CONTRACTS.md."""

from __future__ import annotations

from collections.abc import Sequence

from clinova.contracts import AgentDecision, ParsedProtocol, Persona


def trial_offer(protocol: ParsedProtocol) -> str:
    """Plain-language trial offer: purpose, randomization, each visit's day, procedures, hours and
    travel, total duration. Never includes base rates or outcomes."""
    raise NotImplementedError("T14 implements clinova.simulate.agents.trial_offer")


async def arun_agents(personas: Sequence[Persona], protocol: ParsedProtocol, seed: int) -> list[AgentDecision]:
    """Ask each persona (25 per Gemini call, batches concurrent, via
    ``clinova.common.llm.agenerate_json``) whether they would enroll and at which visit they would
    drop out. Returns one validated AgentDecision per persona, in input order; personas missing
    from a response are retried."""
    raise NotImplementedError("T14 implements clinova.simulate.agents.arun_agents")


def run_agents(personas: Sequence[Persona], protocol: ParsedProtocol, seed: int) -> list[AgentDecision]:
    """Synchronous wrapper of ``arun_agents``."""
    raise NotImplementedError("T14 implements clinova.simulate.agents.run_agents")
