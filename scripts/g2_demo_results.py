"""Gate 2: freeze score, flags and optimizer results for the demo trial and both backups.

Writes docs/demo_results/<NCT>.json (risk scores + top-3 flags) and docs/demo_results/<NCT>_optimize.json
(the optimizer CLI report; fixes are also persisted to the `fixes` table). First registered version only.
No Gemini calls: protocols come from parsed first-version rules in the database.

    uv run python scripts/g2_demo_results.py [--model-version v1.0]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from clinova.model.features import load_protocols
from clinova.model.predict import flag_criteria, score
from clinova.optimize.__main__ import main as optimize_main

TRIALS = ["NCT03232892", "NCT03417882", "NCT06333678"]
# Fields whose rule the sponsor removed between the first and the current registered text (G1 diff,
# docs/demo_trial.md). Not date-verified: version histories are not loaded. NCT03417882's diff is
# suspected parser restructuring, so no field is claimed for it.
SPONSOR_CHANGED = {"NCT03232892": ["prior_platinum", "cardiac_condition"], "NCT03417882": [],
                   "NCT06333678": ["interstitial_lung_disease"]}
OUT = Path(__file__).resolve().parents[1] / "docs" / "demo_results"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-version", default=None)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    protocols = load_protocols(TRIALS)
    for nct in TRIALS:
        t0 = time.perf_counter()
        scores = score(nct, model_version=a.model_version)
        flags = flag_criteria(protocols[nct], k=3)
        fields = [f.field.value if hasattr(f.field, "value") else str(f.field) for f in flags]
        changed = SPONSOR_CHANGED[nct]
        report = {
            "trial_id": nct, "model_version": scores[0].model_version if scores else None,
            "scores": [s.model_dump(mode="json") for s in scores],
            "flags": [f.model_dump(mode="json") for f in flags],
            "sponsor_removed_fields": changed,
            "sponsor_removed_in_top3_flags": sorted(set(changed) & set(fields)) if changed else None,
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
        (OUT / f"{nct}.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        print(nct, json.dumps({"p": [round(s.p, 3) for s in scores], "flags": fields,
                               "hit": report["sponsor_removed_in_top3_flags"]}), flush=True)
        optimize_main(["--trial", nct, "--output", str(OUT / f"{nct}_optimize.json")])


if __name__ == "__main__":
    main()
