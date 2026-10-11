"""G1: rank demo-trial candidates by the gate rule.

Test split, terminated for accrual, eligibility widened (first registered text vs current text, via
parse + diff_rules) on a ClinicalField other than `other`, most parseable first-version rules, ties ->
earliest termination. No dated version history exists yet, so "before termination" is assumed from the
current text of a terminated trial and is flagged as unverified in the output.

    uv run python scripts/pick_demo_trial.py > .data/demo_pick.json
"""
from __future__ import annotations

import json
import sys

from clinova.common.db import get_conn
from clinova.contracts import ClinicalField, Phase
from clinova.parse.diff import diff_rules
from clinova.parse.parser import parse_protocol


def main() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT nct_id, phase, eligibility_text_first, eligibility_text_current, completion_date, why_stopped "
            "FROM trials WHERE split='test' AND status='TERMINATED' AND stop_category='accrual' "
            "AND label_accrual_fail AND eligibility_text_first IS NOT NULL ORDER BY nct_id"
        ).fetchall()
    out = []
    for nct, phase, first, current, ended, why in rows:
        phase = Phase(phase) if phase else None
        item = {"nct_id": nct, "termination": ended and ended.isoformat(), "why_stopped": why}
        try:
            old = parse_protocol(first, trial_id=nct, version_no=0, phase=phase)
            item["parseable_rules"] = sum(r.parse_status != "unparseable" and r.field != ClinicalField.other
                                          for r in old.rules)
            item["setting"] = old.setting
            if current and current.strip() != first.strip():
                new = parse_protocol(current, trial_id=nct, version_no=1, phase=phase)
                changes = diff_rules(old.rules, new.rules)
            else:
                changes = []
            item["widened"] = [c.model_dump(mode="json") for c in changes
                               if c.change in ("relaxed", "removed") and c.field != ClinicalField.other]
            item["all_changes"] = [f"{c.field.value}:{c.change}" for c in changes]
        except Exception as exc:  # 429s etc: report, keep going
            item["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(nct, item.get("parseable_rules"), len(item.get("widened", [])), item.get("error", ""), file=sys.stderr)
        out.append(item)
    ranked = sorted((i for i in out if i.get("widened")),
                    key=lambda i: (-i["parseable_rules"], i["termination"] or "9999"))
    print(json.dumps({"ranked": [i["nct_id"] for i in ranked], "candidates": out}, indent=1, default=str))


if __name__ == "__main__":
    main()
