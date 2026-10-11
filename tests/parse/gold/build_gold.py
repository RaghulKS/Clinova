"""Builds gold.json: 12 real NSCLC eligibility texts (fetched from ClinicalTrials.gov, see
scripts/fetch_gold_candidates.py) annotated BY HAND by the T12 author reading the text, not by Gemini.

Annotation = list of "I|E field operator value-json" strings. Only conditions that map to the
ClinicalField vocabulary are annotated; vague ones ("adequate organ function") are left out, so the
parser is expected to flag them unparseable and they do not count. Value "*" = a rule on this field
is expected but its threshold is ambiguous in the text (field-level match only, never counted in the
strict value score).

Usage: python build_gold.py <candidates.json>   (candidates.json is not committed)
"""

import json
import sys
from pathlib import Path

ANN = {
    "NCT01364012": [
        'I stage in ["IIIB","IVA","IVB"]', 'I histology not_in ["squamous"]', "I ecog le 1",
        "E prior_lines ge 1", 'E brain_mets in ["treated_stable","untreated"]', "E cardiac_condition present",
    ],
    "NCT02283320": [
        "I age ge 18", 'I stage ge "IIIB"', "I prior_platinum present", "I measurable_disease present", "I ecog le 1",
        "I consent_capacity present", "E active_infection present", "E pregnancy present", 'E brain_mets in "*"',
        "E prior_lines gt 1", "E cardiac_condition present",
    ],
    "NCT01319669": [
        "I age ge 18", "I age le 75", 'I platelets lt "*"', "I anc gt 1.5", "I ast_x_uln le 3", "I alt_x_uln le 3",
        "I bilirubin_x_uln le 2", "I ecog le 2", 'I life_expectancy_months ge "*"', "I consent_capacity present",
        "E active_infection present", 'E brain_mets in "*"', "E cardiac_condition present", "E pregnancy present",
        "E platelets gt 300",
    ],
    "NCT04262856": [
        "I age ge 18", 'I stage in ["IVA","IVB"]', "I prior_lines eq 0", 'I pdl1_tps ge "*"',
        'I egfr_status eq "negative"', 'I alk_status eq "negative"', "I ecog le 1", "I measurable_disease present",
        "E autoimmune_disease present",
    ],
    "NCT01217619": [
        "I consent_capacity present", "I age ge 18", 'I stage eq "IIIA"', 'I egfr_status eq "positive"',
        "I measurable_disease present", "I ecog le 1", 'I life_expectancy_months ge "*"', "E pregnancy present",
        'E egfr_status eq "negative"', "E prior_lines ge 1",
    ],
    "NCT02713529": [
        "I measurable_disease present", "I ecog le 1", 'E brain_mets in "*"', "E leptomeningeal present",
        "E interstitial_lung_disease present", "E autoimmune_disease present", "E active_infection present",
        "E pregnancy present",
    ],
    "NCT03565003": [
        "I consent_capacity present", "I age ge 18", 'I life_expectancy_months ge 3', "I measurable_disease present",
        "I ecog le 1", 'I prior_lines ge "*"', "E autoimmune_disease present", 'E brain_mets eq "untreated"',
        "E active_infection present", "E cardiac_condition present",
    ],
    "NCT02949843": [
        "I ecog le 3", "I bilirubin_x_uln le 1.5", "I ast_x_uln le 2.5", "I alt_x_uln le 2.5",
        "I consent_capacity present", "E pregnancy present", "E active_infection present", "E cardiac_condition present",
    ],
    "NCT03050554": [
        'I stage eq "I"', "I life_expectancy_months ge 9", "E autoimmune_disease present",
        "E active_infection present", "E cardiac_condition present", "E pregnancy present",
    ],
    "NCT00449657": [
        'I stage in ["IIIA","IIIB"]', "I age gt 18", "I ecog le 2", "I consent_capacity present", "E pregnancy present",
        'E life_expectancy_months lt "*"', "E active_infection present",
    ],
    "NCT03922997": [
        'I stage in ["IIIB","IVA","IVB"]', "I prior_platinum present", "I prior_lines le 2",
        "I measurable_disease present", "I ecog le 2", "I life_expectancy_months ge 3",
        'I egfr_status eq "negative"', 'I alk_status eq "negative"', 'E egfr_status eq "positive"',
        'E alk_status eq "positive"', 'E brain_mets in "*"', "E leptomeningeal present", "E pregnancy present",
        "E cardiac_condition present",
    ],
    "NCT01639001": [
        'I stage in ["IIIB","IVA","IVB"]', 'I histology not_in ["squamous"]', 'I alk_status eq "positive"',
        "I prior_lines eq 0", "I consent_capacity present", "E cardiac_condition present", "E pregnancy present",
        "E interstitial_lung_disease present", "E active_infection present",
    ],
}


def parse_ann(s: str) -> dict:
    kind, field, op, *rest = s.split(" ", 3)
    val = json.loads(rest[0]) if rest else None
    return {"kind": "inclusion" if kind == "I" else "exclusion", "field": field, "operator": op, "value": val}


if __name__ == "__main__":
    cands = {c["nct_id"]: c for c in json.load(open(sys.argv[1], encoding="utf-8"))}
    out = [
        {"nct_id": i, "title": cands[i]["title"], "text": cands[i]["text"], "rules": [parse_ann(a) for a in ann]}
        for i, ann in ANN.items()
    ]
    (Path(__file__).parent / "gold.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(out), sum(len(o["rules"]) for o in out))
