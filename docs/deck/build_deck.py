"""Build the Clinova 12-slide deck from the files that produce its numbers.

Every number on a slide, in the speaker notes and in docs/deck/outline.md is read
from a repo file at build time (docs/metrics.json, docs/replay.json,
docs/deck/data/demo.json, docs/gate1_e2e.json, docs/ARCHITECTURE.md section 2,
docs/demo_trial.md). Nothing is typed by hand. A value whose source file is
missing renders as a visible "[TBD: ...]" placeholder and stamps the slide DRAFT.

Usage (from the repo root):
    uv run --with python-pptx --with pymupdf python docs/deck/build_deck.py [--render] [--final]

--render  converts the .pptx to docs/deck/clinova.pdf with LibreOffice headless and
          renders every page to docs/deck/render/slide-XX.png.
--final   exits non-zero if any placeholder is left.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from PIL import Image, ImageFont  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE  # noqa: E402
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN  # noqa: E402
from pptx.oxml.ns import qn  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DECK = ROOT / "docs" / "deck"
IMG = DECK / "img"
RENDER = DECK / "render"
DATA = DECK / "data"

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------
FONT = "Arial"
INK = "1B2A30"
TEAL = "0E5E63"
TEAL_DARK = "0B3C40"
TEAL_MID = "2E8A8F"
TINT = "E9F2F2"
GREY = "5C6A70"
LIGHT = "A9B4B8"
RULE = "D5DDE0"
CORAL = "C8492F"
WHITE = "FFFFFF"

SW, SH = 13.333, 7.5  # inches, 16:9
MX = 0.7  # side margin
TITLE_Y, TITLE_H = 0.95, 1.25
BODY_Y = 2.45

ARIAL_TTF = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_BOLD_TTF = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


# ---------------------------------------------------------------------------
# Facts: every number comes through here
# ---------------------------------------------------------------------------
class Facts:
    """Loads source files once and formats values; missing values become placeholders."""

    def __init__(self) -> None:
        self.missing: list[str] = []
        self.used_sources: set[str] = set()
        self.metrics = self._json("docs/metrics.json", reject_fixture=True)
        self.replay = self._json("docs/replay.json")
        self.detail = self._json("docs/metrics_detail.json")
        self.e2e = self._json("docs/gate1_e2e.json")
        self.arch = (ROOT / "docs/ARCHITECTURE.md").read_text()
        p = ROOT / "docs/demo_trial.md"
        self.demo_md = p.read_text() if p.exists() else ""
        m = re.search(r"\*\*Selected: (NCT\d{8})\*\*", self.demo_md)
        self.demo_nct = m.group(1) if m else None
        self.demo = self._json(f"docs/demo_results/{self.demo_nct}.json") if self.demo_nct else None
        self.demo_opt = self._json(f"docs/demo_results/{self.demo_nct}_optimize.json") if self.demo_nct else None
        self.tasks = sorted(glob.glob(str(ROOT / "orchestration/tasks/*.md")))
        self.repo_url = self._repo_url()

    # -- loading ----------------------------------------------------------
    def _json(self, rel: str, reject_fixture: bool = False):
        p = ROOT / rel
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        if reject_fixture and str(d.get("model_version", "")).lower().startswith("fixture"):
            return None
        return d

    def _repo_url(self) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except Exception:
            return None
        out = re.sub(r"\.git$", "", out)
        out = re.sub(r"^git@github\.com:", "https://github.com/", out)
        return out

    # -- placeholders -----------------------------------------------------
    def ph(self, key: str) -> str:
        self.missing.append(key)
        return f"[TBD: {key}]"

    def src(self, rel: str) -> str:
        self.used_sources.add(rel)
        return rel

    # -- formatters ---------------------------------------------------------
    @staticmethod
    def f2(x: float) -> str:
        return f"{x:.2f}"

    @staticmethod
    def pct(x: float, nd: int = 0) -> str:
        s = f"{100 * x:.{nd}f}%"
        return s

    @staticmethod
    def signed_pct(x: float, nd: int = 0) -> str:
        return f"{'+' if x >= 0 else '-'}{abs(100 * x):.{nd}f}%"

    @staticmethod
    def signed_pts(x: float, nd: int = 0) -> str:
        return f"{'+' if x >= 0 else '-'}{abs(100 * x):.{nd}f} pts"

    @staticmethod
    def dollars(x: float) -> str:
        if x >= 1e6:
            return f"${x / 1e6:.1f}M"
        if x >= 1e3:
            return f"${round(x / 1e3):,.0f}K"
        return f"${x:,.0f}"

    # -- ARCHITECTURE.md section 2 (cited problem figures) ---------------------
    def arch_row(self, name: str) -> tuple[str, str] | None:
        for line in self.arch.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0] == name:
                return cells[1], cells[2]
        return None

    def problem(self) -> dict:
        out: dict = {}
        r = self.arch_row("Protocol amendments")
        m = r and re.search(r"(\d+%) of protocols have a substantial amendment; (\d+%) of those are avoidable", r[0])
        out["amend_pct"], out["avoidable_pct"] = (m.group(1), m.group(2)) if m else (self.ph("amend %"), self.ph("avoidable %"))
        out["amend_src"] = r[1] if r else "?"
        r = self.arch_row("Amendment cost")
        m = r and re.search(r"Median (\$\d+K) per amendment in Phase II, (\$\d+K) in Phase III, plus about (\d+) months", r[0])
        out["cost_p2"], out["cost_p3"], out["delay_months"] = (
            (m.group(1), m.group(2), m.group(3)) if m else (self.ph("cost P2"), self.ph("cost P3"), self.ph("delay"))
        )
        out["cost_src"] = r[1] if r else "?"
        r = self.arch_row("Cancer trial failure")
        m = r and re.search(
            r"([\d.]+%) of ([\d,]+) US cancer trials were terminated; accrual issues were the top specific reason \(([\d.]+%)\)",
            r[0],
        )
        out["term_pct"], out["term_n"], out["accrual_pct"] = (
            (m.group(1), m.group(2), m.group(3)) if m else (self.ph("term %"), self.ph("n trials"), self.ph("accrual %"))
        )
        out["term_src"] = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", r[1]) if r else "?"
        r = self.arch_row("Slow enrollment")
        m = r and re.search(r"(\d+%) of sites enroll no one", r[0])
        out["zero_sites"] = m.group(1) if m else self.ph("zero-enrolling sites")
        out["slow_src"] = r[1] if r else "?"
        self.src("docs/ARCHITECTURE.md")
        return out

    # -- metrics.json ---------------------------------------------------------
    def target(self, name: str) -> dict | None:
        if not self.metrics:
            return None
        for t in self.metrics.get("targets", []):
            if t.get("target") == name:
                return t
        return None

    def auc_line(self, name: str) -> dict:
        t = self.target(name)
        if not t or not t.get("auc"):
            k = f"metrics.json {name} AUC"
            return {"ok": False, "auc": self.ph(k), "ci": "", "base": self.ph(k + " baseline"), "base_ci": "",
                    "n_test": self.ph("n_test"), "n_pos": self.ph("n_pos"), "brier": self.ph("Brier"),
                    "base_brier": self.ph("baseline Brier")}
        self.src("docs/metrics.json")
        a, b = t["auc"], t["baseline_auc"]
        return {
            "ok": True,
            "auc": self.f2(a["value"]),
            "ci": f"{self.f2(a['ci_low'])}–{self.f2(a['ci_high'])}",
            "base": self.f2(b["value"]),
            "base_ci": f"{self.f2(b['ci_low'])}–{self.f2(b['ci_high'])}",
            "n_test": str(t["n_test"]),
            "n_pos": str(t["n_pos"]),
            "brier": f"{t['brier']:.3f}",
            "base_brier": f"{t['baseline_brier']:.3f}",
            "raw": t,
        }

    def hit(self) -> dict:
        h = (self.metrics or {}).get("hit_rate") if self.metrics else None
        if self.metrics is not None and h is None:
            return {"ok": False, "na": True}
        if not h:
            k = "metrics.json hit_rate"
            return {"ok": False, "n": self.ph(k + " n"), "hit": self.ph(k), "rand": self.ph(k + " random"),
                    "marg": self.ph(k + " marginal")}
        self.src("docs/metrics.json")
        return {"ok": True, "n": str(h["n_trials"]), "hit": self.pct(h["hit_rate"]), "rand": self.pct(h["random_baseline"]),
                "marg": self.pct(h["marginal_baseline"]), "raw": h}

    def ablation(self) -> dict:
        a = (self.metrics or {}).get("ablation") if self.metrics else None
        if self.metrics is not None and a is None:
            return {"ok": False, "na": True}
        if not a:
            k = "metrics.json ablation"
            return {"ok": False, "n": self.ph(k + " n"), "diff": self.ph(k + " diff"), "ci": "", "with": self.ph(k),
                    "without": self.ph(k)}
        self.src("docs/metrics.json")
        d = a["diff"]
        sign = "+" if d["value"] >= 0 else "−"
        return {"ok": True, "n": str(a["n_trials"]), "diff": f"{sign}{abs(d['value']):.2f}",
                "ci": f"{d['ci_low']:+.2f} to {d['ci_high']:+.2f}", "with": self.f2(a["auc_with"]),
                "without": self.f2(a["auc_without"]), "raw": a}

    def counts(self) -> dict:
        if not self.metrics:
            return {"n_train": self.ph("metrics.json n_train"), "n_test": self.ph("metrics.json n_test"),
                    "version": self.ph("model_version")}
        self.src("docs/metrics.json")
        return {"n_train": f"{self.metrics['n_train']:,}", "n_test": f"{self.metrics['n_test']:,}",
                "version": str(self.metrics.get("model_version"))}

    # -- replay.json -------------------------------------------------------
    def replay_summary(self) -> dict:
        r = self.replay
        if not r or not r.get("months"):
            k = "replay.json"
            return {"ok": False, "retrained": self.ph(k + " retrained Brier"), "frozen": self.ph(k + " frozen Brier"),
                    "n": self.ph(k + " windows"), "first": self.ph("first month"), "last": self.ph("last month"),
                    "better": None, "placeholder_model": False, "summary": ""}
        self.src("docs/replay.json")
        num_r = num_f = den = 0.0
        for m in r["months"]:
            n = m["n_scored"]
            if n and m["retrained"].get("brier") is not None and m["frozen"].get("brier") is not None:
                num_r += n * m["retrained"]["brier"]
                num_f += n * m["frozen"]["brier"]
                den += n
        br, bf = num_r / den, num_f / den
        return {
            "ok": True,
            "retrained": f"{br:.3f}",
            "frozen": f"{bf:.3f}",
            "n": f"{int(den):,}",
            "first": r["months"][0]["month"],
            "last": r["months"][-1]["month"],
            "better": br < bf,
            "placeholder_model": "placeholder" in r.get("summary", "").lower(),
            "summary": r.get("summary", ""),
            "freeze": str(r.get("freeze_date")),
            "horizon": str(r.get("horizon_months")),
        }

    # -- demo backtest ----------------------------------------------------------
    def demo_trial(self) -> dict:
        """Demo trial facts: docs/demo_trial.md (selection), docs/demo_results/<nct>*.json (Gate 2 cached
        score, flags and fixes)."""
        out: dict = {}
        out["nct"] = self.demo_nct or self.ph("demo NCT")
        m = re.search(r'stop reason "([^"]+)".*?terminated (\d{4}-\d{2}-\d{2})', self.demo_md)
        out["stop_reason"] = m.group(1) if m else self.ph("stop reason")
        out["terminated"] = m.group(2) if m else self.ph("termination date")
        if self.demo_md:
            self.src("docs/demo_trial.md")
        out["res_src"] = f"docs/demo_results/{self.demo_nct}.json"
        out["opt_src"] = f"docs/demo_results/{self.demo_nct}_optimize.json"
        d = self.demo
        if d:
            self.src(out["res_src"])
            out["flags"] = sorted(d.get("flags") or [], key=lambda f: f["rank"]) or None
            out["widened"] = d.get("sponsor_removed_fields") or []
            out["widened_in_top3"] = d.get("sponsor_removed_in_top3_flags") or []
            out["risk"] = {r["target"]: r for r in d.get("scores") or []}
            out["pctl"] = d.get("test_percentiles") or {}
        else:
            out.update(flags=None, widened=[], widened_in_top3=[], risk={}, pctl={})
        o = self.demo_opt
        if o:
            self.src(out["opt_src"])
            out["fixes"] = o.get("fixes") or None
            out["rules"] = {r["rule_id"]: r for r in (o.get("protocol") or {}).get("rules", [])}
            out["n_seeds"] = str(len(o.get("cohort_seeds") or []))
        else:
            out.update(fixes=None, rules={}, n_seeds=self.ph("cohort seeds"))
        return out

    def paired(self, target: str, which: str = "baseline") -> dict | None:
        """Paired bootstrap AUC difference from docs/metrics_detail.json (model minus reference)."""
        t = ((self.detail or {}).get("targets") or {}).get(target) or {}
        if which == "sponsor":
            v = (t.get("sponsor_only") or {}).get("auc_diff_model_minus_sponsor")
        else:
            v = t.get("auc_diff_vs_baseline")
        if v:
            self.src("docs/metrics_detail.json")
        return v

    def detail_target(self, target: str) -> dict:
        return ((self.detail or {}).get("targets") or {}).get(target) or {}


FIELD_WORDS = {
    "prior_platinum": "Prior platinum therapy", "ecog": "ECOG performance status", "histology": "Non-squamous histology",
    "crcl_ml_min": "Creatinine clearance", "bilirubin_x_uln": "Bilirubin", "cardiac_condition": "Cardiac exclusion",
    "hemoglobin": "Hemoglobin", "stage": "Stage", "interstitial_lung_disease": "Interstitial lung disease",
}


SHORT_WORDS = {
    "prior_platinum": "prior-platinum rule", "ecog": "ECOG", "histology": "histology rule", "crcl_ml_min": "CrCl",
    "bilirubin_x_uln": "bilirubin rule", "cardiac_condition": "cardiac exclusion", "hemoglobin": "hemoglobin",
}


def field_words(f: str) -> str:
    return FIELD_WORDS.get(f, f.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Charts (large type, for slides)
# ---------------------------------------------------------------------------
def _mpl_setup() -> None:
    for f in (ARIAL_TTF, ARIAL_BOLD_TTF):
        if os.path.exists(f):
            font_manager.fontManager.addfont(f)
    plt.rcParams.update({
        "font.family": "Arial",
        "font.size": 20,
        "axes.edgecolor": "#" + LIGHT,
        "axes.labelcolor": "#" + INK,
        "xtick.color": "#" + GREY,
        "ytick.color": "#" + INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "savefig.dpi": 200,
        "figure.facecolor": "white",
    })


TARGET_NAMES = {"accrual_fail": "Accrual failure", "shortfall": "Enrollment shortfall", "amend": "Eligibility amendment"}


def chart_auc(F: Facts, path: Path, fixture: bool = False) -> bool:
    """LightGBM vs logistic baseline test AUC with bootstrap 95% CI whiskers, one row per target."""
    m = F.metrics
    fixture = fixture and m is None
    if fixture:
        m = json.loads((ROOT / "tests/fixtures/metrics.json").read_text())
    if not m:
        return False
    rows = [t for t in m["targets"] if t.get("auc") and t.get("n_test", 0) > 0]
    if not rows:
        return False
    fig, ax = plt.subplots(figsize=(8.6, 1.35 + 1.25 * len(rows)))
    for i, t in enumerate(rows):
        y = len(rows) - 1 - i
        for dy, key, col, lab in ((0.17, "auc", TEAL, "Clinova (LightGBM)"), (-0.17, "baseline_auc", LIGHT, "Logistic baseline")):
            a = t[key]
            ax.plot([a["ci_low"], a["ci_high"]], [y + dy, y + dy], color="#" + col, lw=5, solid_capstyle="round",
                    alpha=0.55 if key == "auc" else 0.9)
            ax.plot(a["value"], y + dy, "o", ms=15, color="#" + (TEAL if key == "auc" else GREY),
                    label=lab if i == 0 else None)
            ax.text(a["ci_high"] + 0.012, y + dy, f"{a['value']:.2f}", va="center", fontsize=19,
                    color="#" + (TEAL if key == "auc" else GREY), fontweight="bold" if key == "auc" else "normal")
    ax.axvline(0.5, color="#" + RULE, lw=2, ls="--", zorder=0)
    ax.text(0.505, -0.55, "chance", fontsize=15, color="#" + GREY, va="bottom")
    ax.set_yticks(range(len(rows)))
    def ylab(t):
        g = None if fixture else F.paired(t["target"])
        tag = "not validated" if g is not None and g["value"] <= 0 else f"n={t['n_test']}"
        return f"{TARGET_NAMES.get(t['target'], t['target'])}\n({tag})"
    ax.set_yticklabels([ylab(t) for t in reversed(rows)], fontsize=18)
    lo = min(min(t["auc"]["ci_low"], t["baseline_auc"]["ci_low"]) for t in rows)
    ax.set_xlim(max(0.0, min(0.45, lo - 0.03)), 1.0)
    ax.set_ylim(-0.6, len(rows) - 0.25)
    ax.set_xlabel("Test ROC AUC with bootstrap 95% CI", fontsize=17, color="#" + GREY)
    ax.tick_params(axis="x", labelsize=16)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=15, frameon=False, handletextpad=0.3)
    if fixture:
        _watermark(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def chart_hit(F: Facts, path: Path, fixture: bool = False) -> bool:
    """Hit rate of Clinova's top-3 flags vs three random rules and the three largest marginal exclusions."""
    m = F.metrics
    fixture = fixture and m is None
    if fixture:
        m = json.loads((ROOT / "tests/fixtures/metrics.json").read_text())
    h = (m or {}).get("hit_rate")
    if not h:
        return False
    labels = ["Clinova\ntop-3 flags", "Largest marginal\nexclusion", "Three random\nrules"]
    vals = [h["hit_rate"], h["marginal_baseline"], h["random_baseline"]]
    cols = ["#" + TEAL, "#" + LIGHT, "#" + LIGHT]
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    bars = ax.bar(labels, vals, color=cols, width=0.62)
    for b, v in zip(bars, vals, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{100 * v:.0f}%", ha="center", va="bottom", fontsize=24,
                fontweight="bold", color="#" + (TEAL if b is bars[0] else GREY))
    ax.set_ylim(0, max(1.0, max(vals) + 0.15))
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=15)
    ax.tick_params(axis="x", labelsize=16, length=0)
    ax.set_ylabel(f"Widened rule in top 3 (n={h['n_trials']})", fontsize=16, color="#" + GREY)
    ax.spines["left"].set_visible(False)
    ax.yaxis.grid(True, color="#" + RULE, lw=1)
    ax.set_axisbelow(True)
    if fixture:
        _watermark(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def chart_reliability(F: Facts, path: Path, target: str = "accrual_fail") -> bool:
    """10-bin reliability plot from docs/metrics.json (calibrated LightGBM), dot size = trials in bin."""
    t = F.target(target)
    if not t or not t.get("reliability"):
        return False
    bins = [b for b in t["reliability"] if b.get("mean_pred") is not None and b.get("n")]
    if not bins:
        return False
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    ax.plot([0, 1], [0, 1], ls="--", color="#" + LIGHT, lw=2, zorder=0)
    ax.text(0.98, 0.93, "perfect calibration", ha="right", fontsize=14, color="#" + GREY, rotation=0)
    ax.plot([b["mean_pred"] for b in bins], [b["frac_pos"] for b in bins], color="#" + TEAL, lw=2.5, zorder=1)
    nmax = max(b["n"] for b in bins)
    ax.scatter([b["mean_pred"] for b in bins], [b["frac_pos"] for b in bins],
               s=[60 + 500 * b["n"] / nmax for b in bins], color="#" + TEAL, zorder=2)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=15)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=15)
    ax.set_xlabel("Predicted accrual-failure risk", fontsize=16, color="#" + GREY)
    ax.set_ylabel("Observed failure rate", fontsize=16, color="#" + GREY)
    ax.yaxis.grid(True, color="#" + RULE, lw=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def chart_replay(F: Facts, path: Path, fixture: bool = False) -> bool:
    """Cumulative (pooled) Brier score by month, retrained vs frozen. Lower is better."""
    r = F.replay
    fixture = fixture and r is None
    if fixture:
        r = json.loads((ROOT / "tests/fixtures/replay.json").read_text())
    if not r or not r.get("months"):
        return False
    xs, cr, cf = [], [], []
    nr = nf = den = 0.0
    for i, m in enumerate(r["months"]):
        n = m["n_scored"]
        if n and m["retrained"].get("brier") is not None and m["frozen"].get("brier") is not None:
            nr += n * m["retrained"]["brier"]
            nf += n * m["frozen"]["brier"]
            den += n
        if den:
            xs.append(i)
            cr.append(nr / den)
            cf.append(nf / den)
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.plot(xs, cf, color="#" + GREY, lw=3.5, label="Frozen at " + str(r.get("freeze_date", "")))
    ax.plot(xs, cr, color="#" + TEAL, lw=4.5, label="Retrained monthly")
    span = (max(cr + cf) - min(cr + cf)) or 1.0
    yr, yf = cr[-1], cf[-1]
    if abs(yr - yf) < 0.07 * span:  # keep the two end labels apart
        mid = (yr + yf) / 2
        off = 0.04 * span
        yr, yf = (mid - off, mid + off) if cr[-1] <= cf[-1] else (mid + off, mid - off)
    ax.text(xs[-1] + 0.6, yr, f"{cr[-1]:.3f}", va="center", fontsize=19, color="#" + TEAL, fontweight="bold")
    ax.text(xs[-1] + 0.6, yf, f"{cf[-1]:.3f}", va="center", fontsize=19, color="#" + GREY)
    months = [m["month"] for m in r["months"]]
    ticks = [i for i, mo in enumerate(months) if mo.endswith("-01")]
    ax.set_xticks(ticks)
    ax.set_xticklabels([months[i][:4] for i in ticks], fontsize=16)
    ax.set_xlim(-0.5, len(months) + 4)
    ax.tick_params(axis="y", labelsize=15)
    ax.set_ylabel("Cumulative Brier (lower is better)", fontsize=16, color="#" + GREY)
    ax.yaxis.grid(True, color="#" + RULE, lw=1)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=15, frameon=False)
    if fixture:
        _watermark(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def flag_share(f: dict) -> float | None:
    """Exclusion share stated in a flag's own 'why' text (written by model.predict.flag_criteria)."""
    m = re.search(r"excludes (\d+(?:\.\d+)?)% of virtual", f.get("why", ""))
    return float(m.group(1)) / 100 if m else None


def chart_flags(F: Facts, demo: dict, path: Path) -> bool:
    """Clinova's top 3 flags on the demo trial with each rule's exclusion share; the rule the sponsor
    later removed is coral."""
    flags = demo.get("flags") or []
    rows = []
    for f in flags[:3]:
        share = flag_share(f)
        if share is None:
            return False
        rows.append((f"#{f['rank']}  {field_words(f['field'])} ({f['rule_id']})", share,
                     f["field"] in set(demo.get("widened") or [])))
    if not rows:
        return False
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    y = list(range(len(rows)))[::-1]
    ax.barh(y, [r[1] for r in rows], color=["#" + (CORAL if r[2] else TEAL) for r in rows], height=0.6)
    for yi, r in zip(y, rows, strict=True):
        ax.text(r[1] + 0.012, yi, f"{100 * r[1]:.0f}%", va="center", fontsize=20, color="#" + INK, fontweight="bold")
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows], fontsize=17)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(r[1] for r in rows) * 1.25)
    ax.xaxis.set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def chart_auc_diff(F: Facts, path: Path) -> bool:
    """Paired bootstrap AUC differences (model minus reference) with 95% CI, accrual failure."""
    rows = []
    a = F.paired("accrual_fail")
    b = F.paired("accrual_fail", "sponsor")
    if a:
        rows.append(("vs logistic\nbaseline", a))
    if b:
        rows.append(("vs sponsor\ntype alone", b))
    if not rows:
        return False
    fig, ax = plt.subplots(figsize=(7.0, 3.9))
    for i, (_lab, d) in enumerate(rows):
        y = len(rows) - 1 - i
        ax.plot([d["ci_low"], d["ci_high"]], [y, y], color="#" + TEAL, lw=6, alpha=0.55, solid_capstyle="round")
        ax.plot(d["value"], y, "o", ms=16, color="#" + TEAL)
        ax.text(d["value"], y + 0.22, f"{d['value']:+.2f}", ha="center", fontsize=19, color="#" + TEAL,
                fontweight="bold")
        ax.text(d["ci_high"] + 0.008, y, f"[{d['ci_low']:+.2f}, {d['ci_high']:+.2f}]", va="center", fontsize=15,
                color="#" + GREY)
    ax.axvline(0, color="#" + CORAL, lw=2, ls="--")
    ax.text(0.004, -0.55, "no gain", fontsize=14, color="#" + CORAL)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=17)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", labelsize=15)
    lo = min(r[1]["ci_low"] for r in rows)
    hi = max(r[1]["ci_high"] for r in rows)
    ax.set_xlim(min(-0.05, lo - 0.02), hi + 0.13)
    ax.set_ylim(-0.6, len(rows) - 0.3)
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Accrual-failure AUC gain, paired 95% CI", fontsize=16, color="#" + GREY)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return True


