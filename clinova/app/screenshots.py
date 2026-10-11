"""Take Playwright screenshots of every app view into ``docs/screens/``.

Usage (API and app already running):
    python -m clinova.app.screenshots [--url http://127.0.0.1:8501] [--out docs/screens]
One-time browser install: ``uv run playwright install chromium``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from clinova.common.config import REPO_ROOT

WIDE = {"width": 1920, "height": 1080}
TALL = {"width": 1920, "height": 2700}


def _shot(page: Page, out: Path, name: str, *, tall: bool = False) -> None:
    page.set_viewport_size(TALL if tall else WIDE)
    _idle(page)
    page.wait_for_timeout(900)  # let plotly finish drawing
    path = out / f"{name}.png"
    page.screenshot(path=str(path))
    print("wrote", path)


def _idle(page: Page) -> None:
    """Wait until Streamlit has finished its current rerun."""
    page.wait_for_timeout(500)
    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=90000)


def _open_tab(page: Page, name: str) -> None:
    page.get_by_role("tab", name=name).click()
    page.wait_for_timeout(1200)


def run(url: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=WIDE)
        page.goto(url)
        page.get_by_text("Your protocol").first.wait_for(timeout=60000)
        _shot(page, out, "01-input")

        # paste flow
        page.get_by_role("button", name="Use the example protocol").click()
        page.wait_for_timeout(800)
        page.get_by_role("button", name="Parse protocol").click()
        page.get_by_text("Review the parsed criteria").wait_for(timeout=30000)
        _shot(page, out, "02-parsed-rules", tall=True)

        page.get_by_role("button", name="Run Clinova").click()
        page.get_by_text("Biggest exclusion").wait_for(timeout=60000)
        _shot(page, out, "03-results", tall=True)

        page.get_by_role("button", name="Accept").nth(2).click()
        page.get_by_text("Before and after").wait_for(timeout=60000)
        page.wait_for_timeout(1500)
        _shot(page, out, "04-fix-accepted", tall=True)

        # NCT flow with the backtest reveal
        page.set_viewport_size(WIDE)
        page.get_by_text("1. Your protocol").click()  # the input box collapses once a protocol is loaded
        page.wait_for_timeout(600)
        page.get_by_text("Load a registered trial").click()
        page.wait_for_timeout(800)
        page.get_by_label("NCT ID").fill("NCT99999901")
        page.keyboard.press("Enter")
        page.get_by_role("button", name="Load protocol").click()
        page.get_by_text("Review the parsed criteria").wait_for(timeout=30000)
        page.get_by_role("button", name="Run Clinova").click()
        page.get_by_text("What happened next").wait_for(timeout=60000)
        _idle(page)
        page.set_viewport_size(WIDE)
        page.get_by_text("What happened next").scroll_into_view_if_needed()
        page.evaluate("window.scrollBy(0, 0)")
        _shot(page, out, "05-backtest-reveal")

        page.set_viewport_size(WIDE)
        for tab, name in (
            [("Validation", "06-validation"), ("Replay", "07-replay"), ("Community Pulse", "08-pulse"), ("Audit log", "09-audit")]
        ):
            _open_tab(page, tab)
            _shot(page, out, name, tall=tab == "Validation")
        browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m clinova.app.screenshots")
    ap.add_argument("--url", default="http://127.0.0.1:8501")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "screens")
    a = ap.parse_args(argv)
    run(a.url, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
