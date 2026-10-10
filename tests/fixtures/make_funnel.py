"""Reference screen: regenerate tests/fixtures/funnel.json and sim_run_summary.json.

A throwaway reference implementation of the CriteriaRule semantics, used only to build and check
the fixtures (the real screen is clinova/simulate/screen.py). sim_run_summary.json is derived from
funnel.json plus the hand-written agent_decisions.json.

    uv run --frozen python tests/fixtures/make_funnel.py
"""

import operator
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median_low

from pydantic import TypeAdapter

from clinova.contracts import (
    STAGES,
    AgentDecision,
    BrainMets,
    CriteriaRule,
    Funnel,
    FunnelStage,
    ParsedProtocol,
    Persona,
    ReasonShare,
    RuleExclusion,
    SimRunSummary,
)

HERE = Path(__file__).parent
ORDINAL = {"stage": STAGES, "brain_mets": tuple(b.value for b in BrainMets)}
COMPARE = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge,
           "eq": operator.eq, "ne": operator.ne}
SYMBOL = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "=", "ne": "!="}
NAMES = {"age": "Age", "stage": "Stage", "ecog": "ECOG", "prior_lines": "Prior lines",
         "prior_platinum": "Prior platinum chemotherapy", "prior_immunotherapy": "Prior PD-(L)1 inhibitor",
         "measurable_disease": "Measurable disease (RECIST 1.1)", "life_expectancy_months": "Life expectancy",
         "anc": "ANC", "platelets": "Platelets", "hemoglobin": "Hemoglobin", "crcl_ml_min": "Creatinine clearance",
         "ast_x_uln": "AST", "alt_x_uln": "ALT", "bilirubin_x_uln": "Bilirubin", "consent_capacity": "Can consent",
         "brain_mets": "brain metastases", "autoimmune_disease": "autoimmune disease",
         "interstitial_lung_disease": "interstitial lung disease", "active_infection": "active infection",
         "pregnancy": "pregnancy or breastfeeding"}
RUN_ID = "5f3a9c2e8b1d4e7fa6c0d2b4e9f81a37"  # fixed uuid4 hex, so the fixture is reproducible
RUN_AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def satisfies(rule: CriteriaRule, x) -> bool:
    """patient[field] <operator> value."""
    op, v = rule.operator.value, rule.value
    if op in ("present", "absent"):
        return bool(x) == (op == "present")
    if op in ("in", "not_in"):
        return (x in v) == (op == "in")
    if isinstance(v, bool):  # 'eq true' / 'eq false' on a boolean field
        x = bool(x)
    elif rule.field in ORDINAL and op in ("lt", "le", "gt", "ge"):
        x, v = ORDINAL[rule.field].index(x), ORDINAL[rule.field].index(v)
    return COMPARE[op](x, v)


def fails(rule: CriteriaRule, p: Persona) -> bool:
    x = getattr(p, rule.field) if rule.field in ("age", "sex") else getattr(p.clinical, rule.field)
    if x is None:  # missing value passes: the screen never guesses
        return False
    sat = satisfies(rule, x)
    return not sat if rule.kind == "inclusion" else sat


def screen(protocol: ParsedProtocol, personas: list[Persona]) -> dict[str, list[str]]:
    """persona_id -> failed rule_ids in protocol order (unparseable rules skipped)."""
    applied = [r for r in protocol.rules if r.parse_status == "ok"]
    return {p.persona_id: [r.rule_id for r in applied if fails(r, p)] for p in personas}


def label(rule: CriteriaRule) -> str:
    name = NAMES.get(rule.field, rule.field.replace("_", " "))
    op = rule.operator.value
    if op in ("in", "not_in"):
        text = f"{name} {'' if op == 'in' else 'not '}{', '.join(rule.value)}"
    elif op in ("present", "absent"):
        text = name if op == "present" else f"No {name}"
    else:
        unit = f" {rule.unit}" if rule.unit not in (None, "score", "lines") else ""
        text = f"{name} {SYMBOL[op]} {rule.value}{unit}"
    if rule.kind == "exclusion":
        text = f"Excl. {text}"
    return f"{text} ({rule.rule_id})"


