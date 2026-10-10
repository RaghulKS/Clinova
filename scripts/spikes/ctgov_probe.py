"""Live public CT.gov spike. Run: uv run --no-project --with httpx python <file>."""
import argparse
import datetime as dt
import difflib
import html
import json
from pathlib import Path
import re
import time

import httpx

BASE = "https://clinicaltrials.gov"
QUERY = 'AREA[ConditionSearch]("non-small cell lung cancer") AND AREA[StudyType]INTERVENTIONAL AND (AREA[Phase]PHASE2 OR AREA[Phase]PHASE3) AND NOT AREA[Phase]PHASE1'


def enrich(output):
    evidence = json.loads(output.read_text(encoding="utf-8"))
    verified = []
    cache = {item["url"]: item["response"] for item in evidence["history"] if item["status"] == 200}
    with httpx.Client(timeout=60, headers={"User-Agent": "Clinova research spike (public metadata; 1 request/second)"}) as client:
        def get(path):
            url = BASE + path
            if url in cache: return cache[url]
            r = client.get(url); time.sleep(1)
            evidence["requests"].append({"url": url, "status": r.status_code})
            if r.status_code != 200:
                evidence.setdefault("history_errors", []).append({"url": url, "status": r.status_code})
                return None
            cache[url] = r.json()
            evidence["history"].append({"url": url, "status": r.status_code, "response": cache[url]})
            return cache[url]
        for candidate in evidence["candidates"]:
            nct = candidate["nct_id"]
            detail = get(f"/api/int/studies/{nct}?history=true")
            if detail is None: continue
            for change in detail.get("history", {}).get("changes", []):
                version = change["version"]
                if version == 0 or change["date"] <= candidate["start_date"] or "Eligibility" not in change.get("moduleLabels", []): continue
                before = get(f"/api/int/studies/{nct}/history/{version-1}")
                after = get(f"/api/int/studies/{nct}/history/{version}")
                exact = before is not None and after is not None
                if not exact:
                    before = get(f"/api/int/studies/{nct}/history/1")
                    after = detail
                    if before is None: continue
                def text(record):
                    value = record["study"]["protocolSection"]["eligibilityModule"].get("eligibilityCriteria", "")
                    return [re.sub(r"\s+", " ", html.unescape(line)).strip() for line in re.sub(r"<[^>]+>", "\n", value).splitlines() if line.strip()]
                diff = list(difflib.unified_diff(text(before), text(after), fromfile=f"version {version-1}" if exact else "available version 1", tofile=f"version {version}" if exact else "current record", lineterm=""))
                if not diff: continue
                verified.append({**candidate, "eligibility_change": diff, "change_date": change["date"], "version": version, "exact_adjacent_versions": exact,
                                 "all_post_start_eligibility_dates": [item["date"] for item in detail["history"]["changes"] if item["date"] > candidate["start_date"] and "Eligibility" in item.get("moduleLabels", [])]})
                break
            evidence["verified_candidates"] = verified
            output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            if len(verified) == 5: break
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(verified, indent=2))


def candidates(studies):
    result = []
    for study in studies:
        p = study["protocolSection"]
        s = p["statusModule"]
        start = s.get("startDateStruct", {}).get("date", "")
        why = s.get("whyStopped", "")
        if start[:4] >= "2018" and re.search(r"accru|slow recruit|lack of enroll|recruitment challenges|low recruit", why, re.I):
            result.append({"nct_id": p["identificationModule"]["nctId"],
                           "title": p["identificationModule"]["briefTitle"],
                           "start_date": start, "why_stopped": why,
                           "eligibility_change": "UNVERIFIED: historical record required"})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".data/ctgov_probe.json")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--amendments", action="store_true", help="Resume saved probe; verify five post-start text changes")
    args = parser.parse_args()
    if args.amendments:
        enrich(Path(args.output))
        return
    if args.self_check:
        assert candidates([]) == []
        fixture = {"protocolSection": {"identificationModule": {"nctId": "NCT00000000", "briefTitle": "fixture"}, "statusModule": {"startDateStruct": {"date": "2018-02"}, "whyStopped": "Poor accrual"}}}
        assert len(candidates([fixture])) == 1
        fixture["protocolSection"]["statusModule"]["startDateStruct"]["date"] = "2017-12"
        assert candidates([fixture]) == []
        print("self-check passed")
        return
    evidence = {"retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "requests": []}
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Clinova research spike (public metadata; 1 request/second)"}) as client:
        def get(path, params=None):
            response = client.get(BASE + path, params=params)
            evidence["requests"].append({"url": str(response.url), "status": response.status_code, "content_type": response.headers.get("content-type")})
            time.sleep(1)
            return response
        params = {"filter.advanced": QUERY, "countTotal": "true", "pageSize": 1}
        r = get("/api/v2/studies", params); r.raise_for_status()
        evidence["all_count"] = r.json()["totalCount"]
        evidence["example"] = r.json()["studies"][0]
        params["filter.overallStatus"] = "COMPLETED,TERMINATED"
        r = get("/api/v2/studies", params); r.raise_for_status()
        evidence["ended_count"] = r.json()["totalCount"]
        params.update({"filter.overallStatus": "TERMINATED", "pageSize": 1000})
        studies = []
        while True:
            r = get("/api/v2/studies", params); r.raise_for_status()
            payload = r.json(); studies.extend(payload["studies"])
            token = payload.get("nextPageToken")
            if not token: break
            params["pageToken"] = token
        evidence["terminated_count"] = len(studies)
        evidence["candidates"] = candidates(studies)
        evidence["history"] = []
        for candidate in evidence["candidates"][:5]:
            nct = candidate["nct_id"]
            for path in [f"/api/int/studies/{nct}?history=true", f"/api/int/studies/{nct}/history/1"]:
                r = get(path)
                try: payload = r.json()
                except ValueError: payload = {"body_preview": r.text[:200]}
                evidence["history"].append({"nct_id": nct, "url": str(r.url), "status": r.status_code, "response": payload})
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ("retrieved_at_utc", "all_count", "ended_count", "terminated_count", "candidates")}, indent=2))


if __name__ == "__main__":
    main()
