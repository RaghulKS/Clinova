"""Two-pass resumable ingestion: originals first, full adjacent histories second."""

from __future__ import annotations

import json
import logging
from typing import Literal

from clinova.common.config import get_settings
from clinova.ingest.ctgov import CTGovClient
from clinova.ingest.history import fetch_history
from clinova.ingest.labels import label_trial
from clinova.ingest.load import load_trial
from clinova.ingest.pull import iter_studies, map_trial
from clinova.ingest.report import write_reports


def run(
    limit: int | None = None,
    *,
    scope: Literal["nsclc", "solid_tumors"] = "nsclc",
    resume: bool = True,
) -> dict[str, int]:
    """Pull Phase 2, 3 and 2/3 interventional trials from ClinicalTrials.gov API v2, read each
    trial's version history, write ``TrialRecord`` rows to ``trials``, ``VersionEvent`` rows to
    ``trial_versions`` and initial labels to ``outcomes``.

    Args:
        limit: stop after this many trials (None = all, about 1,500 for NSCLC).
        scope: ``nsclc`` (default) or ``solid_tumors`` (fallback when labels are thin).
        resume: skip trials already loaded with history.

    Returns:
        Counts, e.g. ``{"fetched": 1512, "loaded": 1512, "with_history": 1490, "labeled": 980}``.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    directory = get_settings().DATA_DIR / "ingest"
    directory.mkdir(parents=True, exist_ok=True)
    counts = {"fetched": 0, "loaded": 0, "with_history": 0, "labeled": 0, "failed": 0}
    # Originals reach downstream tasks before expensive full histories finish.
    with CTGovClient() as client:
        studies = list(iter_studies(client, scope=scope, limit=limit))
        counts["fetched"] = len(studies)
        for full in (False, True):
            for current in studies:
                nct = current["protocolSection"]["identificationModule"]["nctId"]
                checkpoint = directory / f"{nct}.json"
                saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
                if resume and saved.get("loaded") and (not full or saved.get("history_complete")):
                    continue
                try:
                    history = fetch_history(client, nct, current, full=full)
                    trial = map_trial(current, history.first, first_version_date=history.first_version_date,
                                      history_source=history.source)
                    trial, labels = label_trial(trial, history)
                    trial.current["_clinova_provenance"] = {**trial.current.get("_clinova_provenance", {}),
                                                           **history.metadata, **labels,
                                                           "history_complete": history.complete}
                    load_trial(trial, history.events)
                    temporary = checkpoint.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"loaded": True, "history_complete": history.complete,
                                                      "original": history.first is not None}), encoding="utf-8")
                    temporary.replace(checkpoint)
                    counts["loaded"] += 1
                    counts["with_history"] += int(history.first is not None)
                    counts["labeled"] += int(trial.label_accrual_fail is not None)
                    print(json.dumps({"nct_id": nct, "full": full, **counts}), flush=True)
                    if counts["loaded"] % 20 == 0:
                        write_reports()
                except Exception as exc:
                    # Database exception strings can include credentials.
                    counts["failed"] += 1
                    logging.getLogger(__name__).error("ingestion failed for %s (%s)", nct, type(exc).__name__)
            write_reports()
    return counts
