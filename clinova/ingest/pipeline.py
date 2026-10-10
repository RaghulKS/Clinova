"""Ingestion pipeline (owner: T11). See ARCHITECTURE.md 5.1 and docs/CONTRACTS.md."""

from __future__ import annotations

from typing import Literal


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
    raise NotImplementedError("T11 implements clinova.ingest.pipeline.run")
