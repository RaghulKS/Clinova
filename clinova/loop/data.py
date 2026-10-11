"""Read-only trial snapshots used by the learning loop."""

from __future__ import annotations

from datetime import date

import pandas as pd
from psycopg.rows import dict_row

from clinova.common.db import get_conn
from clinova.contracts import ENDED_STATUSES


def training_frame() -> pd.DataFrame:
    """Exact completion dates are retrospective event times, not CT.gov publication times."""
    with get_conn() as conn:
        rows = conn.cursor(row_factory=dict_row).execute(
            "SELECT * FROM trials t WHERE first_version <> '{}'::jsonb AND "
            "(status = ANY(%s) OR EXISTS (SELECT 1 FROM outcomes o WHERE o.nct_id=t.nct_id "
            "AND o.label='status' AND o.source LIKE 'ctgov:T21%%')) "
            "AND start_date IS NOT NULL ORDER BY nct_id",
            (list(ENDED_STATUSES),),
        ).fetchall()
        # Checker updates belong to outcomes; T11 retains ownership of trials.
        updates = conn.execute(
            "SELECT DISTINCT ON (nct_id,label) nct_id,label,value,observed_at FROM outcomes "
            "WHERE source LIKE 'ctgov:T21%' AND label <> 'status' "
            "ORDER BY nct_id,label,observed_at DESC"
        ).fetchall()
    by_id = {r["nct_id"]: r for r in rows}
    for nct, label, value, observed in updates:
        if nct in by_id and value is not None:
            by_id[nct]["label_" + label] = bool(value)
            by_id[nct]["completion_date"] = observed.date()
    return pd.DataFrame([r for r in rows if r["completion_date"] is not None])


def available_training(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Preserve the registered pre-2018 training split and the replay observation cutoff."""
    if df.empty:
        return df.copy()
    mask = (df["start_date"] < date(2018, 1, 1)) & (df["completion_date"] <= cutoff)
    mask &= df["first_version_date"].notna() & (df["first_version_date"] <= cutoff)
    return df.loc[mask].copy()
