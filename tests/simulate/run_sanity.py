"""T14 opt-in live acceptance probe; ordinary pytest never imports or runs this script."""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from clinova.common.config import get_settings
from clinova.common.db import get_conn
from clinova.common.fixtures import load_fixture
from clinova.contracts import ParsedProtocol, Persona, Visit
from clinova.simulate import runner
from clinova.simulate.agents import PROMPT_VERSION
from clinova.simulate.surrogate import Surrogate


def fixture_adapter():
    spec = importlib.util.spec_from_file_location("fixture_screen", Path(__file__).parents[1] / "fixtures/make_funnel.py")
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    personas = load_fixture("personas.json", list[Persona])

    def cohort(n, seed, setting):
        if n != len(personas):
            raise ValueError("this acceptance probe uses exactly the 200 fixture personas")
        return personas

    def screen(rules, frame, *, trial_id):
        protocol = ParsedProtocol(trial_id=trial_id, rules=rules)
        failures = reference.screen(protocol, personas)
        return np.array([not failures[p.persona_id] for p in personas]), reference.build_funnel(protocol, personas)

    def frame(items):
        return pd.DataFrame([{**p.model_dump(exclude={"clinical"}), **p.clinical.model_dump()} for p in items])

    return cohort, screen, frame


def protocols():
    base = load_fixture("parsed_protocol.json", ParsedProtocol)
    result = []
    for name, count, spacing, hours in (("light", 6, 28, 1), ("medium", 12, 14, 3), ("heavy", 20, 7, 8)):
        visits = [Visit(visit_no=i + 1, name=f"Study visit {i + 1}", day=1 + i * spacing,
                        procedures=["blood_test", "infusion"] + (["biopsy"] if name == "heavy" and i in (0, 5) else []),
                        est_hours=hours, requires_travel=True, invasive=name == "heavy" and i in (0, 5))
                  for i in range(count)]
        result.append(base.model_copy(update={"trial_id": f"FIXTURE-T14-{name}", "visits": visits,
                                              "title": "FICTIONAL NSCLC treatment study",
                                              "summary": "Test an investigational drug plus standard treatment versus standard treatment. Benefit is uncertain."}))
    return result


def main():
    settings = get_settings()
    settings.CLINOVA_MODE = "live"
    started = datetime.now(UTC)
    cohort, screen, frame = fixture_adapter()
    runs = []
    planned = protocols()
    output = settings.DATA_DIR / f"T14-sanity-{PROMPT_VERSION}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with patch.object(runner, "generate_cohort", cohort), patch.object(runner, "screen", screen), patch.object(runner, "cohort_frame", frame):
        for protocol in planned:
            for summary in runner.simulate_trial(protocol, n=200, max_agents=50):
                runs.append(summary.model_dump(mode="json"))
                output.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")
                print(json.dumps({k: getattr(summary, k) for k in ("trial_id", "seed", "n_agents", "enroll_rate", "completion_rate")}), flush=True)
    ids = [r["run_id"] for r in runs]
    with get_conn() as conn:
        rows = conn.execute("SELECT run_id, eligible, enrolled, dropout_visit, persona FROM persona_outcomes WHERE run_id = ANY(%s)", (ids,)).fetchall()
        usage = conn.execute("SELECT count(*), COALESCE(sum(input_tokens),0), COALESCE(sum(output_tokens),0) FROM llm_usage WHERE namespace='agents' AND created_at >= %s", (started,)).fetchone()
    protocol_by_run = {r["run_id"]: next(p for p in planned if p.trial_id == r["trial_id"]) for r in runs}
    outcomes = pd.DataFrame(rows, columns=["run_id", "eligible", "enrolled", "dropout_visit", "persona"])
    outcomes["protocol"] = outcomes["run_id"].map(protocol_by_run)
    surrogate = Surrogate.fit(outcomes)
    surrogate.save(settings.DATA_DIR / "models/surrogate.json")
    timing_cohort = pd.concat([frame(cohort(200, 1, None))] * 5, ignore_index=True)
    timings = []
    for _ in range(100):
        start = time.perf_counter()
        surrogate.score(planned[1], timing_cohort)
        timings.append((time.perf_counter() - start) * 1000)
    result = {"runs": runs, "persisted_runs": len(ids), "persisted_persona_rows": len(rows),
              "agent_calls": int(usage[0]), "input_tokens": int(usage[1]), "output_tokens": int(usage[2]),
              "surrogate_score_1000_median_ms": float(np.median(timings)), "surrogate_score_1000_max_ms": max(timings),
              "started_at": started.isoformat(), "prompt_version": PROMPT_VERSION}
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}), flush=True)


if __name__ == "__main__":
    main()
