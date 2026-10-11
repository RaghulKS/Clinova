"""Transactional initial labels and immutable registration snapshots."""
from datetime import UTC, date, datetime

from psycopg import sql
from psycopg.types.json import Jsonb

from clinova.common.db import get_conn

FROZEN = {"title", "phase", "conditions", "first_posted", "first_version_date", "planned_enrollment",
          "sponsor_class", "lead_sponsor", "n_sites", "n_countries", "n_arms", "allocation", "masking",
          "intervention_types", "min_age_years", "max_age_years", "eligibility_text_first", "first_version"}


def load_trial(trial, events):
    row = trial.model_dump(mode="python")
    columns = list(row)
    values = [Jsonb(row[c]) if c in {"first_version", "current"} else row[c] for c in columns]
    assignments = []
    for c in columns:
        if c == "nct_id":
            continue
        if c in FROZEN:
            assignments.append(sql.SQL("{} = CASE WHEN trials.first_version = '{{}}'::jsonb THEN EXCLUDED.{} ELSE trials.{} END").format(
                sql.Identifier(c), sql.Identifier(c), sql.Identifier(c)))
        else:
            assignments.append(sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)))
    statement = sql.SQL("INSERT INTO trials ({}) VALUES ({}) ON CONFLICT (nct_id) DO UPDATE SET {}, updated_at=now()").format(
        sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in columns),
        sql.SQL(",").join(assignments))
    with get_conn() as conn:
        conn.execute(statement, values)
        for event in events:
            conn.execute("INSERT INTO trial_versions (nct_id,version_no,version_date,field,old_value,new_value,change_class) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (nct_id,version_no,field,version_date) "
                         "DO UPDATE SET change_class=EXCLUDED.change_class", tuple(event.model_dump().values()))
        posted = trial.current.get("statusModule", {}).get("lastUpdatePostDateStruct", {}).get("date", "")
        observed = date.fromisoformat(posted) if len(posted) == 10 else None
        if observed:
            timestamp = datetime.combine(observed, datetime.min.time(), UTC)
            for label in ("accrual_fail", "shortfall", "amend"):
                value = getattr(trial, "label_" + label)
                if value is not None:
                    conn.execute("INSERT INTO outcomes (nct_id,label,value,source,observed_at) VALUES (%s,%s,%s,%s,%s) "
                                 "ON CONFLICT DO NOTHING", (trial.nct_id, label, float(value), "ctgov:T11", timestamp))
