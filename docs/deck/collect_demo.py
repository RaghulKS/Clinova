"""Collect the demo backtest numbers the deck needs into docs/deck/data/demo.json.

Runs Clinova's own backtest on the demo trial's FIRST registered protocol (the same code path as
GET /trials/{nct}/backtest) and keeps only what the deck shows: funnel, flags, fixes, risks and
the sponsor's later rule changes. Refuses to write anything built from fixture data.

Usage (repo root, .env linked, after gate/2):
    uv run python docs/deck/collect_demo.py                      # in-process, DEMO_NCT_ID or NCT from docs/demo_trial.md
    uv run python docs/deck/collect_demo.py --url http://127.0.0.1:8000   # from a running API
    uv run python docs/deck/collect_demo.py --nct NCT03232892
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "deck" / "data" / "demo.json"


def demo_nct() -> str:
    try:
        from clinova.common.config import settings  # type: ignore

        s = settings() if callable(settings) else settings
        nct = getattr(s, "demo_nct_id", None) or getattr(s, "DEMO_NCT_ID", None)
        if nct:
            return str(nct)
    except Exception:
        pass
    m = re.search(r"\*\*Selected: (NCT\d{8})\*\*", (ROOT / "docs/demo_trial.md").read_text())
    if not m:
        sys.exit("no demo NCT id found")
    return m.group(1)


def from_api(url: str, nct: str) -> dict:
    import httpx

    r = httpx.get(f"{url.rstrip('/')}/trials/{nct}/backtest", timeout=1800)
    r.raise_for_status()
    return r.json()


def in_process(nct: str) -> dict:
    from clinova.api.backtest import run_backtest

    return json.loads(run_backtest(nct).model_dump_json())


def reduce(bt: dict) -> dict:
    run = bt.get("run") or {}
    if bt.get("source") == "fixture":
        sys.exit("backtest came from fixtures; refusing to write deck numbers from it")
    trial = bt["trial"]
    funnel = run.get("funnel")
    sims = run.get("sim") or []
    risks = run.get("risks") or []
    fixture_risk = [r for r in risks if str(r.get("model_version", "")).lower().startswith("fixture")]
    out = {
        "generated_by": "docs/deck/collect_demo.py",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": bt.get("source"),
        "nct_id": trial["nct_id"],
        "title": trial.get("title"),
        "why_stopped": trial.get("why_stopped"),
        "terminated": trial.get("completion_date"),
        "first_version_date": trial.get("first_version_date") or trial.get("first_posted"),
        "planned_enrollment": trial.get("planned_enrollment"),
        "actual_enrollment": trial.get("actual_enrollment"),
        "funnel": None,
        "sim": [{k: s.get(k) for k in ("seed", "n_agents", "enroll_rate", "completion_rate", "median_dropout_visit")}
                for s in sims],
        "risk": None if fixture_risk else risks,
        "flags": run.get("flags") or None,
        "fixes": run.get("fixes") or None,
        "changes": bt.get("changes") or [],
        "widened_fields": sorted({c["field"] for c in bt.get("changes") or [] if c["change"] in ("relaxed", "removed")}),
        "events": bt.get("events") or [],
    }
    if funnel:
        out["funnel"] = {
            "n_screened": funnel["n_screened"],
            "n_eligible": funnel["n_eligible"],
            "eligible_frac": funnel["eligible_frac"],
            "top_exclusions": funnel.get("marginal", [])[:8],
            "skipped_rules": funnel.get("skipped_rules", []),
        }
    # Fixture flags or fixes carry FIXTURE ids; never let them through.
    for key in ("flags", "fixes"):
        items = out[key] or []
        if any("FIXTURE" in json.dumps(x) for x in items):
            print(f"dropping {key}: fixture content", file=sys.stderr)
            out[key] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nct")
    ap.add_argument("--url")
    ap.add_argument("--raw-out", help="also save the full backtest JSON here")
    args = ap.parse_args()
    nct = args.nct or demo_nct()
    bt = from_api(args.url, nct) if args.url else in_process(nct)
    if args.raw_out:
        Path(args.raw_out).write_text(json.dumps(bt, indent=1, default=str))
    out = reduce(bt)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"wrote {OUT}: flags={'yes' if out['flags'] else 'no'} fixes={'yes' if out['fixes'] else 'no'} "
          f"risk={'yes' if out['risk'] else 'no'} widened={out['widened_fields']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
