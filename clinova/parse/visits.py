"""Visit schedules (owner: T12). See ARCHITECTURE.md 5.2 and docs/CONTRACTS.md."""

from __future__ import annotations

from clinova.contracts import TrialRecord, Visit


def extract_visits(schedule_text: str, *, trial_id: str) -> list[Visit]:
    """Read an explicit visit schedule from text into chronological Visits (``estimated=False``),
    expanding repeating cycles over the first 12 months of treatment."""
    raise NotImplementedError("T12 implements clinova.parse.visits.extract_visits")


def estimate_visits(trial: TrialRecord) -> list[Visit]:
    """Build a plausible schedule from FIRST-VERSION design fields only (cycle length, route,
    arms, imaging cadence typical for the phase). Every Visit has ``estimated=True``; the UI says so."""
    raise NotImplementedError("T12 implements clinova.parse.visits.estimate_visits")
