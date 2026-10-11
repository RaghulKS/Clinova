"""Registered snapshots and exact adjacent-version field changes."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from clinova.contracts import VersionEvent


def normalize(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", text or ""))).strip()


def fields(study):
    p = study.get("protocolSection", study)
    s, d = p.get("statusModule", {}), p.get("designModule", {})
    enrollment = d.get("enrollmentInfo", {})
    return {
        "eligibility": normalize(p.get("eligibilityModule", {}).get("eligibilityCriteria")),
        "enrollment_count": enrollment.get("count"), "enrollment_type": enrollment.get("type"),
        "primary_completion_date": s.get("primaryCompletionDateStruct", {}).get("date"),
        "overall_status": s.get("overallStatus"), "why_stopped": s.get("whyStopped"),
        "location_count": len(p.get("contactsLocationsModule", {}).get("locations", [])),
    }


@dataclass
class HistoryResult:
    first: dict | None = None
    first_version_date: date | None = None
    events: list[VersionEvent] = field(default_factory=list)
    source: str = "earliest_vs_current"
    complete: bool = False
    metadata: dict = field(default_factory=dict)


def fetch_history(client, nct_id, current, *, full=False):
    result = HistoryResult()
    try:
        detail = client.get(f"/api/int/studies/{nct_id}", {"history": "true"})
        changes = sorted(detail.get("history", {}).get("changes", []), key=lambda c: c["version"])
        result.metadata["index"] = changes
        original = next((c for c in changes if c["version"] == 0), None)
        if original is None:
            result.metadata["missing_original_index"] = True
            return result
        snapshot = client.get(f"/api/int/studies/{nct_id}/history/0")
        if snapshot.get("studyVersion") != 0:
            raise ValueError("history endpoint did not return requested version zero")
        result.first = snapshot["study"]
        result.first_version_date = date.fromisoformat(original["date"])
        result.metadata["original_retrieved"] = True
        previous, previous_no = result.first, 0
        if not full:
            result.complete = len(changes) == 1
            result.source = "versions" if result.complete else "earliest_vs_current"
            return result
        for item in changes[1:]:
            version = item["version"]
            snapshot = client.get(f"/api/int/studies/{nct_id}/history/{version}")
            if snapshot.get("studyVersion") != version:
                raise ValueError("history endpoint returned a different version")
            new = snapshot["study"]
            if version == previous_no + 1:
                before, after = fields(previous), fields(new)
                for name, old in before.items():
                    if old != after[name]:
                        result.events.append(VersionEvent(
                            nct_id=nct_id, version_no=version, version_date=date.fromisoformat(item["date"]),
                            field=name, old_value=None if old is None else str(old),
                            new_value=None if after[name] is None else str(after[name]),
                        ))
            previous, previous_no = new, version
        result.complete = all(c["version"] == i for i, c in enumerate(changes))
        result.source = "versions" if result.complete else "earliest_vs_current"
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        result.metadata["error"] = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            result.metadata["http_status"] = exc.response.status_code
        if result.first is None:
            result.metadata["current_only"] = True
            try:
                fallback = client.get(f"/api/int/studies/{nct_id}/history/1")
                if fallback.get("studyVersion") == 1:
                    result.metadata.update({"current_only": False, "earliest_retrieved_version": 1,
                                            "earliest_snapshot": fallback["study"],
                                            "fallback_comparison": {"old": fields(fallback["study"]),
                                                                    "new": fields(current)}})
            except (httpx.HTTPError, KeyError, ValueError):
                pass
    return result
