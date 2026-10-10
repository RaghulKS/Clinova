"""Optional MiroFish community-pulse client. HTTP/files only; never imports MiroFish code."""
from __future__ import annotations

import os
from pathlib import Path

import httpx

PULSE_DIR = Path(__file__).resolve().parents[2] / "docs" / "pulse"


def pulse_path(trial_id: str) -> Path:
    return PULSE_DIR / f"{trial_id}.md"


def load_pulse(trial_id: str) -> dict:
    """Cached report if present, else a clean 'not run' state. Never needs the service."""
    p = pulse_path(trial_id)
    if p.exists():
        return {"status": "ok", "trial_id": trial_id, "report": p.read_text()}
    return {"status": "not_run", "trial_id": trial_id, "report": None}


def build_seed_pack(summary: str, consent: str, schedule: str, personas: list[dict]) -> str:
    """Plain-text seed document to upload to the service."""
    people = "\n".join(f"- {p.get('age')}y {p.get('sex')}, {p.get('occupation')}" for p in personas[:20])
    return (
        f"# Trial summary\n{summary}\n\n# Consent summary\n{consent}\n\n"
        f"# Visit schedule\n{schedule}\n\n# Community members\n{people}\n\n"
        "Task: patients and site coordinators discuss this trial; report the burden they perceive."
    )


def service_up(base_url: str | None = None) -> bool:
    url = base_url or os.environ.get("MIROFISH_URL", "http://localhost:5001")
    try:
        return httpx.get(url, timeout=2).status_code < 500
    except httpx.HTTPError:
        return False


def save_pulse(trial_id: str, report: str) -> Path:
    PULSE_DIR.mkdir(parents=True, exist_ok=True)
    p = pulse_path(trial_id)
    p.write_text(report)
    return p
