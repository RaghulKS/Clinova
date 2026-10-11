"""Take Playwright screenshots of every app view at 1920x1080 into ``docs/screens/``.

Usage (API and app already running):
    python -m clinova.app.screenshots [--url http://127.0.0.1:8501] [--nct NCT03232892] [--out docs/screens]
One-time browser install: ``uv run playwright install chromium``.

The main flow follows the demo video: load the trial as first registered, run, funnel and risk, flags,
the backtest reveal, fixes, accept one. If the trial's run has no fixes (optimizer not connected), the fix
views are taken on the example protocol instead and carry its "fixture data" label.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from clinova.common.config import REPO_ROOT, get_settings

WIDE = {"width": 1920, "height": 1080}


def _idle(page: Page, timeout: int = 900_000) -> None:
    """Wait until Streamlit has finished its current rerun."""
    page.wait_for_timeout(600)
    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=timeout)


def _shot(page: Page, out: Path, name: str, *, full: bool = False) -> None:
    _idle(page)
    page.wait_for_timeout(1200)  # let plotly finish drawing
    path = out / f"{name}.png"
    page.screenshot(path=str(path), full_page=full)
    print("wrote", path)


def _scroll_to(page: Page, text: str, offset: int = -70) -> None:
    loc = page.get_by_text(text).first
    loc.wait_for(timeout=900_000)
    loc.evaluate("e => e.scrollIntoView({block: 'start'})")
    page.evaluate(
        """off => { const m = document.querySelector('[data-testid="stMain"]') || document.querySelector('section.main');
                    if (m) m.scrollBy(0, off); window.scrollBy(0, off); }""",
        offset,
    )
    page.wait_for_timeout(500)


def _open_tab(page: Page, name: str) -> None:
    page.get_by_role("tab", name=name).click()
    page.wait_for_timeout(1500)


def _scroll_top(page: Page) -> None:
    page.evaluate(
        """() => { const m = document.querySelector('[data-testid="stMain"]') || document.querySelector('section.main');
                    if (m) m.scrollTo(0, 0); window.scrollTo(0, 0); }"""
    )


def _accept_and_shoot(page: Page, out: Path, prefix: str = "") -> None:
    _scroll_to(page, "Suggested fixes")
    _shot(page, out, f"06-fixes{prefix}")
    page.get_by_role("button", name="Accept").first.click()
    page.get_by_text("Before and after", exact=False).first.wait_for(timeout=120_000)
    _idle(page)
    _scroll_to(page, "Suggested fixes")
    page.evaluate(
        """() => { const m = document.querySelector('[data-testid="stMain"]'); if (m) m.scrollBy(0, 420); }"""
    )
    _shot(page, out, f"07-fix-accepted{prefix}")


def run(url: str, nct: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=WIDE, device_scale_factor=1)
        page.set_default_timeout(900_000)
        page.goto(url)
        page.get_by_text("Your protocol").first.wait_for(timeout=60_000)
        _shot(page, out, "01-input")

        # 0:20 load the registered trial as first registered
        page.get_by_label("NCT ID").fill(nct)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Load protocol").click()
        page.get_by_text("Review the parsed criteria").wait_for(timeout=120_000)
        _shot(page, out, "02-parsed-rules")

        page.get_by_role("button", name="Run Clinova").click()
        page.get_by_text("Where enrollment stalls, and how likely trouble is").wait_for(timeout=900_000)
        _idle(page)
        _scroll_to(page, "Where enrollment stalls, and how likely trouble is")
        _shot(page, out, "03-funnel-risk")
        _scroll_to(page, "Where to look first")
        _shot(page, out, "04-flags")
        _scroll_to(page, "What happened next")
        _idle(page)
        _shot(page, out, "05-reveal")

        if page.get_by_role("button", name="Accept").count() > 0:
            _accept_and_shoot(page, out)
        else:
            # no fixes on the trial yet: take the fix views on the example protocol (labelled fixture data)
            _scroll_top(page)
            page.get_by_text("1. Your protocol").click()
            page.wait_for_timeout(600)
            page.get_by_text("Paste a protocol").click()
            page.wait_for_timeout(800)
            page.get_by_role("button", name="Use the example protocol").click()
            page.wait_for_timeout(800)
            page.get_by_role("button", name="Parse protocol").click()
            page.get_by_text("Review the parsed criteria").wait_for(timeout=120_000)
            _idle(page)
            page.get_by_role("button", name="Run Clinova").click()
            page.get_by_text("Suggested fixes").wait_for(timeout=900_000)
            _idle(page)
            _accept_and_shoot(page, out)

        for tab, name in (
            ("Validation", "08-validation"),
            ("Replay", "09-replay"),
            ("Community Pulse", "10-pulse"),
            ("Audit log", "11-audit"),
        ):
            _scroll_top(page)
            _open_tab(page, tab)
            _shot(page, out, name)
        browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m clinova.app.screenshots")
    ap.add_argument("--url", default="http://127.0.0.1:8501")
    ap.add_argument("--nct", default=get_settings().DEMO_NCT_ID or "NCT99999901")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "screens")
    a = ap.parse_args(argv)
    run(a.url, a.nct, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
