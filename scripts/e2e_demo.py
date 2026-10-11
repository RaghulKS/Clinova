"""G1 end-to-end demo run: first registered version -> parse -> 1,000 personas -> screen -> live agents
(2 seeds) -> sim_runs / persona_outcomes. Prints a JSON summary (funnel, top exclusions, rates).

    CLINOVA_MODE=live uv run python scripts/e2e_demo.py [NCT...] > .data/G1-e2e.json
"""
from __future__ import annotations

import json
import sys

from clinova.common.config import get_settings
from clinova.simulate.__main__ import registered_protocols
from clinova.simulate.runner import simulate_trial


def main() -> None:
    nct = sys.argv[1] if len(sys.argv) > 1 else get_settings().DEMO_NCT_ID
    if not nct:
        raise SystemExit("no trial: pass an NCT id or set DEMO_NCT_ID")
    protocols = registered_protocols(1, nct)
    if not protocols:
        raise SystemExit(f"{nct} is not in trials")
    protocol = protocols[0]
    summaries = simulate_trial(protocol, n=1000, seeds=(1, 2), persist=True)
    rules = {r.rule_id: r for r in protocol.rules}
    out = {
        "nct_id": nct,
        "setting": protocol.setting,
        "n_rules": len(protocol.rules),
        "n_parseable": sum(r.parse_status != "unparseable" for r in protocol.rules),
        "n_visits": len(protocol.visits),
        "runs": [],
    }
    for s in summaries:
        out["runs"].append({
            "run_id": s.run_id, "seed": s.seed, "n_eligible": s.n_eligible, "eligible_frac": s.eligible_frac,
            "n_agents": s.n_agents, "enroll_rate": s.enroll_rate, "completion_rate": s.completion_rate,
            "dropout_curve": s.dropout_curve, "median_dropout_visit": s.median_dropout_visit,
            "top_reasons": [r.model_dump(mode="json") for r in s.top_reasons],
            "stages": [st.model_dump(mode="json") for st in s.funnel.stages] if s.funnel else [],
            "top_exclusions": [
                {**m.model_dump(mode="json"), "text": rules[m.rule_id].raw_text}
                for m in (s.funnel.marginal[:5] if s.funnel else []) if m.rule_id in rules
            ],
            "skipped_rules": s.funnel.skipped_rules if s.funnel else [],
        })
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