def build_funnel(protocol: ParsedProtocol, personas: list[Persona]) -> Funnel:
    failed = screen(protocol, personas)
    applied = [r for r in protocol.rules if r.parse_status == "ok"]
    n = len(personas)
    first = Counter(f[0] for f in failed.values() if f)
    stages, remaining = [FunnelStage(rule_id=None, label="Screened", remaining=n, excluded=0)], n
    for r in applied:
        remaining -= first[r.rule_id]
        stages.append(FunnelStage(rule_id=r.rule_id, label=label(r), remaining=remaining, excluded=first[r.rule_id]))
    total = Counter(rid for f in failed.values() for rid in f)
    only = Counter(f[0] for f in failed.values() if len(f) == 1)
    marginal = sorted(
        (RuleExclusion(rule_id=r.rule_id, field=r.field, label=label(r), excluded_n=total[r.rule_id],
                       excluded_frac=total[r.rule_id] / n, excluded_only_n=only[r.rule_id]) for r in applied),
        key=lambda m: -m.excluded_n,
    )
    return Funnel(trial_id=protocol.trial_id, n_screened=n, n_eligible=remaining, eligible_frac=remaining / n,
                  stages=stages, marginal=marginal, skipped_rules=list(protocol.unparseable))


def sim_summary(protocol: ParsedProtocol, funnel: Funnel, decisions: list[AgentDecision]) -> SimRunSummary:
    enrolled = [d for d in decisions if d.enroll]
    drops = [d.dropout_visit for d in enrolled if d.dropout_visit is not None]
    quitters = [d for d in decisions if not d.enroll or d.dropout_visit is not None]
    tags = Counter(t for d in quitters for t in set(d.burden_tags))
    top = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0].value))[:5]
    return SimRunSummary(
        run_id=RUN_ID, trial_id=protocol.trial_id, version_no=protocol.version_no, seed=1,
        model_version="fixture/agents-v1", n_personas=funnel.n_screened, n_eligible=funnel.n_eligible,
        eligible_frac=funnel.eligible_frac, n_agents=len(decisions), enroll_rate=len(enrolled) / len(decisions),
        completion_rate=(len(enrolled) - len(drops)) / len(enrolled),
        dropout_curve=[sum(v <= i for v in drops) / len(enrolled) for i in range(1, len(protocol.visits) + 1)],
        median_dropout_visit=median_low(drops) if drops else None,
        top_reasons=[ReasonShare(tag=t, share=c / len(quitters)) for t, c in top],
        funnel=funnel, run_at=RUN_AT,
    )


def load():
    protocol = ParsedProtocol.model_validate_json((HERE / "parsed_protocol.json").read_bytes())
    personas = TypeAdapter(list[Persona]).validate_json((HERE / "personas.json").read_bytes())
    return protocol, personas


def main():
    protocol, personas = load()
    funnel = build_funnel(protocol, personas)
    (HERE / "funnel.json").write_text(funnel.model_dump_json(indent=1) + "\n", encoding="utf-8")
    print(f"eligible {funnel.n_eligible}/{funnel.n_screened} = {funnel.eligible_frac:.1%}")
    for m in funnel.marginal[:6]:
        print(f"  {m.label}: {m.excluded_n} failing, {m.excluded_only_n} only this")
    decisions_path = HERE / "agent_decisions.json"
    if decisions_path.exists():
        decisions = TypeAdapter(list[AgentDecision]).validate_json(decisions_path.read_bytes())
        summary = sim_summary(protocol, funnel, decisions)
        (HERE / "sim_run_summary.json").write_text(summary.model_dump_json(indent=1) + "\n", encoding="utf-8")
        print(f"sim: enroll {summary.enroll_rate:.0%}, completion {summary.completion_rate:.0%}")


if __name__ == "__main__":
    main()
