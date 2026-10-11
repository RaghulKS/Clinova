"""Fetch real NSCLC eligibility texts from ClinicalTrials.gov for the T12 gold set (dev tool, network)."""

import json
import sys

import httpx

r = httpx.get(
    "https://clinicaltrials.gov/api/v2/studies",
    params={
        "query.cond": "non-small cell lung cancer",
        "filter.overallStatus": "COMPLETED|TERMINATED",
        "query.term": "AREA[Phase](PHASE2 OR PHASE3)",
        "pageSize": 80,
        "fields": "NCTId|BriefTitle|Phase|EligibilityCriteria|StartDate",
    },
    timeout=40,
)
out = []
for s in r.json()["studies"]:
    p = s["protocolSection"]
    t = p.get("eligibilityModule", {}).get("eligibilityCriteria", "")
    if 700 < len(t) < 3000:
        out.append(
            {
                "nct_id": p["identificationModule"]["nctId"],
                "title": p["identificationModule"]["briefTitle"],
                "start": p["statusModule"].get("startDateStruct", {}).get("date"),
                "text": t,
            }
        )
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(len(out), [(o["nct_id"], o["start"], len(o["text"])) for o in out])
