"""G2: split and label ended trials whose CT.gov start date is month-only (YYYY-MM).

    CLINOVA_MODE=demo uv run python scripts/relabel_month_starts.py [--dry-run]

Idempotent and resumable: it only touches rows still in split 'pending', so a re-run skips
rows it already moved and picks up rows that T11's job (re)loaded as pending.

Each row is rebuilt with T11's own path: map_trial -> label_trial -> load_trial(trial, []).
Demo mode is enforced: label_trial's Gemini stop-reason call answers from llm_cache only.
A cache miss falls back to the keyword rules below, but only if those rules agree with
Gemini on train-split labels (accuracy >= 0.90, Cohen's kappa >= 0.70 for accrual-vs-not).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.contracts import ENDED_STATUSES, StopCategory
from clinova.ingest.history import HistoryResult
from clinova.ingest.labels import label_trial
from clinova.ingest.load import load_trial
from clinova.ingest.pull import map_trial

MONTH_ONLY = r"^\d{4}-\d{2}$"
ENDED = sorted(str(s) for s in ENDED_STATUSES)
GATE_ACCURACY, GATE_KAPPA = 0.90, 0.70

# Rules mirror the Gemini prompt in labels.py: "accrual only when enrollment/recruitment problems
# are explicit; mixed efficacy and recruitment is other". Written against train-split labels only.
_ACCRUAL = re.compile(r"accru|accural|enrol|recruit|"
                      r"(lack|low|insufficient|poor|slow)\w*( number of)? (of )?(eligible )?(patients|participants|subjects)",
                      re.I)
_NOT_ACCRUAL = re.compile(r"closed enrol|enrol\w* (was |were )?(complete|closed|halted|stopped|paused|suspended)|"
                          r"(stop|halt|pause|suspend|clos)\w* (of )?(study )?(enrol|recruit|accru)|"
                          r"completed? (enrol|recruit|accru)|(enrol|recruit|accru)\w* (was |were )?(complete|finish|met)", re.I)
_OTHER_REASON = re.compile(r"\b(and|also|as well as|plus)\b.{0,40}\b(fund|financ|efficac|futil|safety|toxic|adverse|"
                           r"strateg|design|standard of care|landscape|priorit|drug supply|covid)|"
                           r"\b(fund|financ|efficac|futil|safety|toxic|adverse|strateg|design|standard of care|"
                           r"landscape|priorit|drug supply|covid)\w*.{0,40}\b(and|also|as well as|plus|,)\s", re.I)


def keyword_category(text: str | None) -> StopCategory:
    """accrual / other / unknown (no text). Only the accrual-vs-not decision is checked against Gemini."""
    if not text or not text.strip():
        return StopCategory.unknown
    if not _ACCRUAL.search(text) or _NOT_ACCRUAL.search(text) or _OTHER_REASON.search(text):
        return StopCategory.other
    return StopCategory.accrual


def agreement(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs: (gemini_is_accrual, keyword_is_accrual). Accuracy, confusion counts, Cohen's kappa."""
    n = len(pairs)
    c = Counter(pairs)
    tp, tn, fp, fn = c[(True, True)], c[(False, False)], c[(False, True)], c[(True, False)]
    if not n:
        return {"n": 0}
    po = (tp + tn) / n
    pe = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / n**2
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    return {"n": n, "accuracy": round(po, 4), "kappa": round(kappa, 4),
            "confusion": {"both_accrual": tp, "both_not": tn, "keyword_only_accrual": fp, "gemini_only_accrual": fn}}


def split_counts() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT split, count(*), count(label_accrual_fail), count(*) FILTER (WHERE label_accrual_fail), "
            "count(label_shortfall), count(*) FILTER (WHERE label_shortfall) FROM trials GROUP BY split ORDER BY split"
        ).fetchall()
    keys = ("trials", "accrual_labeled", "accrual_positive", "shortfall_labeled", "shortfall_positive")
    return {r[0]: dict(zip(keys, r[1:], strict=True)) for r in rows}


