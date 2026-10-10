"""Look and feel: calm, clinical, one accent colour, readable at 1920x1080."""

from __future__ import annotations

import html

ACCENT = "#0B6E78"  # the one accent: deep teal
ACCENT_SOFT = "#E3F1F2"
ACCENT_MID = "#7FB8BE"
INK = "#1D2733"
MUTED = "#5F6B7A"
LINE = "#DDE3E9"
BG = "#F5F7F9"
CARD = "#FFFFFF"
GREY_BAR = "#C4CCD4"
GREY_DARK = "#8B96A3"

CSS = f"""
<style>
:root {{ --accent: {ACCENT}; --accent-soft: {ACCENT_SOFT}; --ink: {INK}; --muted: {MUTED}; --line: {LINE}; }}
.stApp {{ background: {BG}; color: {INK}; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1840px; }}
h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.01em; }}
h1 {{ font-size: 1.9rem; font-weight: 650; margin-bottom: 0.1rem; }}
h3 {{ font-size: 1.15rem; font-weight: 650; }}
.cl-sub {{ color: {MUTED}; font-size: 1.0rem; margin-bottom: 1.1rem; }}
.cl-section {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; color: {MUTED}; margin: 1.2rem 0 0.4rem; font-weight: 650; }}
.cl-panel-title {{ display: flex; align-items: center; gap: 0.6rem; font-size: 1.2rem; font-weight: 650; margin: 0 0 0.2rem; }}
.cl-panel-sub {{ color: {MUTED}; font-size: 0.88rem; margin-bottom: 0.7rem; }}
.cl-card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 0.95rem 1.1rem; margin-bottom: 0.75rem; }}
.cl-card.accent {{ border-left: 4px solid {ACCENT}; }}
.cl-card h4 {{ margin: 0 0 0.25rem; font-size: 0.98rem; font-weight: 650; }}
.cl-big {{ font-size: 2.3rem; font-weight: 680; color: {ACCENT}; line-height: 1.05; }}
.cl-range {{ color: {MUTED}; font-size: 0.9rem; }}
.cl-badge {{ display: inline-block; padding: 0.08rem 0.55rem; border-radius: 999px; font-size: 0.76rem; font-weight: 620; border: 1px solid {LINE}; color: {MUTED}; background: #fff; }}
.cl-badge.high {{ background: {ACCENT}; color: #fff; border-color: {ACCENT}; }}
.cl-badge.medium {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-color: {ACCENT_MID}; }}
.cl-badge.low {{ background: #fff; color: {MUTED}; border-style: dashed; }}
.cl-tag {{ display: inline-block; padding: 0.05rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 650; letter-spacing: 0.03em; text-transform: uppercase; background: #fff; color: {MUTED}; border: 1px dashed {GREY_DARK}; vertical-align: middle; }}
.cl-driver {{ font-size: 0.88rem; margin: 0.18rem 0; color: {INK}; }}
.cl-driver .dir {{ color: {MUTED}; font-size: 0.78rem; }}
.cl-flag {{ border-left: 3px solid {ACCENT}; padding: 0.2rem 0 0.2rem 0.75rem; margin: 0.6rem 0; }}
.cl-flag .where {{ font-weight: 640; font-size: 0.93rem; }}
.cl-flag .lbl {{ color: {MUTED}; font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 0.3rem; }}
.cl-flag .txt {{ font-size: 0.9rem; }}
.cl-metric {{ display: flex; justify-content: space-between; border-top: 1px solid {LINE}; padding: 0.38rem 0; font-size: 0.9rem; }}
.cl-metric .v {{ font-weight: 650; }}
.cl-metric .r {{ color: {MUTED}; font-size: 0.8rem; margin-left: 0.4rem; font-weight: 400; }}
.cl-note {{ color: {MUTED}; font-size: 0.8rem; }}
.cl-precedent {{ font-size: 0.82rem; color: {MUTED}; margin: 0.15rem 0; }}
.cl-callout {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_MID}; border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 0.92rem; margin: 0.5rem 0; }}
.cl-warn {{ background: #fff; border: 1px dashed {GREY_DARK}; border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 0.9rem; margin: 0.5rem 0; }}
.cl-timeline {{ border-left: 2px solid {LINE}; margin-left: 0.4rem; padding-left: 1rem; }}
.cl-timeline .ev {{ margin: 0 0 0.7rem; position: relative; font-size: 0.9rem; }}
.cl-timeline .ev::before {{ content: ''; position: absolute; left: -1.38rem; top: 0.35rem; width: 9px; height: 9px; border-radius: 50%; background: {GREY_DARK}; }}
.cl-timeline .ev.key::before {{ background: {ACCENT}; box-shadow: 0 0 0 3px {ACCENT_SOFT}; }}
.cl-timeline .date {{ color: {MUTED}; font-size: 0.78rem; }}
div[data-testid="stButton"] > button[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #fff; font-weight: 620; }}
div[data-testid="stButton"] > button[kind="primary"]:hover {{ background: #095861; border-color: #095861; }}
button[data-baseweb="tab"] {{ font-size: 1rem; }}
</style>
"""


def esc(text: object) -> str:
    """HTML-escape anything that came from the API or the pasted protocol before it enters markup."""
    return html.escape("" if text is None else str(text))


def fixture_tag(source: object) -> str:
    return '<span class="cl-tag">fixture data</span>' if str(getattr(source, "value", source)) == "fixture" else ""
