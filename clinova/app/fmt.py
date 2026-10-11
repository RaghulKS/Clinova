"""Number formatting for the app (pure). House rules: percent to at most 1 decimal, dollars rounded to $1K."""

from __future__ import annotations

from datetime import date


def _trim(s: str) -> str:
    return s[:-2] if s.endswith(".0") else s


def pct(x: float | None, digits: int = 1) -> str:
    """0.229 -> '22.9%', 0.46 -> '46%'. At most one decimal; a trailing '.0' is dropped."""
    if x is None:
        return "n/a"
    digits = min(max(digits, 0), 1)
    return _trim(f"{x * 100:.{digits}f}") + "%"


def signed_pct(x: float) -> str:
    """Relative change: 0.38 -> '+38%'."""
    s = _trim(f"{x * 100:+.1f}")
    return ("±0" if s in ("+0", "-0") else s) + "%"


def signed_pts(x: float) -> str:
    """Absolute change in percentage points: -0.09 -> '-9 pts'."""
    s = _trim(f"{x * 100:+.1f}")
    return ("±0" if s in ("+0", "-0") else s) + " pts"


def pts(x: float) -> str:
    """Unsigned percentage points: 0.094 -> '9.4 points'."""
    return _trim(f"{x * 100:.1f}") + " points"


def money(x: float | None) -> str:
    """Dollars rounded to $1K: 141000 -> '$141K', 1_250_000 -> '$1,250K', 400 -> '<$1K', 0 -> '$0'."""
    if x is None:
        return "n/a"
    if x <= 0:
        return "$0"
    k = round(x / 1000)
    if k == 0:
        return "<$1K"
    return f"${k:,}K"


def nice_date(d: date | str | None) -> str:
    """2017-07-25 -> '25 Jul 2017'."""
    if d is None:
        return "unknown date"
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return f"{d.day} {d.strftime('%b %Y')}"


def risk_level(p: float) -> tuple[str, str]:
    """Plain-words level of a calibrated probability, with a CSS class.

    Fixed bands (a presentation choice, documented in docs/handoff/T32.md): high at 40% or more,
    moderate from 20%, else low. The number and its range are always shown next to the word."""
    if p >= 0.40:
        return "High", "high"
    if p >= 0.20:
        return "Moderate", "moderate"
    return "Low", "low"
