"""Read cached original protocols; write optimizer-owned ``fixes`` only."""
from __future__ import annotations

from collections.abc import Sequence

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from clinova.common.db import get_conn
from clinova.contracts import CriteriaRule, Fix, ParsedProtocol, Setting, TrialRecord
from clinova.parse.visits import estimate_visits


def load_protocol(trial_id: str) -> ParsedProtocol:
    """Build from stored version-zero rules without parsing or Gemini calls."""
    with get_conn() as conn:
        row = conn.cursor(row_factory=dict_row).execute("SELECT * FROM trials WHERE nct_id = %s", (trial_id,)).fetchone()
        rules = conn.cursor(row_factory=dict_row).execute(
            "SELECT rule_id, kind, field, operator, value, unit, raw_text, parse_status, locked, note "
            "FROM criteria_rules WHERE nct_id = %s AND version_no = 0 "
            "ORDER BY CASE kind WHEN 'inclusion' THEN 0 ELSE 1 END, "
            "NULLIF(regexp_replace(rule_id, '[^0-9]', '', 'g'), '')::int, rule_id", (trial_id,)).fetchall()
    if row is None:
        raise LookupError(f"Trial {trial_id} has not been ingested")
    if not rules:
        raise LookupError(f"Trial {trial_id} has no cached first-version rules; run clinova.parse first")
    row.pop("updated_at", None)
    trial = TrialRecord.model_validate(row)
    parsed = [CriteriaRule.model_validate(r) for r in rules]
    setting = Setting.any
    for rule in parsed:
        if rule.kind != "inclusion" or rule.parse_status != "ok":
            continue
        if rule.field in {"prior_platinum", "prior_immunotherapy"} and (
                rule.operator == "present" or rule.operator == "eq" and rule.value is True):
            setting = Setting.second_line_plus
        if rule.field == "prior_lines" and isinstance(rule.value, (int, float)):
            if rule.operator == "ge" and rule.value >= 1 or rule.operator == "gt" and rule.value >= 0:
                setting = Setting.second_line_plus
            elif rule.operator == "eq" and rule.value == 0 or rule.operator == "le" and rule.value == 0:
                setting = Setting.first_line
    arms = trial.first_version.get("armsInterventionsModule", {}).get("armGroups", [])
    return ParsedProtocol(trial_id=trial_id, version_no=0, phase=trial.phase, title=trial.title,
                          summary=trial.first_version.get("descriptionModule", {}).get("briefSummary"),
                          setting=setting, arms=[a.get("label", "Unspecified arm") for a in arms],
                          randomized=(True if trial.allocation == "RANDOMIZED" else
                                      False if trial.allocation == "NON_RANDOMIZED" else None),
                          rules=parsed, visits=estimate_visits(trial))


def save_fixes(fixes: Sequence[Fix]) -> int:
    """Upsert deterministically identified fixes in one transaction; reruns are idempotent."""
    with get_conn() as conn:
        for fix in fixes:
            payload = fix.model_dump(mode="json")
            metrics = {k: v for k, v in payload.items() if k not in {"fix_id", "trial_id", "label", "edits"}}
            conn.execute(
                "INSERT INTO fixes (fix_id, trial_id, label, edits, metrics) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (fix_id) DO UPDATE SET edits = EXCLUDED.edits, metrics = EXCLUDED.metrics, "
                "label = EXCLUDED.label",
                (fix.fix_id, fix.trial_id, fix.label, Jsonb(payload["edits"]), Jsonb(metrics)))
    return len(fixes)
