"""Simulation runner (owner: T14, then T24). Tables ``sim_runs`` and ``persona_outcomes``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from statistics import median_low
from uuid import uuid4

import numpy as np
from psycopg.types.json import Jsonb

from clinova.cohort.generate import cohort_frame, generate_cohort
from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.contracts import AgentDecision, Funnel, ParsedProtocol, Persona, ReasonShare, SimRunSummary
from clinova.simulate.agents import PROMPT_VERSION, run_agents
from clinova.simulate.screen import screen


def summarize(protocol: ParsedProtocol, seed: int, funnel: Funnel,
              decisions: Sequence[AgentDecision]) -> SimRunSummary:
    enrolled = [d for d in decisions if d.enroll]
    dropouts = [d.dropout_visit for d in enrolled if d.dropout_visit is not None]
    adverse = [d for d in decisions if not d.enroll or d.dropout_visit is not None]
    counts = Counter(tag for decision in adverse for tag in set(decision.burden_tags))
    return SimRunSummary(
        run_id=uuid4().hex, trial_id=protocol.trial_id, version_no=protocol.version_no, seed=seed,
        model_version=f"{get_settings().GEMINI_MODEL_FAST}/{PROMPT_VERSION}",
        n_personas=funnel.n_screened, n_eligible=funnel.n_eligible, eligible_frac=funnel.eligible_frac,
        n_agents=len(decisions), enroll_rate=len(enrolled) / len(decisions) if decisions else 0,
        completion_rate=(len(enrolled) - len(dropouts)) / len(enrolled) if enrolled else 0,
        dropout_curve=[sum(d <= v.visit_no for d in dropouts) / len(enrolled) if enrolled else 0
                       for v in protocol.visits],
        median_dropout_visit=median_low(dropouts) if dropouts else None,
        top_reasons=[ReasonShare(tag=tag, share=count / len(adverse))
                     for tag, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5]],
        funnel=funnel, run_at=datetime.now(UTC))


def persist_run(summary: SimRunSummary, personas: Sequence[Persona], mask: np.ndarray,
                decisions: Sequence[AgentDecision]) -> None:
    """One transaction: preserve NULL enrollment for eligible, unsampled personas."""
    row = summary.model_dump(mode="json")
    columns = list(row)
    values = [Jsonb(row[c]) if c in {"dropout_curve", "top_reasons", "funnel"} else row[c]
              for c in columns]
    by_id = {d.persona_id: d for d in decisions}
    outcomes = []
    for persona, eligible in zip(personas, mask, strict=True):
        decision = by_id.get(persona.persona_id)
        outcomes.append((summary.run_id, persona.persona_id, bool(eligible),
                         decision.enroll if decision else None, decision.dropout_visit if decision else None,
                         decision.reason if decision else None,
                         list(decision.burden_tags) if decision else None, Jsonb(persona.model_dump(mode="json"))))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO sim_runs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})", values)
        cur.executemany("INSERT INTO persona_outcomes "
                        "(run_id, persona_id, eligible, enrolled, dropout_visit, reason, burden_tags, persona) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", outcomes)


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
    if n < 0 or max_agents < 0:
        raise ValueError("n and max_agents must be nonnegative")
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be unique")
    summaries = []
    for seed in seeds:
        personas = generate_cohort(n, seed, protocol.setting)
        if len(personas) != n or len({p.persona_id for p in personas}) != n:
            raise ValueError("cohort must return n personas with unique ids")
        mask, funnel = screen(protocol.rules, cohort_frame(personas), trial_id=protocol.trial_id)
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != (n,) or int(mask.sum()) != funnel.n_eligible or funnel.n_screened != n:
            raise ValueError("screen mask and funnel disagree")
        indices = np.flatnonzero(mask)
        if len(indices) > max_agents:
            indices = np.sort(np.random.default_rng(seed).choice(indices, max_agents, replace=False))
        sampled = [personas[i] for i in indices]
        decisions = run_agents(sampled, protocol, seed) if sampled else []
        if [d.persona_id for d in decisions] != [p.persona_id for p in sampled]:
            raise ValueError("agent decisions must match the sampled personas in order")
        summary = summarize(protocol, seed, funnel, decisions)
        if persist:
            persist_run(summary, personas, mask, decisions)
        summaries.append(summary)
    return summaries
