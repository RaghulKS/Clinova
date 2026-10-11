"""CLI: ``python -m clinova.app`` runs ``streamlit run clinova/app/main.py`` (owner: T15)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    entry = Path(__file__).with_name("main.py")  # T15 creates clinova/app/main.py
    theme = [
        "--theme.base=light",
        "--theme.primaryColor=#0B6E78",
        "--theme.backgroundColor=#F5F7F9",
        "--theme.secondaryBackgroundColor=#ECF0F3",
        "--theme.textColor=#1D2733",
        "--client.toolbarMode=minimal",
        "--browser.gatherUsageStats=false",
    ]
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(entry), *theme, *(argv or sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
