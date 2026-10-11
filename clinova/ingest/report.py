"""Reports derived from persisted public trial evidence, never fixture numbers."""
import csv
import json
import random
from collections import Counter
from pathlib import Path

from clinova.common.config import REPO_ROOT, get_settings
from clinova.common.db import get_conn


def write_reports(output: Path | None = None):
    output = output or REPO_ROOT / "docs"
    output.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        rows = conn.execute("SELECT nct_id,status,split,stop_category,why_stopped,label_accrual_fail,"
                            "label_shortfall,label_amend,first_version,current,eligibility_text_first,"
                            "start_date,completion_date FROM trials ORDER BY nct_id").fetchall()
        event_count = conn.execute("SELECT count(*) FROM trial_versions").fetchone()[0]
    counts = {"total": len(rows), "status": dict(Counter(r[1] for r in rows)),
              "split": dict(Counter(r[2] for r in rows)),
              "originals": sum(bool(r[9]) for r in rows), "version_events": event_count}
    labels = {}
    for i, name in enumerate(("accrual_fail", "shortfall", "amend"), 5):
        known = [r[i] for r in rows if r[i] is not None]
        labels[name] = {"known": len(known), "positive": sum(known),
                        "prevalence": sum(known) / len(known) if known else None}
    counts["labels"] = labels
    (output / "data_report.md").write_text(
        "# CT.gov ingestion data report\n\nGenerated from Tiger Cloud by clinova.ingest.report.\n\n```json\n"
        + json.dumps(counts, indent=2) + "\n```\n\n"
        "Originals count confirmed version-0 snapshots only. Missing originals are excluded from first-registration "
        "features and shortfall labels. Unknown amendment evidence is null, never a negative label. "
        "Partial dates remain in raw JSON. A month-only start (YYYY-MM) is stored as the 1st of the month for the "
        "2018-01-01 split; other partial dates leave the date column null. Condition-search hits can include basket trials. "
        "The two-pass background job loads originals before full adjacent histories; coverage can increase.\n",
        encoding="utf-8")
    eligible = [r for r in rows if r[3] is not None and r[4]]
    sample = random.Random(get_settings().SEED).sample(eligible, min(40, len(eligible)))
    with (output / "labels_spotcheck.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("nct_id", "status", "stop_category", "why_stopped"))
        writer.writerows((r[0], r[1], r[3], r[4]) for r in sample)
    candidates = []
    for r in rows:
        if r[2] != "test" or r[5] is not True or r[7] is not True:
            continue
        amendments = r[10].get("_clinova_provenance", {}).get("amendments", [])
        for amendment in amendments:
            if amendment["change_class"] not in {"widened", "mixed"}:
                continue
            if not r[13] or amendment["date"] >= r[13].isoformat():
                continue
            score = min(len(r[11]), 10000) / 1000
            candidates.append((score, r[0], amendment, r[12], r[13]))
            break
    candidates.sort(key=lambda item: (-item[0], item[1]))
    text = "# Demo trial candidates\n\nSelection: test split, terminated for accrual, exact eligibility widening before completion; ranked by original eligibility characters / 1000, capped at 10.\n\n"
    if not candidates:
        text += "No fully verified candidates yet. History enrichment is incomplete; no reserve is presented as verified.\n"
    for score, nct, amendment, start, ended in candidates[:5]:
        text += (f"- {nct}: score {score:.3f}; start {start}; amendment {amendment['date']}; "
                 f"completion {ended}. {amendment['explanation']}\n")
    (output / "demo_trial.md").write_text(text, encoding="utf-8")
    return counts
