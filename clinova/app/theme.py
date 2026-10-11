"""Look and feel: calm, clinical, one accent colour, readable at 1920x1080 and on a recorded video."""

from __future__ import annotations

import html

ACCENT = "#0B6E78"  # the one accent: deep teal
ACCENT_DARK = "#095861"
ACCENT_SOFT = "#E3F1F2"
ACCENT_SOFT_BAR = "#BFDCDF"
ACCENT_MID = "#7FB8BE"
INK = "#1D2733"
MUTED = "#5F6B7A"
LINE = "#DDE3E9"
BG = "#F5F7F9"
CARD = "#FFFFFF"
GREY_BAR = "#C4CCD4"
GREY_DARK = "#8B96A3"
# Semantic, used only for the "fixture data" label and unparseable rows: never for results.
AMBER_BG = "#FFF4D6"
AMBER_LINE = "#E2BE62"
AMBER_INK = "#6E5100"

CSS = f"""
<style>
:root {{ --accent: {ACCENT}; --accent-soft: {ACCENT_SOFT}; --ink: {INK}; --muted: {MUTED}; --line: {LINE}; }}
.stApp {{ background: {BG}; color: {INK}; }}
header[data-testid="stHeader"] {{ background: transparent; height: 2.2rem; }}
[data-testid="stToolbar"] {{ visibility: hidden; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1840px; }}
h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.01em; }}
.cl-top {{ display: flex; align-items: baseline; gap: 1rem; margin-bottom: 0.2rem; }}
.cl-logo {{ font-size: 1.85rem; font-weight: 720; color: {INK}; letter-spacing: -0.02em; }}
.cl-logo span {{ color: {ACCENT}; }}
.cl-sub {{ color: {MUTED}; font-size: 0.98rem; }}
.cl-section {{ font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.09em; color: {MUTED}; margin: 1.1rem 0 0.45rem; font-weight: 680; }}
.cl-h2 {{ font-size: 1.35rem; font-weight: 690; margin: 1.6rem 0 0.15rem; display: flex; align-items: center; gap: 0.6rem; }}
.cl-h2 .n {{ display: inline-flex; width: 1.7rem; height: 1.7rem; border-radius: 50%; background: {ACCENT}; color: #fff; font-size: 0.9rem; align-items: center; justify-content: center; }}
.cl-h2-sub {{ color: {MUTED}; font-size: 0.92rem; margin-bottom: 0.8rem; }}
.cl-panel-title {{ display: flex; align-items: center; gap: 0.6rem; font-size: 1.2rem; font-weight: 680; margin: 0 0 0.15rem; }}
.cl-panel-sub {{ color: {MUTED}; font-size: 0.88rem; margin-bottom: 0.7rem; }}
.cl-card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 0.95rem 1.1rem; margin-bottom: 0.75rem; }}
.cl-card.accent {{ border-left: 4px solid {ACCENT}; }}
.cl-card.tight {{ padding: 0.75rem 0.95rem; }}
.cl-card h4 {{ margin: 0 0 0.25rem; font-size: 0.98rem; font-weight: 680; }}
.cl-trial {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 0.9rem 1.2rem; margin: 0.4rem 0 0.2rem; display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }}
.cl-trial .t {{ font-weight: 680; font-size: 1.05rem; }}
.cl-trial .id {{ color: {MUTED}; font-size: 0.85rem; margin-left: 0.4rem; }}
.cl-asof {{ display: inline-block; background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border: 1px solid {ACCENT_MID}; border-radius: 999px; padding: 0.22rem 0.8rem; font-size: 0.9rem; font-weight: 640; white-space: nowrap; }}
.cl-big {{ font-size: 2.3rem; font-weight: 700; color: {ACCENT}; line-height: 1.05; }}
.cl-hero {{ font-size: 3.4rem; font-weight: 720; color: {ACCENT}; line-height: 1.0; letter-spacing: -0.02em; }}
.cl-mid {{ font-size: 1.9rem; font-weight: 700; color: {ACCENT}; line-height: 1.05; }}
.cl-level {{ display: inline-block; font-size: 0.82rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; border-radius: 4px; padding: 0.12rem 0.5rem; margin-left: 0.5rem; vertical-align: middle; }}
.cl-level.high {{ background: {ACCENT}; color: #fff; }}
.cl-level.moderate {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; border: 1px solid {ACCENT_MID}; }}
.cl-level.low {{ background: #fff; color: {MUTED}; border: 1px solid {LINE}; }}
.cl-range {{ color: {MUTED}; font-size: 0.9rem; margin-top: 0.2rem; }}
.cl-badge {{ display: inline-block; padding: 0.08rem 0.55rem; border-radius: 999px; font-size: 0.76rem; font-weight: 620; border: 1px solid {LINE}; color: {MUTED}; background: #fff; }}
.cl-badge.high {{ background: {ACCENT}; color: #fff; border-color: {ACCENT}; }}
.cl-badge.medium {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-color: {ACCENT_MID}; }}
.cl-badge.low {{ background: #fff; color: {MUTED}; border-style: dashed; }}
.cl-tag {{ display: inline-block; padding: 0.06rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; background: {AMBER_BG}; color: {AMBER_INK}; border: 1px solid {AMBER_LINE}; vertical-align: middle; }}
.cl-driver {{ font-size: 0.88rem; margin: 0.22rem 0; color: {INK}; padding-left: 0.9rem; position: relative; }}
.cl-driver::before {{ content: ''; position: absolute; left: 0.1rem; top: 0.5rem; width: 5px; height: 5px; border-radius: 50%; background: {ACCENT_MID}; }}
.cl-driver .dir {{ color: {MUTED}; font-size: 0.78rem; }}
.cl-flag {{ background: {CARD}; border: 1px solid {LINE}; border-top: 4px solid {ACCENT}; border-radius: 10px; padding: 0.85rem 1.05rem; height: 100%; margin-bottom: 0.6rem; }}
.cl-flag .rank {{ color: {ACCENT}; font-weight: 720; font-size: 0.8rem; letter-spacing: 0.06em; text-transform: uppercase; }}
.cl-flag .where {{ font-weight: 680; font-size: 1.0rem; margin: 0.15rem 0 0.2rem; }}
.cl-flag .lbl {{ color: {MUTED}; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; margin-top: 0.45rem; font-weight: 650; }}
.cl-flag .txt {{ font-size: 0.9rem; }}
.cl-flag .money {{ border-top: 1px solid {LINE}; margin-top: 0.6rem; padding-top: 0.45rem; display: flex; justify-content: space-between; font-size: 0.88rem; }}
.cl-flag .money b {{ font-size: 1.05rem; color: {ACCENT_DARK}; }}
.cl-metric {{ display: flex; justify-content: space-between; align-items: baseline; border-top: 1px solid {LINE}; padding: 0.4rem 0; font-size: 0.9rem; gap: 0.6rem; }}
.cl-metric .v {{ font-weight: 680; text-align: right; white-space: nowrap; }}
.cl-metric .r {{ color: {MUTED}; font-size: 0.78rem; font-weight: 400; display: block; }}
.cl-metric.money .v {{ font-size: 1.15rem; color: {ACCENT_DARK}; }}
.cl-note {{ color: {MUTED}; font-size: 0.82rem; }}
.cl-precedent {{ font-size: 0.82rem; color: {MUTED}; margin: 0.2rem 0; }}
.cl-precedent b {{ color: {INK}; font-weight: 640; }}
.cl-callout {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_MID}; border-radius: 8px; padding: 0.65rem 0.95rem; font-size: 0.95rem; margin: 0.5rem 0; }}
.cl-callout.strong {{ background: {ACCENT}; color: #fff; border-color: {ACCENT}; font-size: 1.02rem; }}
.cl-callout.strong b {{ color: #fff; }}
.cl-warn {{ background: {AMBER_BG}; border: 1px solid {AMBER_LINE}; color: {AMBER_INK}; border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 0.9rem; margin: 0.5rem 0; }}
.cl-empty {{ background: #fff; border: 1px dashed {GREY_BAR}; border-radius: 10px; padding: 1.1rem 1.2rem; color: {MUTED}; font-size: 0.92rem; }}
.cl-empty b {{ color: {INK}; }}
.cl-kpis {{ display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0.2rem 0 0.6rem; }}
.cl-kpi {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; padding: 0.45rem 0.8rem; min-width: 8.5rem; }}
.cl-kpi .k {{ color: {MUTED}; font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em; }}
.cl-kpi .v {{ font-size: 1.35rem; font-weight: 700; }}
.cl-kpi .s {{ color: {MUTED}; font-size: 0.8rem; }}
.cl-kpi.hi {{ border-color: {ACCENT}; box-shadow: inset 0 0 0 1px {ACCENT}; }}
.cl-kpi.hi .v {{ color: {ACCENT}; }}
.cl-ba {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; overflow: hidden; }}
.cl-ba th {{ text-align: left; color: {MUTED}; font-weight: 650; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.06em; padding: 0.5rem 0.8rem; border-bottom: 1px solid {LINE}; background: {BG}; }}
.cl-ba td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid {LINE}; }}
.cl-ba td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.cl-ba td.after {{ font-weight: 700; }}
.cl-ba td.better {{ color: {ACCENT}; font-weight: 700; }}
.cl-ba td.worse {{ color: {MUTED}; }}
.cl-footer {{ margin-top: 2.2rem; padding-top: 0.8rem; border-top: 1px solid {LINE}; color: {MUTED}; font-size: 0.85rem; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; }}
.cl-footer b {{ color: {INK}; }}
div[data-testid="stButton"] > button[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #fff; font-weight: 640; }}
div[data-testid="stButton"] > button[kind="primary"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
div[data-testid="stButton"] > button[kind="primary"]:disabled {{ background: {GREY_BAR}; border-color: {GREY_BAR}; color: #fff; }}
button[data-baseweb="tab"] p {{ font-size: 1.02rem; font-weight: 600; }}
div[data-testid="stExpander"] summary p {{ font-weight: 640; }}
</style>
"""


def esc(text: object) -> str:
    """HTML-escape anything that came from the API or the pasted protocol before it enters markup."""
    return html.escape("" if text is None else str(text))


def is_fixture(source: object) -> bool:
    return str(getattr(source, "value", source)) == "fixture"


def fixture_tag(source: object) -> str:
    return '<span class="cl-tag" title="Placeholder data shipped with the app, not a result">fixture data</span>' if is_fixture(source) else ""


def empty(title: str, body: str = "") -> str:
    """A calm empty-state box (escaped)."""
    return f'<div class="cl-empty"><b>{esc(title)}</b>' + (f"<br>{esc(body)}" if body else "") + "</div>"


FOOTER = (
    '<div class="cl-footer"><span><b>Clinova advises; the protocol author decides.</b> '
    "Virtual patients are synthetic; no real patient data is used.</span>"
    "<span>Probabilities are calibrated estimates with ranges, not verdicts.</span></div>"
)
