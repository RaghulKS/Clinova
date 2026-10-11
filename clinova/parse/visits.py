"""Visit schedules (owner: T12). See ARCHITECTURE.md 5.2 and docs/CONTRACTS.md."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from clinova.common import llm
from clinova.contracts import PROCEDURE_VOCAB, Phase, TrialRecord, Visit

HORIZON_DAYS = 365
_INVASIVE = {"biopsy", "bronchoscopy"}


class _VisitOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    day: int = Field(description="Study day; Day 1 = first dose; screening is negative.")
    procedures: list[str] = Field(description="Tags from the allowed list.")
    est_hours: float = Field(description="On-site hours incl. waiting and infusion; use the text if stated.")
    requires_travel: bool
    remote_ok: bool = Field(description="True if the text says it may be done locally, by phone or at home.")


class _VisitsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visits: list[_VisitOut] = Field(description="Chronological. Expand repeating cycles over the first 12 months (day <= 365).")


_PROMPT = """Extract the visit schedule from this protocol text as a chronological list of visits.
Expand repeating cycles into explicit visits for the first 12 months (study day <= 365). Day 1 = first dose;
screening visits have negative days. Procedure tags must come from: {vocab}. Use est_hours from the text when
stated, otherwise estimate (infusion visits 4-7 h, blood-only visits 1-2 h). Do not invent visits.

SCHEDULE TEXT:
\"\"\"
{text}
\"\"\"
"""


def _build(v: _VisitOut, n: int, estimated: bool = False) -> Visit:
    procs = [p if p in PROCEDURE_VOCAB else "other" for p in v.procedures]
    return Visit(
        visit_no=n, name=v.name, day=v.day, procedures=procs, est_hours=max(0.0, v.est_hours),
        requires_travel=v.requires_travel, invasive=bool(set(procs) & _INVASIVE), remote_ok=v.remote_ok, estimated=estimated,
    )


def extract_visits(schedule_text: str, *, trial_id: str) -> list[Visit]:
    """Read an explicit visit schedule from text into chronological Visits (``estimated=False``),
    expanding repeating cycles over the first 12 months of treatment."""
    out = llm.generate_json(
        _PROMPT.format(vocab=", ".join(PROCEDURE_VOCAB), text=schedule_text.strip()),
        _VisitsOut, namespace="parse_visits", seed=0, temperature=0.0,
    )
    vs = sorted(out.visits, key=lambda v: v.day)
    return [_build(v, i, False) for i, v in enumerate(vs, 1)]


def estimate_visits(trial: TrialRecord) -> list[Visit]:
    """Build a plausible schedule from FIRST-VERSION design fields only (cycle length, route,
    arms, imaging cadence typical for the phase). Every Visit has ``estimated=True``; the UI says so."""
    arms_blob = json.dumps(trial.first_version.get("armsInterventionsModule", {})).lower()
    oral = any(w in arms_blob for w in ("oral", "tablet", "capsule", "nib"))
    iv = any(w in arms_blob for w in ("infusion", "intravenous", "iv ", "carboplatin", "cisplatin", "pemetrexed", "docetaxel",
                                       "paclitaxel", "pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "mab"))
    if trial.phase == Phase.phase2:
        cycle, img_every = (28 if oral and not iv else 21), 42
    else:
        cycle, img_every = (28 if oral and not iv else 21), 63
    drug = ("oral_drug_dispense",) if oral and not iv else ("infusion",)
    drug_hours = 1.0 if drug[0] == "oral_drug_dispense" else (4.0 if (trial.n_arms or 1) < 2 else 5.0)
    specs: list[tuple[str, int, list[str], float, bool]] = [
        ("Screening", -14, ["physical_exam", "vitals", "blood_draw", "ecg", "ct_scan", "mri_brain", "questionnaire"], 5.0, False)
    ]
    next_img = img_every
    for k in range(0, HORIZON_DAYS // cycle + 1):
        day = 1 + k * cycle
        if day > HORIZON_DAYS:
            break
        procs = ["physical_exam", "vitals", "blood_draw", *drug, "adverse_event_review"]
        name, hours = f"Cycle {k + 1} Day 1", drug_hours + 1.5
        if k == 0:
            procs += ["pk_sampling", "questionnaire"]
            hours += 3.0
        if day >= next_img - 3:
            procs.append("ct_scan")
            name += f" + week {round((day - 1) / 7)} imaging"
            hours += 1.5
            next_img += img_every
        specs.append((name, day, procs, hours, False))
        if k == 0 and drug[0] == "infusion":  # first-cycle safety check, can be local
            specs.append(("Cycle 1 Day 8 safety labs", 8, ["vitals", "blood_draw", "adverse_event_review"], 1.5, True))
    specs.sort(key=lambda s: s[1])
    return [
        Visit(visit_no=i, name=n, day=d, procedures=p, est_hours=h, requires_travel=not remote, invasive=False,
              remote_ok=remote, estimated=True)
        for i, (n, d, p, h, remote) in enumerate(specs, 1)
    ]