def _watermark(ax) -> None:
    ax.text(0.5, 0.5, "ILLUSTRATIVE FIXTURE\nNOT RESULTS", transform=ax.transAxes, ha="center", va="center",
            fontsize=30, color="#" + CORAL, alpha=0.35, fontweight="bold", rotation=12)


def crop_screen(src: Path, dst: Path, aspect: float, y_frac: float = 0.0, x_frac: float = 0.0,
                width_frac: float = 1.0) -> bool:
    """Crop a screenshot to the frame aspect (w/h), starting at y_frac of the height."""
    if not src.exists():
        return False
    im = Image.open(src).convert("RGB")
    W, H = im.size
    w = int(W * width_frac)
    x0 = int((W - w) * x_frac)
    h = int(w / aspect)
    if h > H:
        h = H
        w = int(h * aspect)
    y0 = min(int(H * y_frac), H - h)
    im.crop((x0, y0, x0 + w, y0 + h)).save(dst, optimize=True)
    return True


# ---------------------------------------------------------------------------
# Slide spec
# ---------------------------------------------------------------------------
@dataclass
class SlideSpec:
    kicker: str
    title: str
    lines: list[str]
    visual: str
    notes: str
    criterion: str
    sources: list[str]
    draw: Callable | None = None
    dark: bool = False
    missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def text(slide, x, y, w, h, runs, size=18, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         line_spacing=1.1, space_after=0, name=None):
    """Add a text box. `runs` is a str, or a list of paragraphs; a paragraph is a str or list of (text, opts)."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_after = Pt(space_after)
        items = para if isinstance(para, list) else [(para, {})]
        for t, opts in items:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = FONT
            f.size = Pt(opts.get("size", size))
            f.bold = opts.get("bold", bold)
            f.italic = opts.get("italic", False)
            f.color.rgb = rgb(opts.get("color", color))
    return tb


def box(slide, x, y, w, h, fill=TINT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, line=None, radius=0.08, name=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        s.name = name
    style = s._element.find(qn("p:style"))
    if style is not None:
        s._element.remove(style)
    if fill:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(1.25)
    else:
        s.line.fill.background()
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    return s


def circle_num(slide, x, y, d, label, fill=TEAL, color=WHITE, size=20):
    c = box(slide, x, y, d, d, fill=fill, shape=MSO_SHAPE.OVAL)
    tf = c.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = True
    r.font.color.rgb = rgb(color)
    return c


def arrow(slide, x1, y1, x2, y2, color=LIGHT, width=2.0):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
    ln.append(tail)
    return c


def picture(slide, path: Path, x, y, w=None, h=None, border=True):
    if not path.exists():
        return None
    if border:
        box(slide, x - 0.04, y - 0.04, (w or 0) + 0.08, (h or 0) + 0.08, fill=RULE, radius=0.02)
    return slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w) if w else None, Inches(h) if h else None)


def picture_fit(slide, path: Path, x, y, w, h):
    """Place an image inside (x, y, w, h), keeping its aspect, centered."""
    if not path.exists():
        return None
    iw, ih = Image.open(path).size
    s = min(w / iw, h / ih)
    pw, ph = iw * s, ih * s
    return slide.shapes.add_picture(str(path), Inches(x + (w - pw) / 2), Inches(y + (h - ph) / 2), Inches(pw), Inches(ph))


def missing_panel(slide, x, y, w, h, msg):
    box(slide, x, y, w, h, fill=None, line=CORAL, radius=0.04)
    text(slide, x + 0.3, y, w - 0.6, h, msg, size=18, color=CORAL, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def set_bullet(p, size, color=TEAL_MID):
    """Hanging-indent bullet on a paragraph (real PowerPoint bullet, not a typed character)."""
    pPr = p._p.get_or_add_pPr()
    indent = int(Pt(size * 1.1))
    pPr.set("marL", str(indent))
    pPr.set("indent", str(-indent))
    for tag in ("a:buClr", "a:buSzPct", "a:buFont", "a:buChar", "a:buNone"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    buClr = pPr.makeelement(qn("a:buClr"), {})
    clr = buClr.makeelement(qn("a:srgbClr"), {"val": color})
    buClr.append(clr)
    buSz = pPr.makeelement(qn("a:buSzPct"), {"val": "100000"})
    buFont = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
    buChar = pPr.makeelement(qn("a:buChar"), {"char": "\u2022"})
    for el in (buClr, buSz, buFont, buChar):
        pPr.append(el)


def body_lines(slide, x, y, w, lines, size=19, color=INK, dark=False, h=None):
    """Up to 3 short lines, each its own bulleted paragraph."""
    tb = text(slide, x, y, w, h or (0.6 * len(lines) + 0.6), lines, size=size, color=color, line_spacing=1.1,
              space_after=size * 0.75)
    for p in tb.text_frame.paragraphs:
        set_bullet(p, size, color="7FC4C7" if dark else TEAL_MID)
    return tb


# ---------------------------------------------------------------------------
# The 12 slides (order: ARCHITECTURE.md section 12)
# ---------------------------------------------------------------------------
def build_specs(F: Facts, fixture_charts: bool) -> list[SlideSpec]:
    P = F.problem()
    C = F.counts()
    A = F.auc_line("accrual_fail")
    S = F.auc_line("shortfall")
    H = F.hit()
    AB = F.ablation()
    R = F.replay_summary()
    D = F.demo_trial()
    n_tasks = str(len(F.tasks))
    repo = F.repo_url or F.ph("repo URL")
    specs: list[SlideSpec] = []

    # Charts
    IMG.mkdir(parents=True, exist_ok=True)
    has_auc = chart_auc(F, IMG / "auc_vs_baseline.png", fixture=fixture_charts)
    has_hit = chart_hit(F, IMG / "hit_rate.png", fixture=fixture_charts)
    has_rel = chart_reliability(F, IMG / "reliability.png")
    has_replay = chart_replay(F, IMG / "replay_brier.png", fixture=fixture_charts)
    has_flags = chart_flags(F, D, IMG / "demo_flags.png")
    has_diff = chart_auc_diff(F, IMG / "auc_gain.png")
    screens = ROOT / "docs" / "screens"
    crop_screen(screens / "03-funnel-risk.png", IMG / "screen_results.png", aspect=16 / 9, y_frac=0.06, x_frac=0.5, width_frac=0.94)
    crop_screen(screens / "03-funnel-risk.png", IMG / "screen_title.png", aspect=1.6, y_frac=0.1, x_frac=0.09, width_frac=0.55)

    # 1 Title ------------------------------------------------------------------
    def d1(s):
        text(s, MX, 1.65, 6.6, 1.2, "Clinova", size=66, bold=True, color=WHITE)
        text(s, MX, 2.95, 6.4, 1.3, "Test-fly your NSCLC trial on thousands of virtual patients before enrolling a real one",
             size=24, color=WHITE, line_spacing=1.15)
        text(s, MX, 4.6, 6.3, 1.0,
             "Every prediction is graded against how real ClinicalTrials.gov trials turned out",
             size=18, color="BFDCDD", line_spacing=1.15)
        text(s, MX, 6.35, 7.0, 0.4, repo, size=15, color="BFDCDD")
        img = IMG / "screen_title.png"
        if img.exists():
            w = 5.2
            h = w / 1.6
            box(s, 7.45 - 0.05, 1.75 - 0.05, w + 0.1, h + 0.1, fill="FFFFFF", radius=0.03)
            s.shapes.add_picture(str(img), Inches(7.45), Inches(1.75), Inches(w), Inches(h))
    specs.append(SlideSpec(
        kicker="", title="Clinova test-flies an NSCLC protocol on virtual patients and grades itself on real trials",
        dark=True,
        lines=["Test-fly your NSCLC trial on thousands of virtual patients before enrolling a real one",
               "Every prediction is graded against how real ClinicalTrials.gov trials turned out", repo],
        visual="Dark teal title slide; funnel panel of the app on the demo trial (docs/screens/03-funnel-risk.png, cropped) at right.",
        notes=(
            "Clinova is a flight simulator for non-small cell lung cancer trial protocols. A study designer pastes a draft "
            "protocol or an NCT ID, and Clinova shows where enrollment will stall and where patients will drop out, before "
            "the protocol is final. The difference from other tools is proof: every prediction is graded against how real "
            "ClinicalTrials.gov trials turned out, on trials the model never saw. Clinova advises the author; it never "
            "approves a protocol. Pitch wording: docs/ARCHITECTURE.md section 1."
        ),
        criterion="Utility and Relevance", sources=["docs/ARCHITECTURE.md"], draw=d1))

    # 2 Problem -------------------------------------------------------------------
    def d2(s):
        tiles = [
            (P["amend_pct"], "of protocols need a substantial amendment", f"{P['avoidable_pct']} of those are avoidable"),
            (P["cost_p3"], "median cost of one Phase III amendment", f"{P['cost_p2']} in Phase II, plus about {P['delay_months']} months"),
            (P["accrual_pct"], "of cancer-trial terminations: low accrual, the top reason",
             f"{P['term_pct']} of {P['term_n']} US cancer trials terminated"),
        ]
        w, gap = 3.75, 0.3
        for i, (big, lab, sub) in enumerate(tiles):
            x = MX + i * (w + gap)
            box(s, x, BODY_Y + 0.1, w, 3.3, fill=TINT)
            text(s, x + 0.35, BODY_Y + 0.45, w - 0.7, 1.1, big, size=58, bold=True, color=TEAL)
            text(s, x + 0.35, BODY_Y + 1.6, w - 0.7, 0.9, lab, size=18, color=INK, line_spacing=1.1)
            text(s, x + 0.35, BODY_Y + 2.65, w - 0.7, 0.6, sub, size=14, color=GREY, line_spacing=1.1)
        text(s, MX, 6.15, SW - 2 * MX, 0.4,
             "Clinova pulls the two levers the FDA cost study names: looser enrollment rules and simpler protocols",
             size=17, color=INK)
    specs.append(SlideSpec(
        kicker="The problem", title="Most trial delays are written into the protocol before the first patient is screened",
        lines=[f"{P['amend_pct']} of protocols need a substantial amendment; median {P['cost_p3']} each in Phase III",
               f"Low accrual is the top reason cancer trials stop ({P['accrual_pct']} of terminations)",
               "Clinova pulls the two levers the FDA cost study names: looser rules, simpler protocols"],
        visual="Three stat tiles: amendment rate, Phase III amendment cost, accrual share of terminations.",
        notes=(
            f"Delays are designed in. {P['amend_pct']} of protocols get a substantial amendment and {P['avoidable_pct']} "
            f"of those were avoidable ({P['amend_src']}). Each costs a median {P['cost_p2']} in Phase II and {P['cost_p3']} "
            f"in Phase III plus about {P['delay_months']} months ({P['cost_src']}). Of {P['term_n']} US cancer trials, "
            f"{P['term_pct']} were terminated, and accrual was the top specific reason at {P['accrual_pct']} "
            f"({P['term_src']}). All figures: docs/ARCHITECTURE.md section 2. The FDA-commissioned cost study names looser "
            "enrollment rules and simpler protocols."
        ),
        criterion="Utility and Relevance", sources=["docs/ARCHITECTURE.md"], draw=d2))

    # 3 Why now ---------------------------------------------------------------------
    def d3(s):
        cards = [
            ("History is public", "ClinicalTrials.gov keeps every registered version, so a widened rule is a checkable label"),
            ("LLMs read eligibility", "Gemini turns free-text criteria into rules that code can check on virtual patients"),
            ("Design is the AI gap", "Tufts CSDD 2024: full AI adoption is lowest in trial design and planning"),
        ]
        w, gap = 3.75, 0.3
        for i, (head, sub) in enumerate(cards):
            x = MX + i * (w + gap)
            box(s, x, BODY_Y + 0.1, w, 3.55, fill=TINT)
            circle_num(s, x + 0.35, BODY_Y + 0.45, 0.75, str(i + 1))
            text(s, x + 0.35, BODY_Y + 1.5, w - 0.7, 0.9, head, size=22, bold=True, color=INK, line_spacing=1.05)
            text(s, x + 0.35, BODY_Y + 2.25, w - 0.7, 1.3, sub, size=16, color=GREY, line_spacing=1.15)
    specs.append(SlideSpec(
        kicker="Why now", title="Public version history and LLM parsing make protocol risk checkable today",
        lines=["ClinicalTrials.gov keeps every registered version: amendments become public labels",
               "LLMs turn free-text eligibility into code-checkable rules",
               "Trial design is where AI adoption lags most (Tufts CSDD 2024)"],
        visual="Three numbered cards.",
        notes=(
            "Three things changed. First, ClinicalTrials.gov keeps every registered version of a protocol, so when a sponsor "
            "widens a rule, that edit is public. Starter Sheet 01 assumes amendment data is private; we use version history "
            "as a public proxy instead, so every claim can be checked. Second, an LLM can turn free-text eligibility into "
            "rules code can check. Third, the Tufts CSDD 2024 benchmark finds full AI adoption lowest in trial design and "
            "planning. Source: docs/ARCHITECTURE.md sections 2 and 3."
        ),
        criterion="Utility and Relevance", sources=["docs/ARCHITECTURE.md"], draw=d3))

    # 4 What Clinova does ------------------------------------------------------------
    def d4(s):
        steps = [
            ("Read", "Draft text or NCT ID to coded rules and visits"),
            ("Simulate", "Virtual NSCLC cohort, screened in code; persona agents decide"),
            ("Calibrate", "LightGBM on real trials, explained by SHAP"),
            ("Optimize", "Test edits; see trade-offs with ranges"),
            ("Learn", "Log every prediction, score it when the trial ends"),
        ]
        n = len(steps)
        w = (SW - 2 * MX - (n - 1) * 0.3) / n
        y = BODY_Y + 0.35
        for i, (head, sub) in enumerate(steps):
            x = MX + i * (w + 0.3)
            box(s, x, y, w, 3.2, fill=TINT)
            circle_num(s, x + 0.3, y + 0.35, 0.7, str(i + 1))
            text(s, x + 0.3, y + 1.3, w - 0.5, 0.5, head, size=22, bold=True)
            text(s, x + 0.3, y + 1.9, w - 0.5, 1.2, sub, size=15, color=GREY, line_spacing=1.15)
            if i < n - 1:
                arrow(s, x + w + 0.03, y + 0.7, x + w + 0.27, y + 0.7, color=TEAL_MID, width=2.5)
    specs.append(SlideSpec(
        kicker="What Clinova does", title="Clinova test-flies a protocol in five steps, from draft text to a graded risk",
        lines=["Read, simulate, calibrate, optimize, learn",
               "The author sees where the funnel stalls, how likely failure is, and three fixes",
               "Every score is logged and graded when the real trial ends"],
        visual="Five-step process flow with numbered circles and arrows.",
        notes=(
            "Five steps. Read: Gemini turns eligibility rules and the visit schedule into code-checkable logic. Simulate: a "
            "virtual NSCLC cohort is screened in code, then persona agents decide whether to enroll and when they would drop "
            "out. Calibrate: LightGBM trained on real ClinicalTrials.gov trials gives calibrated risk, explained by SHAP. "
            "Optimize: test edits such as relaxing a criterion or cutting a visit, with ranges across cohort seeds. Learn: every "
            "prediction is logged and scored when the trial ends. Source: docs/ARCHITECTURE.md section 1."
        ),
        criterion="Utility and Relevance", sources=["docs/ARCHITECTURE.md"], draw=d4))

    # 5 Backtest ---------------------------------------------------------------------
    flags = D.get("flags")
    widened = D.get("widened") or []
    hit_flag = None
    for f in (flags or [])[:3]:
        if f["field"] in widened and hit_flag is None:
            hit_flag = f
    wid_names = ", ".join(field_words(w).lower() for w in widened) if widened else F.ph("sponsor-removed rule")
    by_share = bool(flags) and all(abs(f.get("delta_p", 0.0)) < 1e-12 for f in flags[:3])
    acc = (D.get("risk") or {}).get("accrual_fail")
    acc_pctl = ((D.get("pctl") or {}).get("accrual_fail") or {}).get("all_test")
    if flags is None:
        flag_line = f"Top 3 flags: {F.ph('demo flags')}"
        bt_title = f"Backtest: Clinova reads {D['nct']}'s first registered protocol"
    elif hit_flag:
        share = flag_share(hit_flag)
        flag_line = (f"Flag #{hit_flag['rank']}: {field_words(hit_flag['field']).lower()} ({hit_flag['rule_id']}), "
                     f"the rule the sponsor later removed" + (f"; alone it excludes {F.pct(share)}" if share else ""))
        bt_title = (f"Given only its first protocol, Clinova's flag #{hit_flag['rank']} was the rule the sponsor "
                    "later removed")
    else:
        flag_line = f"The rule the sponsor later removed ({wid_names}) was not in our top 3 flags"
        bt_title = f"Backtest on {D['nct']}: our top 3 flags missed the rule the sponsor later removed"
    method_line = ("Flags rank rules by how many virtual patients each excludes; the risk model does not read criteria"
                   if by_share else "Flags rank rules by how much relaxing each one lowers predicted risk")
    if acc:
        risk_line = (f"Honest miss: predicted accrual-failure risk {F.pct(acc['p'])} (range {F.pct(acc['ci_low'])} to "
                     f"{F.pct(acc['ci_high'])})" + (f", the {F.pct(acc_pctl)[:-1]}th percentile of test trials"
                                                     if acc_pctl is not None else ""))
        if acc_pctl is not None and acc_pctl >= 0.8:
            risk_line = risk_line.replace("Honest miss: p", "P")
    else:
        risk_line = f"Predicted accrual-failure risk: {F.ph('demo risk')}"
    bt_lines = [flag_line, method_line, risk_line]

    def d5(s):
        if has_flags:
            picture_fit(s, IMG / "demo_flags.png", MX, BODY_Y + 0.35, 6.3, 3.0)
            text(s, MX, BODY_Y, 6.3, 0.4, "Top 3 flags: share of virtual patients each rule excludes on its own",
                 size=14, color=GREY)
            text(s, MX, BODY_Y + 3.5, 6.3, 0.4, "Coral: the rule the sponsor later removed", size=14, color=CORAL)
        else:
            missing_panel(s, MX, BODY_Y, 6.3, 3.6, "[TBD: demo flags]")
        x = 7.45
        text(s, x, BODY_Y - 0.05, SW - x - MX, 0.5, f"{D['nct']}", size=24, bold=True, color=TEAL)
        text(s, x, BODY_Y + 0.5, SW - x - MX, 0.4, f"Terminated {D['terminated']}: “{D['stop_reason']}”", size=16,
             color=GREY)
        body_lines(s, x, BODY_Y + 1.15, SW - x - MX, bt_lines, size=16, h=3.2)
    specs.append(SlideSpec(
        kicker="Backtest on a real terminated trial", title=bt_title, lines=bt_lines,
        visual="Bar chart of Clinova's top 3 flags with each rule's exclusion share (docs/deck/img/demo_flags.png), the "
               "sponsor-removed rule in coral; trial facts at right.",
        notes=(
            f"{D['nct']} is a test-split trial terminated on {D['terminated']}, stop reason \"{D['stop_reason']}\" "
            f"(docs/demo_trial.md). Clinova saw only its first registered protocol. {flag_line}. "
            + ("The model predicts no risk change for any flag, so this is an exclusion-share ranking. " if by_share else "")
            + (f"{risk_line}: the model misses this trial. " if acc and (acc_pctl or 0) < 0.8 else "")
            + "The removal is inferred from first versus current text, not a dated version history. "
            f"Source: {D['res_src']}."
        ),
        criterion="Utility and Relevance", sources=["docs/demo_trial.md", D["res_src"]], draw=d5))

    # 6 Live demo screenshot ------------------------------------------------------------
    def d6(s):
        img = IMG / "screen_results.png"
        if img.exists():
            w = 7.7
            h = w * 9 / 16
            picture(s, img, MX, BODY_Y, w, h)
        else:
            missing_panel(s, MX, BODY_Y, 7.7, 4.33, "[TBD: docs/screens results screenshot]")
        x = MX + 7.7 + 0.5
        body_lines(s, x, BODY_Y + 0.1, SW - x - MX, h=4.0, lines=[
            "Funnel: who is excluded by which rule, who enrolls, who completes",
            "Risk: probability with a range, its rank among test trials and plain-word drivers",
            "Labels say “not validated” or “no model yet” where the evidence is thin",
        ], size=18)
    specs.append(SlideSpec(
        kicker="Live demo", title="The app shows where enrollment stalls and how likely trouble is, in plain words",
        lines=["Funnel: who is excluded by which rule, who enrolls, who completes",
               "Risk: probability with a range, its rank among test trials and plain-word drivers",
               "Labels say “not validated” or “no model yet” where the evidence is thin"],
        visual="App screenshot of the demo trial: funnel and risk (docs/screens/03-funnel-risk.png).",
        notes=(
            "This is the running app on the demo trial. Left, the funnel: virtual patients screened against each criterion "
            "in protocol order, with Clinova's top flag highlighted. Right, risk: each target's probability with a range, "
            "its rank among held-out test trials and the drivers in plain words. Weak evidence is labelled \"not "
            "validated\" or \"no model yet\". Below sit flags, the reveal and three fixes; tabs hold validation, replay "
            "and the audit log. Screenshot: docs/screens/03-funnel-risk.png."
        ),
        criterion="Design and Packaging", sources=["docs/screens/03-funnel-risk.png"], draw=d6))

    # 7 Optimizer -------------------------------------------------------------------
    fixes = D.get("fixes")
    nice = {"fastest": "Fastest enrollment", "lowest_dropout": "Fewest dropouts", "balanced": "Balanced", "other": "Alternative"}
    no_dollars = bool(fixes) and all(fx.get("dollar_value", 0) == 0 for fx in fixes[:3])

    def fix_rows(fx):
        r = fx.get("ranges", {})

        def rng(key, fmt):
            v = r.get(key)
            return f"{fmt(v['low'])} to {fmt(v['high'])}" if v else ""
        rows = [
            ("Enrollment speed", F.signed_pct(fx["delta_enroll"]), rng("delta_enroll", F.signed_pct)),
            ("Dropout", F.signed_pts(fx["delta_dropout"], 1), rng("delta_dropout", lambda v: F.signed_pts(v, 1))),
            ("Diversity index", f"{fx['delta_diversity']:+.3f}", ""),
        ]
        if not no_dollars:
            rows.append(("Expected value", F.dollars(fx["dollar_value"]), ""))
        return rows

    def edit_words(e):
        rule = D.get("rules", {}).get(e["target_id"], {})
        return _edit_words(e, rule)

    def d7(s):
        w, gap = 3.75, 0.3
        if not fixes:
            missing_panel(s, MX, BODY_Y + 0.1, SW - 2 * MX, 3.6, "[TBD: demo fixes]")
            return
        for i, fx in enumerate(fixes[:3]):
            x = MX + i * (w + gap)
            box(s, x, BODY_Y, w, 3.7, fill=TINT)
            text(s, x + 0.3, BODY_Y + 0.25, w - 0.6, 0.45, nice.get(fx["label"], fx["label"]), size=21, bold=True,
                 color=TEAL)
            edits = [e for e in (edit_words(e) for e in fx["edits"])]
            text(s, x + 0.3, BODY_Y + 0.8, w - 0.6, 1.3, edits, size=14, color=GREY, line_spacing=1.05)
            yy = BODY_Y + 1.9
            for lab, val, rg in fix_rows(fx):
                text(s, x + 0.3, yy, 1.8, 0.35, lab, size=15, color=INK)
                text(s, x + 1.9, yy, w - 2.2, 0.35, val, size=17, bold=True, color=INK, align=PP_ALIGN.RIGHT)
                if rg:
                    text(s, x + 1.4, yy + 0.32, w - 1.7, 0.3, rg, size=12, color=GREY, align=PP_ALIGN.RIGHT)
                yy += 0.62
        foot = (f"Ranges: {D['n_seeds']} cohort seeds. "
                + ("No dollar value yet (no amendment labels). " if no_dollars else "")
                + "Authors lock rules that define the trial's population")
        text(s, MX, 6.4, SW - 2 * MX, 0.4, foot, size=14, color=GREY)
    if fixes:
        fx_lines = [f"{nice.get(fx['label'], fx['label'])}: enrollment speed {F.signed_pct(fx['delta_enroll'])}, "
                    f"dropout {F.signed_pts(fx['delta_dropout'], 1)}"
                    + ("" if no_dollars else f", {F.dollars(fx['dollar_value'])} expected value")
                    for fx in fixes[:3]]
    else:
        fx_lines = [F.ph("fix 1"), F.ph("fix 2"), F.ph("fix 3")]
    sponsor_hit = [fx for fx in (fixes or [])[:3]
                   if any(D.get("rules", {}).get(e["target_id"], {}).get("field") in widened for e in fx["edits"])]
    specs.append(SlideSpec(
        kicker="Optimizer",
        title="The optimizer proposes three trade-offs with ranges; the author decides which, if any",
        lines=fx_lines,
        visual="Three fix cards (fastest, fewest dropouts, balanced) with edits, deltas and ranges.",
        notes=(
            f"For {D['nct']} the optimizer, Optuna NSGA-II over at most three edits, returns three representative fixes. "
            + (". ".join(fx_lines) + ". " if fixes else f"{F.ph('fix numbers')}. ")
            + f"Ranges span {D['n_seeds']} cohort seeds. "
            + ("We show no dollars: the amendment model has no positive labels yet. " if no_dollars else "")
            + (f"{len(sponsor_hit)} of the three include a rule the sponsor later removed. " if fixes else "")
            + f"Source: {D['opt_src']}."
        ),
        criterion="Utility and Relevance", sources=[D["opt_src"]], draw=d7))

    # 8 Architecture ------------------------------------------------------------------
    def d8(s):
        y0 = BODY_Y + 0.15
        bw, bh = 2.05, 0.95
        cols = [MX, MX + 2.45, MX + 4.9, MX + 7.35, MX + 9.8]
        nodes = {
            "ctgov": (cols[0], y0, "ClinicalTrials.gov\n+ version history"),
            "ingest": (cols[1], y0, "Ingest + labels"),
            "parse": (cols[2], y0, "Criteria parser\n(Gemini)"),
            "screen": (cols[3], y0, "Cohort + code\nscreen"),
            "agents": (cols[4], y0, "Persona agents\n(Gemini)"),
            "db": (cols[1], y0 + 1.55, "Tiger Data\nPostgres + pgvector"),
            "model": (cols[3], y0 + 1.55, "LightGBM + SHAP\ncalibrated risk"),
            "opt": (cols[4], y0 + 1.55, "Optimizer\nNSGA-II"),
            "loop": (cols[2], y0 + 1.55, "Learning loop\nledger + replay"),
            "app": (cols[4], y0 + 3.1, "FastAPI +\nStreamlit app"),
        }
        for k, (x, y, lab) in nodes.items():
            fill = TEAL if k in ("model", "db") else TINT
            col = WHITE if k in ("model", "db") else INK
            b = box(s, x, y, bw, bh, fill=fill, radius=0.12)
            tf = b.text_frame
            tf.margin_left = tf.margin_right = Inches(0.08)
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf.word_wrap = True
            for j, part in enumerate(lab.split("\n")):
                p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                p.alignment = PP_ALIGN.CENTER
                r = p.add_run()
                r.text = part
                r.font.name = FONT
                r.font.size = Pt(14 if j == 0 else 12)
                r.font.bold = j == 0
                r.font.color.rgb = rgb(col)

        def right(a, b):
            ax, ay, _ = nodes[a]
            bx, by, _ = nodes[b]
            arrow(s, ax + bw + 0.04, ay + bh / 2, bx - 0.04, by + bh / 2, color=TEAL_MID)

        def down(a, b):
            ax, ay, _ = nodes[a]
            bx, by, _ = nodes[b]
            arrow(s, ax + bw / 2, ay + bh + 0.04, bx + bw / 2, by - 0.04, color=TEAL_MID)

        def left(a, b):
            ax, ay, _ = nodes[a]
            bx, by, _ = nodes[b]
            arrow(s, ax - 0.04, ay + bh / 2, bx + bw + 0.04, by + bh / 2, color=TEAL_MID)
        right("ctgov", "ingest")
        right("ingest", "parse")
        right("parse", "screen")
        right("screen", "agents")
        down("ingest", "db")
        down("screen", "model")
        down("agents", "opt")
        left("opt", "model")
        left("model", "loop")
        left("loop", "db")
        down("opt", "app")
        text(s, MX, y0 + 3.25, 8.4, 0.9,
             [[("Built by AI agents: ", {"bold": True}),
               (f"four orchestrators across two model families ran {n_tasks} task prompts, "
                "coordinated by git tags and reviewing each other's work", {})]],
             size=16, color=INK, line_spacing=1.15)
    specs.append(SlideSpec(
        kicker="Architecture", title="Small, tested modules around one Tiger Data store, built entirely by AI agents",
        lines=["Parser, cohort screen, persona agents, LightGBM, optimizer and learning loop",
               "Public data only; MIT license; one-command Docker Compose with cached AI outputs",
               f"Four orchestrators, two model families, {n_tasks} task prompts"],
        visual="Block diagram of the pipeline (ingest, parse, screen, agents, model, optimizer, loop, app) around Tiger Data.",
        notes=(
            "ClinicalTrials.gov records and their version history are ingested and labelled into Tiger Data, which is "
            "Postgres with TimescaleDB and pgvector. Gemini parses criteria; a code screen and persona agents simulate the "
            "cohort; LightGBM with SHAP scores risk; the optimizer re-scores every candidate edit in milliseconds; the "
            "learning loop logs and grades every prediction. Judges run it with one Docker Compose command and cached AI "
            f"outputs. Four AI orchestrators across two model families ran {n_tasks} task prompts "
            "(count of orchestration/tasks/*.md)."
        ),
        criterion="Readiness", sources=["docs/ARCHITECTURE.md", "orchestration/tasks/"], draw=d8))

    # 9 Validation ----------------------------------------------------------------------
    gain = F.paired("accrual_fail")
    gain_sp = F.paired("accrual_fail", "sponsor")
    dt = F.detail_target("accrual_fail")
    beats = bool(gain) and gain["ci_low"] > 0

    def sgn(v):
        return f"{'+' if v >= 0 else '−'}{abs(v):.2f}"
    if A["ok"] and gain and not beats:
        val_title = (f"On {A['n_test']} unseen trials, accrual-failure AUC is {A['auc']}: above chance, not yet shown "
                     "to beat a simple baseline")
    elif A["ok"]:
        val_title = f"On {A['n_test']} unseen trials, accrual-failure AUC is {A['auc']} vs {A['base']} for a simple baseline"
    else:
        val_title = f"On unseen trials, Clinova's accrual-failure AUC is {A['auc']} vs {A['base']} for a baseline"
    if H["ok"]:
        hit_line = (f"Hit rate: widened rule in our top 3 for {H['hit']} of {H['n']} trials "
                    f"(random {H['rand']}, largest exclusion {H['marg']})")
    elif H.get("na"):
        hit_line = "Hit rate not computable: no test trial has an amendment label yet"
    else:
        hit_line = f"Hit rate: {H['hit']} (random {H['rand']}, largest exclusion {H['marg']})"
    if AB["ok"]:
        abl_line = f"Simulation features: AUC {AB['diff']} (95% CI {AB['ci']}) on {AB['n']} simulated trials"
    elif AB.get("na"):
        abl_line = "Simulation ablation not computable: too few simulated trials"
    else:
        abl_line = f"Simulation ablation: {AB['diff']}"
    auc_line = f"Accrual failure: AUC {A['auc']} (95% CI {A['ci']}) vs logistic baseline {A['base']} ({A['base_ci']})"
    gain_line = (f"Paired AUC gain {sgn(gain['value'])} (95% CI {sgn(gain['ci_low'])} to {sgn(gain['ci_high'])})"
                 + (": the interval includes zero" if not beats else "")) if gain else F.ph("paired AUC gain")
    const = dt.get("brier_constant_train_prevalence")
    brier_line = (f"Brier {A['brier']} vs {const:.3f} for predicting the base rate" if const is not None
                  else f"Brier {A['brier']} vs baseline {A['base_brier']}")
    if H.get("na") and AB.get("na"):
        val_lines = [auc_line, gain_line, brier_line + "; hit rate and simulation benefit not computable yet"]
    elif H.get("na"):
        val_lines = [auc_line, gain_line, abl_line]
    else:
        val_lines = [auc_line, hit_line, abl_line]
    right_chart = ("hit_rate.png" if has_hit else "auc_gain.png" if has_diff else "reliability.png" if has_rel else None)

    def d9(s):
        if has_auc:
            picture_fit(s, IMG / "auc_vs_baseline.png", MX, BODY_Y - 0.15, 6.5, 3.1)
        else:
            missing_panel(s, MX, BODY_Y, 6.5, 3.4, "[TBD: AUC chart from docs/metrics.json]")
        if right_chart:
            picture_fit(s, IMG / right_chart, 7.45, BODY_Y - 0.15, SW - 7.45 - MX, 3.1)
        else:
            missing_panel(s, 7.45, BODY_Y, SW - 7.45 - MX, 3.4, "[TBD: hit-rate chart from docs/metrics.json]")
        body_lines(s, MX, BODY_Y + 3.05, SW - 2 * MX, val_lines, size=16, h=1.55)
    sl = F.detail_target("shortfall")
    sl_gain = (sl.get("auc_diff_vs_baseline") or {}).get("value")
    sl_words = (f"Shortfall AUC is {S['auc']} vs {S['base']}" + (", so it is not validated. " if sl_gain is not None
                                                                  and sl_gain <= 0 else ". "))
    specs.append(SlideSpec(
        kicker="Validation on trials the model never saw", title=val_title,
        lines=val_lines,
        visual="Left: test AUC vs logistic baseline per target with bootstrap 95% CI whiskers "
               "(docs/deck/img/auc_vs_baseline.png). Right: "
               + {"hit_rate.png": "hit rate vs random and largest-marginal-exclusion baselines (docs/deck/img/hit_rate.png).",
                  "auc_gain.png": "paired AUC gain over the logistic baseline and over sponsor type alone, with 95% CIs "
                                  "(docs/deck/img/auc_gain.png); shown because the hit rate is not computable.",
                  "reliability.png": "reliability plot (docs/deck/img/reliability.png).",
                  None: "missing."}[right_chart],
        notes=(
            f"Train: trials starting before 2018 ({C['n_train']}); test: 2018 or later ({C['n_test']}). Accrual-failure "
            f"AUC is {A['auc']} against {A['base']} for a logistic baseline. "
            + (f"The paired gain is {sgn(gain['value'])}, interval {sgn(gain['ci_low'])} to {sgn(gain['ci_high'])}"
               + (f"; over sponsor type alone {sgn(gain_sp['value'])}" if gain_sp else "") + ". " if gain else "")
            + ("So it ranks failures above chance but is not shown to beat simple baselines. " if gain and not beats else "")
            + sl_words + f"{hit_line}. {abl_line}. Model {C['version']}: docs/metrics.json, docs/metrics_detail.json."
        ),
        criterion="Readiness", sources=["docs/metrics.json", "docs/metrics_detail.json"], draw=d9))

    # 10 Learning loop --------------------------------------------------------------------
    if R["ok"] and R["better"] and float(R["retrained"]) > 0.95 * float(R["frozen"]):
        loop_title = (f"Retraining as outcomes arrive gave a slightly lower Brier score: {R['retrained']} vs "
                      f"{R['frozen']} frozen")
    elif R["ok"] and R["better"]:
        loop_title = f"Retraining as outcomes arrive lowered the Brier score from {R['frozen']} to {R['retrained']}"
    elif R["ok"]:
        loop_title = (f"In a 2020–2023 replay, monthly retraining did not beat the frozen model (Brier "
                      f"{R['retrained']} vs {R['frozen']})")
    else:
        loop_title = f"The learning loop grades every prediction; retrained Brier {R['retrained']} vs frozen {R['frozen']}"
    loop_lines = [
        f"Replay {R['first']} to {R['last']}: score each month, then learn its outcomes",
        f"Pooled Brier over {R['n']} overlapping windows: retrained {R['retrained']}, frozen {R['frozen']}",
        "Every live prediction is logged in Tiger Data and scored when the trial ends",
    ]
    if R["ok"] and R["first"][:4] != "2020" or R["ok"] and R["last"][:4] != "2023":
        loop_title = loop_title.replace("In a 2020–2023 replay, m", "M")

    def d10(s):
        if has_replay:
            picture_fit(s, IMG / "replay_brier.png", MX, BODY_Y - 0.05, 7.6, 3.95)
        else:
            missing_panel(s, MX, BODY_Y, 7.6, 3.9, "[TBD: replay chart from docs/replay.json]")
        x = MX + 7.6 + 0.45
        body_lines(s, x, BODY_Y + 0.15, SW - x - MX, loop_lines, size=17, h=3.8)
        if R.get("placeholder_model"):
            text(s, x, 6.05, SW - x - MX, 0.6, "Provisional: replay ran with a placeholder model", size=13, color=CORAL)
    specs.append(SlideSpec(
        kicker="Learning loop", title=loop_title, lines=loop_lines,
        visual="Cumulative Brier by month, retrained vs frozen (docs/deck/img/replay_brier.png, from docs/replay.json).",
        notes=(
            f"The replay freezes a model on {R.get('freeze') or F.ph('freeze date')}, then steps month by month from "
            f"{R['first']} to {R['last']}: it scores trials whose outcome arrives within {R.get('horizon', '?')} months, "
            "then adds that month's outcomes and retrains. The frozen arm never retrains. Pooled over "
            f"{R['n']} scored windows the retrained Brier is {R['retrained']} against {R['frozen']} frozen. Windows overlap, "
            "so this is descriptive, not a significance test. Source: docs/replay.json."
        ),
        criterion="Readiness", sources=["docs/replay.json"], draw=d10))

    # 11 Limitations and responsible use ---------------------------------------------------
    lim = []
    if gain and not beats:
        lim.append(("Not shown to beat simple baselines",
                    "Accrual AUC is above chance, but its paired gain over a logistic baseline includes zero"))
    if H.get("na"):
        lim.append(("Amendment labels are missing",
                    "Version histories are not loaded yet, so hit rate and amendment risk are unmeasured"))
    else:
        lim.append(("Registry edits are a proxy",
                    "A widened rule on ClinicalTrials.gov is public evidence of an amendment, not the same thing"))
    lim.append(("Personas are synthetic",
                "Clinical traits come from published distributions"
                + ("; too few simulated trials to test their benefit" if AB.get("na") else ", labelled as assumptions")))
    lim.append(("Scores are correlational",
                "They rank protocols against similar trials; they do not show that a rule caused a failure"))
    if len(lim) < 4:
        lim.insert(1, ("Stop reasons are free text",
                       "Some terminations are business decisions; labels were spot-checked by a second model"))
    lim = lim[:4]

    def d11(s):
        w, h = (SW - 2 * MX - 0.3) / 2, 1.5
        for i, (head, sub) in enumerate(lim):
            x = MX + (i % 2) * (w + 0.3)
            y = BODY_Y + 0.05 + (i // 2) * (h + 0.25)
            box(s, x, y, w, h, fill=TINT)
            text(s, x + 0.35, y + 0.25, w - 0.7, 0.45, head, size=19, bold=True)
            text(s, x + 0.35, y + 0.72, w - 0.7, 0.7, sub, size=15, color=GREY, line_spacing=1.1)
        y = BODY_Y + 0.05 + 2 * (h + 0.25)
        box(s, MX, y, SW - 2 * MX, 0.8, fill=TEAL)
        text(s, MX + 0.35, y, SW - 2 * MX - 0.7, 0.8,
             "Clinova advises; the author decides. No real patient data. Every decision is logged.",
             size=17, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    specs.append(SlideSpec(
        kicker="Limitations and responsible use", title="We show what the evidence can and cannot support",
        lines=[f"{lim[0][0]}; {lim[1][0].lower()}", f"{lim[2][0]}; {lim[3][0].lower()}",
               "Clinova advises; the protocol author decides. No real patient data"],
        visual="2x2 grid of limitation cards; teal banner with the responsible-use statement.",
        notes=(
            "Four limits. " + " ".join(f"{t}." for _h, t in lim)
            + " Clinova uses no real patient data, advises the protocol author, logs every accept and reject, and never "
            "approves a protocol. Sources: docs/gate2_report.md, docs/metrics.json notes."
        ),
        criterion="Readiness", sources=["docs/gate2_report.md", "docs/metrics.json"], draw=d11))

    # 12 Roadmap and repo -----------------------------------------------------------------
    def d12(s):
        items = ["Lymphoma, ophthalmology and immunology", "Site-level enrollment simulation",
                 "Persona model tuned on expert feedback", "Structured protocol input (ICH M11)",
                 "Full GxP validation package"]
        y = BODY_Y + 0.05
        for i, it in enumerate(items):
            circle_num(s, MX, y + i * 0.68, 0.48, str(i + 1), fill="7FC4C7", color=TEAL_DARK, size=15)
            text(s, MX + 0.75, y + i * 0.68, 6.3, 0.48, it, size=19, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
        box(s, 7.75, BODY_Y + 0.05, SW - 7.75 - MX, 2.9, fill="134E52", radius=0.06)
        text(s, 8.1, BODY_Y + 0.35, SW - 8.1 - MX - 0.3, 0.4, "Run it yourself", size=17, color="BFDCDD")
        text(s, 8.1, BODY_Y + 0.85, SW - 8.1 - MX - 0.3, 0.9, repo.replace("https://", ""), size=21, bold=True, color=WHITE,
             line_spacing=1.1)
        text(s, 8.1, BODY_Y + 1.65, SW - 8.1 - MX - 0.3, 1.0,
             "MIT license. docker compose up, then open the app; demo mode needs no API keys.",
             size=15, color="BFDCDD", line_spacing=1.15)
    specs.append(SlideSpec(
        kicker="Roadmap", title="Next: more indications, site-level simulation and structured protocol input", dark=True,
        lines=["Other indications, site-level simulation, ICH M11 input, GxP package",
               "Persona model tuned on expert feedback", repo],
        visual="Dark slide: numbered roadmap list at left; repo link card at right.",
        notes=(
            "Next steps: expand beyond NSCLC to lymphoma, ophthalmology and immunology; simulate enrollment site by site; "
            "tune the persona model on expert feedback; accept structured protocols in ICH M11 format; and build a full GxP "
            f"validation package. Everything is open source under MIT at {repo}. One docker compose command starts the "
            "database, API and app, and demo mode runs on cached AI outputs with no keys. Roadmap: docs/ARCHITECTURE.md "
            "section 12."
        ),
        criterion="Design and Packaging", sources=["docs/ARCHITECTURE.md"], draw=d12))

    for i, sp in enumerate(specs):
        sp.missing = [m for m in F.missing]  # snapshot (deck-wide)
    return specs


OPS = {"le": "≤", "lt": "<", "ge": "≥", "gt": ">", "eq": "=", "ne": "≠"}


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _edit_words(e: dict, rule: dict | None = None) -> str:
    k, t = e["kind"], e["target_id"]
    rule = rule or {}
    name = SHORT_WORDS.get(rule.get("field"), field_words(rule["field"]) if rule.get("field") else t)
    if k == "relax_rule":
        new_v, new_op = e.get("new_value"), e.get("new_operator")
        old_op, old_v = rule.get("operator"), rule.get("value")
        if isinstance(new_v, list):
            return f"Widen {name} ({t})"
        if new_op and old_op and new_op != old_op and {new_op[0], old_op[0]} == {"l", "g"}:
            return f"Lift {name} limit ({t})"
        op = OPS.get(new_op or old_op or "", new_op or old_op or "")
        unit = f" {rule['unit']}" if rule.get("unit") and rule["unit"] not in ("score",) else ""
        if old_v is not None and not isinstance(old_v, list):
            return f"{name} ({t}): {op} {_num(old_v)} to {op} {_num(new_v)}{unit}"
        return f"{name} ({t}): {op} {_num(new_v)}{unit}"
    if k == "drop_rule":
        return f"Drop {name} ({t})"
    if k == "cut_visit":
        return f"cut visit {t}"
    if k == "merge_visits":
        return f"merge visit {t} with the next"
    if k == "make_remote":
        return f"make visit {t} remote"
    return f"{k} {t}"


# ---------------------------------------------------------------------------
# Build pptx
# ---------------------------------------------------------------------------
def slide_chrome(s, sp: SlideSpec, idx: int, total: int, draft: bool):
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(TEAL_DARK if sp.dark else WHITE)
    if idx > 1:
        if sp.kicker:
            text(s, MX, 0.5, 9.0, 0.35, sp.kicker.upper(), size=13, bold=True, color="7FC4C7" if sp.dark else TEAL_MID)
        text(s, MX, TITLE_Y, SW - 2 * MX, TITLE_H, sp.title, size=30, bold=True, color=WHITE if sp.dark else INK,
             line_spacing=1.05, anchor=MSO_ANCHOR.TOP, name="Title")
    foot_col = "7FC4C7" if sp.dark else LIGHT
    text(s, SW - MX - 1.0, SH - 0.5, 1.0, 0.3, f"{idx} / {total}", size=11, color=foot_col, align=PP_ALIGN.RIGHT)
    if idx > 1:
        text(s, MX, SH - 0.5, 9.5, 0.3, "Sources: " + ", ".join(sp.sources), size=10, color=foot_col)
    if draft:
        b = box(s, SW - MX - 3.3, 0.42, 3.3, 0.42, fill=CORAL, radius=0.3)
        tf = b.text_frame
        tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = "DRAFT: placeholder numbers"
        r.font.name = FONT
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.color.rgb = rgb(WHITE)


def slide_has_placeholder(s) -> bool:
    for sh in s.shapes:
        if sh.has_text_frame and "[TBD:" in sh.text_frame.text:
            return True
    return False


def build_pptx(specs: list[SlideSpec], out: Path, deck_has_missing: bool) -> None:
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    blank = prs.slide_layouts[6]
    for i, sp in enumerate(specs, start=1):
        s = prs.slides.add_slide(blank)
        if sp.draw:
            # draw content first so we can see placeholders, then chrome
            sp.draw(s)
        notes_has_ph = "[TBD:" in sp.notes or "[TBD:" in sp.title or any("[TBD:" in ln for ln in sp.lines)
        slide_chrome(s, sp, i, len(specs), draft=slide_has_placeholder(s) or notes_has_ph)
        s.notes_slide.notes_text_frame.text = sp.notes
    prs.core_properties.title = "Clinova"
    prs.core_properties.author = "Clinova"
    prs.save(out)


# ---------------------------------------------------------------------------
# Text-fit check (approximate, Arial metrics)
# ---------------------------------------------------------------------------
def fit_report(pptx_path: Path) -> list[str]:
    prs = Presentation(pptx_path)
    problems = []
    for si, s in enumerate(prs.slides, start=1):
        for sh in s.shapes:
            if not sh.has_text_frame or not sh.text_frame.text.strip():
                continue
            tf = sh.text_frame
            box_w = Emu(sh.width).inches - (Emu(tf.margin_left or 0).inches + Emu(tf.margin_right or 0).inches)
            box_h = Emu(sh.height).inches
            total_h = 0.0
            for p in tf.paragraphs:
                runs = p.runs
                if not runs:
                    continue
                size = max((r.font.size.pt if r.font.size else 18) for r in runs)
                bold = any(r.font.bold for r in runs)
                font = ImageFont.truetype(ARIAL_BOLD_TTF if bold else ARIAL_TTF, int(size * 10))
                txt = "".join(r.text for r in runs)
                lines = _wrap_count(txt, font, box_w * 72 * 10)
                ls = p.line_spacing if isinstance(p.line_spacing, float) else 1.0
                total_h += lines * size * 1.17 * ls / 72 + (p.space_after.pt / 72 if p.space_after else 0)
            if total_h > box_h + 0.08 and sh.shape_type != 1:  # autoshapes (1) hold labels, checked visually
                problems.append(f"slide {si}: text may overflow ({total_h:.2f}in > {box_h:.2f}in): {tf.text[:60]!r}")
    return problems


def _wrap_count(txt: str, font, width_px: float) -> int:
    n = 0
    for chunk in txt.split("\n"):
        words = chunk.split(" ")
        line = ""
        n += 1
        for w in words:
            trial = (line + " " + w).strip()
            if font.getlength(trial) <= width_px:
                line = trial
            else:
                n += 1
                line = w
    return n


# ---------------------------------------------------------------------------
# Outline
# ---------------------------------------------------------------------------
_A, _D, _R = "metrics.targets.accrual_fail.", "detail.targets.accrual_fail.", "replay.summary."
_EXT = "external: docs/ARCHITECTURE.md"
# scripts/check_numbers.py sources for outline lines, keyed by (slide, "title" | "notes" | bullet index).
OUTLINE_SOURCES = {
    (2, 0): _EXT, (2, 1): _EXT, (2, "notes"): _EXT, (3, "notes"): _EXT,
    (5, 2): "numbers: demo.scores.accrual_fail.p demo.scores.accrual_fail.ci_low demo.scores.accrual_fail.ci_high",
    (9, "title"): f"numbers: {_A}n_test {_A}auc.value",
    (9, 0): f"numbers: {_A}auc.value {_A}auc.ci_low {_A}auc.ci_high "
            f"{_A}baseline_auc.value {_A}baseline_auc.ci_low {_A}baseline_auc.ci_high",
    (9, 1): f"numbers: {_D}auc_diff_vs_baseline.value {_D}auc_diff_vs_baseline.ci_low {_D}auc_diff_vs_baseline.ci_high",
    (9, 2): f"numbers: {_A}brier {_D}brier_constant_train_prevalence",
    (9, "notes"): f"numbers: metrics.split_year metrics.n_train metrics.split_year metrics.n_test {_A}auc.value "
                  f"{_A}baseline_auc.value {_D}auc_diff_vs_baseline.value {_D}auc_diff_vs_baseline.ci_low "
                  f"{_D}auc_diff_vs_baseline.ci_high {_D}sponsor_only.auc_diff_model_minus_sponsor.value "
                  "metrics.targets.shortfall.auc.value metrics.targets.shortfall.baseline_auc.value",
    (10, "title"): f"numbers: {_R}retrained_brier {_R}frozen_brier",
    (10, 1): f"numbers: {_R}n_scored {_R}retrained_brier {_R}frozen_brier",
    (10, "notes"): f"numbers: replay.horizon_months {_R}n_scored {_R}retrained_brier {_R}frozen_brier",
}


def write_outline(specs: list[SlideSpec], F: Facts, path: Path) -> list[str]:
    warns = []

    def src(i: int, key: object) -> str:
        return f" <!-- {OUTLINE_SOURCES[(i, key)]} -->" if (i, key) in OUTLINE_SOURCES else ""

    out = ["# Clinova deck outline", "",
           "Generated by `docs/deck/build_deck.py` from the files named in each slide's notes. Do not edit by hand: "
           "every number here is filled in by the script. `[TBD: ...]` marks a value whose source file did not exist "
           "when the script ran.", ""]
    for i, sp in enumerate(specs, start=1):
        wc = len(re.findall(r"\S+", sp.notes))
        if not 60 <= wc <= 90:
            warns.append(f"slide {i}: speaker notes have {wc} words (want 60-90)")
        out += [f"## {i}. {sp.title}{src(i, 'title')}", ""]
        if sp.kicker:
            out += [f"*{sp.kicker}*", ""]
        out += [f"- {ln}{src(i, k)}" for k, ln in enumerate(sp.lines)] + [""]
        out += [f"**Visual:** {sp.visual}", "", f"**Speaker notes ({wc} words):** {sp.notes}{src(i, 'notes')}", "",
                f"**Sources:** {', '.join(sp.sources)}", ""]
    out += ["## Judging criteria map", "", "| Slide | Title | Criterion it serves |", "| --- | --- | --- |"]
    for i, sp in enumerate(specs, start=1):
        out.append(f"| {i} | {sp.title} | {sp.criterion} |")
    out.append("")
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return warns


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def find_soffice() -> str | None:
    for c in (shutil.which("soffice"), shutil.which("libreoffice"),
              os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if c and os.path.exists(c):
            return c
    return None


def render(pptx: Path) -> list[Path]:
    import fitz  # pymupdf

    so = find_soffice()
    if not so:
        print("LibreOffice not found: skipping PDF and PNG renders", file=sys.stderr)
        return []
    subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", str(DECK), str(pptx)], check=True,
                   capture_output=True, timeout=300)
    pdf = DECK / (pptx.stem + ".pdf")
    RENDER.mkdir(exist_ok=True)
    for old in RENDER.glob("slide-*.png"):
        old.unlink()
    outs = []
    doc = fitz.open(pdf)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=120)
        p = RENDER / f"slide-{i:02d}.png"
        pix.save(p)
        outs.append(p)
    return outs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--final", action="store_true", help="fail if any placeholder remains")
    ap.add_argument("--fixture-charts", action="store_true",
                    help="layout preview only: draw charts from tests/fixtures with an ILLUSTRATIVE watermark")
    args = ap.parse_args()
    _mpl_setup()
    F = Facts()
    specs = build_specs(F, fixture_charts=args.fixture_charts)
    pptx = DECK / "clinova.pptx"
    build_pptx(specs, pptx, bool(F.missing))
    warns = write_outline(specs, F, DECK / "outline.md")
    warns += fit_report(pptx)
    for w in warns:
        print("WARN", w)
    missing = sorted(set(F.missing))
    print(f"placeholders: {len(missing)}")
    for m in missing:
        print("  TBD", m)
    print("sources used:", ", ".join(sorted(F.used_sources)))
    if args.render:
        outs = render(pptx)
        print(f"rendered {len(outs)} slides to {RENDER}")
    if args.final and (missing or args.fixture_charts):
        print("FINAL BUILD FAILED: placeholders or fixture charts remain", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
