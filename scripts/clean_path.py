"""Clean-path test (owner: T31): fresh clone, no .env, demo mode, the demo trial through the real app in a browser.

    uv run python scripts/clean_path.py --docker      # docker compose up --build in the clone (needs Docker)
    uv run python scripts/clean_path.py               # same layout without Docker (what the Dockerfile does)

Both clone this repo's HEAD (commit first) into a temp folder that has no .env, wait for the app on
localhost:8501, then drive it with Playwright: load NCT03232892, run, accept a fix, check the numbers moved.
The non-Docker mode needs DATABASE_URL (from the environment or this repo's .env) pointing at a seeded
database (`make tiger-load`); the Docker mode brings its own database. Prints a JSON timing report.
One-time: `uv run playwright install chromium`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NCT = "NCT03232892"
APP, API = "http://localhost:8501", "http://localhost:8000"
# UI strings the flow depends on (clinova/app/main.py, panels.py). Change here if the copy changes.
UI = {"mode": "Load a registered trial", "load": "Load protocol", "run": "Run Clinova",
      "results": "4. Results", "accept": "Accept", "after": "Before and after"}


def wait_http(url: str, timeout: float) -> float:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=3)
            return time.time() - t0
        except Exception:
            time.sleep(2)
    raise SystemExit(f"timeout waiting for {url}")


def browser_flow() -> dict:
    from playwright.sync_api import sync_playwright

    t = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        t0 = time.time()
        page.goto(APP)
        page.get_by_text(UI["mode"]).wait_for(timeout=60000)
        page.get_by_text(UI["mode"]).click()
        page.get_by_label("NCT ID").fill(NCT)
        page.keyboard.press("Enter")
        page.get_by_role("button", name=UI["load"]).click()
        page.get_by_text("originally registered").first.wait_for(timeout=60000)
        t["load_s"] = round(time.time() - t0, 1)
        t1 = time.time()
        page.get_by_role("button", name=UI["run"]).click()
        page.get_by_text(UI["results"]).wait_for(timeout=300000)
        page.get_by_role("button", name=UI["accept"]).first.wait_for(timeout=60000)
        t["run_s"] = round(time.time() - t1, 1)
        n_fixes = page.get_by_role("button", name=UI["accept"]).count()
        t2 = time.time()
        page.get_by_role("button", name=UI["accept"]).first.click()
        page.get_by_text(UI["after"]).wait_for(timeout=120000)
        t["accept_s"] = round(time.time() - t2, 1)
        t["fix_cards"] = n_fixes
        assert n_fixes >= 3, f"expected three fix cards, saw {n_fixes}"
        assert page.get_by_text("Traceback").count() == 0
        b.close()
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="clinova-clean-"))
    clone = tmp / "clinova"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(REPO), str(clone)], check=True)
    assert not (clone / ".env").exists()
    report: dict = {"mode": "docker" if a.docker else "no-docker", "clone": str(clone)}
    procs: list[subprocess.Popen] = []
    t0 = time.time()
    # A clean machine has none of these: drop them from the child environment.
    env = {k: v for k, v in os.environ.items() if k not in ("GEMINI_API_KEY", "VIRTUAL_ENV")}
    try:
        if a.docker:
            subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=clone, check=True, env=env)
        else:
            if "DATABASE_URL" not in env:
                from clinova.common.config import get_settings

                env["DATABASE_URL"] = get_settings().DATABASE_URL.get_secret_value()
            env.update(CLINOVA_MODE="demo", MODEL_VERSION="v1.0", CLINOVA_API_URL=API, PYTHONUTF8="1")
            subprocess.run(["uv", "sync", "--frozen", "--no-dev"], cwd=clone, check=True, env=env)
            report["sync_s"] = round(time.time() - t0, 1)
            (clone / "models").mkdir()
            shutil.copytree(clone / "db/seed/files/bundle/v1.0", clone / "models/v1.0")
            (clone / ".data/models").mkdir(parents=True)
            (clone / ".data/model").mkdir(parents=True)
            shutil.copy(clone / "db/seed/files/surrogate.json", clone / ".data/models")
            shutil.copy(clone / "db/seed/files/cohort_any_2000_42.parquet", clone / ".data/model")
            run = ["uv", "run", "--frozen", "--no-dev", "python"]
            subprocess.run([*run, "scripts/seed.py", "load"], cwd=clone, check=True, env=env)
            procs.append(subprocess.Popen([*run, "scripts/demo_serve.py"], cwd=clone, env=env))
            wait_http(f"{API}/health", 120)
            procs.append(subprocess.Popen(
                [*run, "-m", "clinova.app", "--server.port=8501", "--server.headless=true"], cwd=clone, env=env))
        wait_http(f"{APP}/_stcore/health", 600)
        report["up_s"] = round(time.time() - t0, 1)  # clone excluded from nothing: this is wall time from clone
        report["flow"] = browser_flow()
        report["result"] = "pass"
    finally:
        for p in procs:
            p.terminate()
        if a.docker:
            subprocess.run(["docker", "compose", "down", "-v"], cwd=clone, env=env)
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        print(json.dumps(report, indent=1))
    sys.exit(0)


if __name__ == "__main__":
    main()
