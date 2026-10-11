"""CT.gov discovery and leakage-safe first-registration mapping."""

from __future__ import annotations

import re
from collections.abc import Iterator
from copy import deepcopy
from datetime import date

from clinova.contracts import ENDED_STATUSES, OverallStatus, Phase, Split, TrialRecord
from clinova.ingest.ctgov import CTGovClient

_PHASES = {frozenset(["PHASE2"]): Phase.phase2, frozenset(["PHASE3"]): Phase.phase3,
           frozenset(["PHASE2", "PHASE3"]): Phase.phase2_3}


def iter_studies(client: CTGovClient, scope: str = "nsclc", limit: int | None = None) -> Iterator[dict]:
    """Yield unique current studies, terminated and other ended records first."""
    conditions = {"nsclc": "non-small cell lung cancer", "solid_tumors": "solid tumor"}
    if scope not in conditions:
        raise ValueError(f"Unsupported scope: {scope}")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    if limit == 0:
        return
    query = (f'AREA[ConditionSearch]("{conditions[scope]}") AND AREA[StudyType]INTERVENTIONAL '
             'AND (AREA[Phase]PHASE2 OR AREA[Phase]PHASE3) AND NOT AREA[Phase]PHASE1')
    seen: set[str] = set()
    for statuses in ("TERMINATED", "COMPLETED,WITHDRAWN", None):
        params = {"filter.advanced": query, "pageSize": 1000, "format": "json"}
        if statuses:
            params["filter.overallStatus"] = statuses
        tokens: set[str] = set()
        while True:
            page = client.get("/api/v2/studies", params=params)
            for study in page.get("studies", []):
                nct_id = study["protocolSection"]["identificationModule"]["nctId"]
                if nct_id in seen:
                    continue
                seen.add(nct_id)
                yield study
                if limit is not None and len(seen) >= limit:
                    return
            token = page.get("nextPageToken")
            if not token:
                break
            if token in tokens:
                raise ValueError("CT.gov repeated a pagination token")
            tokens.add(token)
            params["pageToken"] = token


def _exact_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _age(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+(Years?|Months?|Weeks?|Days?)", value, re.I)
    if not match:
        return None
    factors = {"year": 1, "month": 1 / 12, "week": 7 / 365.25, "day": 1 / 365.25}
    return float(match[1]) * factors[match[2].lower().rstrip("s")]


def map_trial(current: dict, first: dict | None = None, *, first_version_date: str | date | None = None,
              history_source: str = "earliest_vs_current") -> TrialRecord:
    """Map current outcomes and a verified version-0 study/protocol snapshot only.

    Callers must not pass later historical snapshots as ``first``. Missing originals
    retain empty first-side features; required title/phase remain discovery metadata.
    """
    current_p = deepcopy(current.get("protocolSection", current))
    first_p = deepcopy(first.get("protocolSection", first)) if first else {}
    identification = current_p["identificationModule"]
    if first_p.get("identificationModule", {}).get("nctId", identification["nctId"]) != identification["nctId"]:
        raise ValueError("First and current study IDs differ")
    design = first_p.get("designModule", {})
    phase_design = design if first_p else current_p.get("designModule", {})
    phases = frozenset(phase_design.get("phases", []))
    if phases not in _PHASES:
        raise ValueError(f"Unsupported phases: {sorted(phases)}")
    status_module = current_p.get("statusModule", {})
    status = OverallStatus(status_module["overallStatus"])
    raw_start = status_module.get("startDateStruct", {}).get("date")
    start = _exact_date(raw_start)
    if start is None and isinstance(raw_start, str) and re.fullmatch(r"\d{4}-\d{2}", raw_start):
        start = _exact_date(f"{raw_start}-01")  # month precision is enough for the 2018-01-01 split
    split = Split.pending
    if status in ENDED_STATUSES and start is not None:
        split = Split.train if start < date(2018, 1, 1) else Split.test
    provenance = {"first_registered_version_available": bool(first_p)}
    if not first_p:
        provenance["discovery_metadata_fields"] = ["title", "phase"]
    if status in ENDED_STATUSES and start is None:
        provenance["split_note"] = "Ended trial lacks a year-month start date; excluded from train/test."
    current_p["_clinova_provenance"] = provenance
    enrollment = design.get("enrollmentInfo", {})
    actual = current_p.get("designModule", {}).get("enrollmentInfo", {})
    eligibility = first_p.get("eligibilityModule", {})
    locations = first_p.get("contactsLocationsModule", {}).get("locations")
    arms_module = first_p.get("armsInterventionsModule", {})
    arms = arms_module.get("armGroups")
    sponsor = first_p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    design_info = design.get("designInfo", {})
    return TrialRecord(
        nct_id=identification["nctId"],
        title=first_p.get("identificationModule", {}).get("briefTitle", identification["briefTitle"]),
        phase=_PHASES[phases], conditions=first_p.get("conditionsModule", {}).get("conditions", []),
        start_date=start, first_posted=_exact_date(status_module.get("studyFirstPostDateStruct", {}).get("date")),
        first_version_date=_exact_date(first_version_date) if first_p else None,
        primary_completion_date=_exact_date(status_module.get("primaryCompletionDateStruct", {}).get("date")),
        completion_date=_exact_date(status_module.get("completionDateStruct", {}).get("date")),
        status=status, why_stopped=status_module.get("whyStopped"),
        planned_enrollment=enrollment.get("count") if enrollment.get("type") == "ESTIMATED" else None,
        actual_enrollment=actual.get("count") if actual.get("type") == "ACTUAL" else None,
        sponsor_class=sponsor.get("class"), lead_sponsor=sponsor.get("name"),
        n_sites=len(locations) if locations is not None else None,
        n_countries=len({x["country"] for x in locations if x.get("country")}) if locations is not None else None,
        n_arms=len(arms) if arms is not None else None, allocation=design_info.get("allocation"),
        masking=design_info.get("maskingInfo", {}).get("masking"),
        intervention_types=sorted({x["type"] for x in arms_module.get("interventions", []) if x.get("type")}),
        min_age_years=_age(eligibility.get("minimumAge")), max_age_years=_age(eligibility.get("maximumAge")),
        eligibility_text_first=eligibility.get("eligibilityCriteria", ""),
        eligibility_text_current=current_p.get("eligibilityModule", {}).get("eligibilityCriteria"),
        first_version=first_p, current=current_p, split=split, history_source=history_source,
    )