def gemini_rows() -> list[tuple[str, str, str]]:
    """(split, stop_category, why_stopped) for stored Gemini stop labels; keyword_G2 rows excluded."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT split, stop_category, why_stopped FROM trials WHERE split IN ('train','test') "
            "AND status IN ('TERMINATED','WITHDRAWN') AND stop_category IS NOT NULL AND btrim(coalesce(why_stopped,'')) <> '' "
            "AND coalesce(current->'_clinova_provenance'->>'stop_category_source','') <> 'keyword_G2'"
        ).fetchall()


def candidates() -> list[tuple]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT nct_id, current, first_version, first_version_date, history_source FROM trials "
            "WHERE split = 'pending' AND status = ANY(%s) "
            "AND current->'statusModule'->'startDateStruct'->>'date' ~ %s ORDER BY nct_id",
            (ENDED, MONTH_ONLY),
        ).fetchall()


def rebuild(row):
    """Stored row -> (trial labeled by label_trial in demo mode, label metadata, stored provenance)."""
    _, current, first, first_date, source = row
    current = dict(current)
    stored = current.pop("_clinova_provenance", {}) or {}
    trial = map_trial(current, first or None, first_version_date=first_date, history_source=source)
    history = HistoryResult(first=first or None, complete=bool(stored.get("history_complete")), events=[])
    trial, labels = label_trial(trial, history)
    return trial, labels, stored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="compute labels and agreement, write nothing")
    args = parser.parse_args(argv)
    if get_settings().CLINOVA_MODE != "demo":
        sys.exit("refusing to run outside CLINOVA_MODE=demo (no Gemini network calls allowed)")

    before = split_counts()
    rebuilt = [rebuild(row) for row in candidates()]
    # Stop-reason texts that went to Gemini: hit = label_trial got a cached Gemini category.
    asked = [(t, labels, stored) for t, labels, stored in rebuilt if t.status in {"TERMINATED", "WITHDRAWN"} and t.why_stopped]
    hits = [(t.split.value, t.stop_category.value, t.why_stopped) for t, labels, _ in asked if "stop_error" not in labels]

    dev, test = [], []
    for split, category, text in gemini_rows() + hits:
        pair = (category == "accrual", keyword_category(text) == StopCategory.accrual)
        (dev if split == "train" else test).append(pair)
    dev_agreement, test_agreement = agreement(dev), agreement(test)
    use_keywords = dev_agreement.get("accuracy", 0) >= GATE_ACCURACY and dev_agreement.get("kappa", 0) >= GATE_KAPPA

    sources, moved = Counter(), Counter()
    for trial, labels, stored in rebuilt:
        provenance = {**stored, **trial.current.get("_clinova_provenance", {}), "start_date_precision": "month"}
        provenance.pop("split_note", None)
        update = {}
        if trial.status in {"TERMINATED", "WITHDRAWN"}:
            if not trial.why_stopped:
                source = "no_stop_text"  # label_trial's own rule: stop_category unknown, no Gemini call
            elif "stop_error" not in labels:
                source = "gemini_cache"
            else:
                source = "keyword_G2"
                category = keyword_category(trial.why_stopped) if use_keywords else None
                update["stop_category"] = category
                update["label_accrual_fail"] = trial.status == "TERMINATED" and category == StopCategory.accrual
                labels = {k: v for k, v in labels.items() if k != "stop_error"}
                if not use_keywords:
                    labels["stop_category_note"] = "Gemini cache miss; keyword rules failed the agreement gate"
            provenance["stop_category_source"] = source
            sources[source] += 1
        if not use_keywords:
            update["label_accrual_fail"] = None  # never add only negatives from COMPLETED rows
        provenance.update(labels)
        trial = trial.model_copy(update=update)
        trial.current["_clinova_provenance"] = provenance
        moved[trial.split.value] += 1
        if not args.dry_run:
            load_trial(trial, [])

    summary = {"dry_run": args.dry_run, "candidates": len(rebuilt), "moved_to": dict(moved),
               "stop_category_source": dict(sources), "keyword_labels_applied": use_keywords,
               "train_agreement": dev_agreement, "test_agreement": test_agreement,
               "before": before, "after": before if args.dry_run else split_counts()}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
