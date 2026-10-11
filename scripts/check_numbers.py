"""Check published evaluation claims against the committed JSON reports (stdlib only).

Usage: python scripts/check_numbers.py [--allow-missing] [--self-test]

Tables associate target rows with AUC/CI, Brier, test-count and baseline columns.
For prose that cannot be associated unambiguously, append an ordered source list:
  Accrual failure AUC 0.701. <!-- numbers: metrics.targets.accrual_fail.auc.value -->
Paths use target names instead of list offsets; replay.months uses YYYY-MM keys.
Replay summary fields: retrained_brier, frozen_brier, n_scored, n_unique, n_train.
Other prose-note numbers can use metrics.notes.<index>.numbers.<index>.
Annotations select sources, never supply expected values or suppress mismatches.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

DOCUMENTS = ("README.md", "docs/deck/outline.md", "docs/video/script.md", "docs/submission/devpost.md")
NUMBER = re.compile(r"(?<![\w.])[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)\s*%?(?!\w|\.\d)")
ANNOTATION = re.compile(r"<!--\s*numbers:\s*(.*?)\s*-->")
METRIC = re.compile(
    r"auc|brier|hit[-_ ]rate|ablation|accrual|shortfall|amend|baseline|replay|"
    r"train(?:ing)?\s+(?:rows|trials|set)|test\s+(?:rows|trials|set)|"
    r"(?:rows|trials)\s+(?:in\s+)?(?:train|test)|labeled|positives?|"
    r"n_train|n_test|n_pos|held[- ]out|scored windows|unique trials|feature coverage|parsed rules|"
    r"embedding|features\s*\(", re.I,
)


def numeric_tokens(text: str) -> list[str]:
    """Remove structural numbers before extracting visible quantitative claims."""
    text = re.sub(r"<!--.*?-->", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", lambda m: m[0].split("](")[0][1:], text)
    text = re.sub(r"`[^`]*`", lambda m: m[0][1:-1] if not re.search(r"[/\\=]|NCT\d", m[0]) else "", text)
    text = re.sub(r"https?://\S+|NCT\d+|\bv\d+(?:\.\d+)*\b", "", text, flags=re.I)
    text = re.sub(r"\b(?:localhost|127\.0\.0\.1):\d+\b", "", text, flags=re.I)
    text = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?(?:T[\d:.]+Z)?\b", "", text)
    text = re.sub(r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}\b", "", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", text)
    text = re.sub(r"\bGate\s+\d+\b", "", text, flags=re.I)
    text = re.sub(r"\b95\s*%\s*(?:confidence|CI)\b", "CI", text, flags=re.I)
    text = re.sub(r"^\s*(?:#{1,6}\s+)?\d+[.)]\s+", "", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|seconds?|minutes?|hours?|screens?|slides?)\b", "", text, flags=re.I)
    return [match[0].strip() for match in NUMBER.finditer(text)]


def flatten(value: object, prefix: str, out: dict[str, object]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            flatten(item, f"{prefix}.{key}", out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            name = item.get("target", item.get("month", index)) if isinstance(item, dict) else index
            flatten(item, f"{prefix}.{name}", out)
    else:
        out[prefix] = value


def sources(metrics: dict, replay: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    flatten(metrics, "metrics", out)
    flatten(replay, "replay", out)
    out["replay.n_months"] = len(replay.get("months", []))
    summary = re.search(
        r"Brier ([\d.]+) vs ([\d.]+); ([\d,]+) scored windows, ([\d,]+) unique trials, "
        r"([\d,]+) initial training trials", replay.get("summary", ""),
    )
    if summary:
        for name, value in zip(("retrained_brier", "frozen_brier", "n_scored", "n_unique", "n_train"), summary.groups(), strict=True):
            out[f"replay.summary.{name}"] = Decimal(value.replace(",", ""))
    for index, note in enumerate(metrics.get("notes", [])):
        for offset, token in enumerate(numeric_tokens(note)):
            out[f"metrics.notes.{index}.numbers.{offset}"] = decimal(token)
    return out


def decimal(token: str) -> Decimal:
    return Decimal(token.replace(",", "").replace("%", "").strip()) / (100 if "%" in token else 1)


def matches(token: str, expected: object) -> bool:
    if not isinstance(expected, (int, float, Decimal)) or isinstance(expected, bool):
        return False
    raw = token.replace(",", "").replace("%", "").strip()
    places = len(raw.partition(".")[2])
    scale = Decimal(100 if "%" in token else 1)
    actual = Decimal(raw)
    value = Decimal(str(expected)) * scale
    # Counts must match exactly. Decimal claims permit only their displayed rounding.
    if isinstance(expected, int) and "%" not in token:
        return actual == value
    quantum = Decimal(1).scaleb(-places)
    return abs(actual - value) <= quantum / 2 and actual == value.quantize(quantum, rounding=ROUND_HALF_UP)


def target_name(text: str) -> str | None:
    names = []
    if re.search(r"accrual(?:[_ -]fail(?:ure)?)?", text, re.I):
        names.append("accrual_fail")
    if re.search(r"shortfall", text, re.I):
        names.append("shortfall")
    return names[0] if len(names) == 1 else None


def infer_paths(context: str, count: int, source: dict[str, object]) -> list[str]:
    """Associate a table cell or one simple claim; ambiguous prose fails closed."""
    text = re.sub(r"<!--.*?-->", "", context).lower()
    target = target_name(text)
    baseline = bool(re.search(r"baseline|logistic", text))
    if "replay" in text or re.search(r"\b(?:frozen|retrained)\b", text):
        month = re.search(r"\b\d{4}-\d{2}\b", text)
        if "brier" in text:
            if month:
                arm = "frozen" if "frozen" in text else "retrained"
                paths = [f"replay.months.{month[0]}.{arm}.brier"]
            elif count == 2 and "frozen" in text and "retrained" in text:
                arms = sorted(("retrained", "frozen"), key=text.index)
                paths = [f"replay.summary.{arm}_brier" for arm in arms]
            else:
                arm = "frozen" if "frozen" in text else "retrained"
                paths = [f"replay.summary.{arm}_brier"]
        elif "auc" in text and month:
            arm = "frozen" if "frozen" in text else "retrained"
            paths = [f"replay.months.{month[0]}.{arm}.auc"]
        elif "months" in text or "month replay" in text:
            paths = ["replay.n_months"]
        else:
            paths = []
    elif target:
        prefix = f"metrics.targets.{target}."
        if "auc" in text:
            key = prefix + ("baseline_auc" if baseline else "auc")
            paths = [key + suffix for suffix in (".value", ".ci_low", ".ci_high")[:count]] if count in (1, 3) else []
        elif "brier" in text:
            if count == 2 and baseline and re.search(r"lightgbm|lgbm|model", text):
                paths = [prefix + "brier", prefix + "baseline_brier"]
            else:
                paths = [prefix + ("baseline_brier" if baseline else "brier")]
        elif re.search(r"test|labeled|positive", text):
            if count == 2 and re.search(r"positive|\bpos\b", text):
                paths = [prefix + "n_test", prefix + "n_pos"]
            else:
                paths = [prefix + ("n_pos" if re.search(r"positive|\bpos\b", text) else "n_test")]
        else:
            paths = []
    elif count == 1 and re.search(r"train|test", text) and re.search(r"rows|trials|set", text):
        paths = ["metrics.n_train" if "train" in text else "metrics.n_test"]
    elif re.search(r"hit[-_ ]rate", text):
        paths = ["metrics.hit_rate"]
    elif "ablation" in text:
        paths = ["metrics.ablation"]
    else:
        paths = []
    return paths if len(paths) == count and all(path in source for path in paths) else []


def check_text(text: str, source: dict[str, object], filename: str) -> tuple[list[str], int]:
    errors: list[str] = []
    checked = 0
    table: list[str] = []
    code = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            code = not code
            continue
        if code:
            continue
        if line.startswith("#"):
            table = []
        annotation = ANNOTATION.search(line)
        if "|" in line and line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r"[: -]+", cell) for cell in cells):
                continue
            if not table:
                table = cells
                if numeric_tokens(line) and METRIC.search(line):
                    errors.append(f"{filename}:{line_no}: unexpected numeric claim in table header")
                continue
            contexts = [(cell, f"{cells[0]} {header}") for header, cell in zip(table, cells, strict=False)]
        else:
            table = []
            contexts = [(line, line)]
        # Demo probabilities belong to docs/demo_results, outside this checker's source contract.
        if not annotation and "docs/demo_results" in line:
            continue
        annotated_paths = annotation[1].split() if annotation else []
        if annotation and len(annotated_paths) != len(numeric_tokens(line)):
            errors.append(f"{filename}:{line_no}: annotation must name one source per visible numeric claim")
            continue
        annotation_offset = 0
        for visible, context in contexts:
            tokens = numeric_tokens(visible)
            if not tokens:
                continue
            if not annotation and not METRIC.search(context):
                continue
            inferred = infer_paths(context, len(tokens), source)
            paths = annotated_paths[annotation_offset:annotation_offset + len(tokens)] if annotation else inferred
            annotation_offset += len(tokens)
            location = f"{filename}:{line_no}"
            if not paths or len(paths) != len(tokens):
                errors.append(f"{location}: cannot associate {tokens}; add an ordered <!-- numbers: ... --> annotation")
                continue
            if inferred and annotation and inferred != paths:
                errors.append(f"{location}: annotation conflicts with metric/baseline labels: expected {inferred}, got {paths}")
                continue
            for token, path in zip(tokens, paths, strict=True):
                checked += 1
                if path not in source:
                    errors.append(f"{location}: unknown source {path}")
                elif not matches(token, source[path]):
                    errors.append(f"{location}: {token} != {path} ({source[path]!r})")
    return errors, checked


def self_test() -> None:
    metrics = {"n_train": 1424, "n_test": 360, "hit_rate": None, "ablation": None, "targets": [
        {"target": "accrual_fail", "n_test": 359, "n_pos": 34,
         "auc": {"value": 0.7010859728506788, "ci_low": 0.623474, "ci_high": 0.773902},
         "baseline_auc": {"value": 0.660543, "ci_low": 0.593433, "ci_high": 0.723302},
         "brier": 0.082726, "baseline_brier": 0.084061}]}
    replay = {"months": [{"month": "2020-01", "retrained": {"auc": 0.863636}}], "summary":
              "Brier 0.078241 vs 0.076678; 1736 scored windows, 324 unique trials, 216 initial training trials"}
    source = sources(metrics, replay)
    good = """| Target | Test rows / positives | Model AUC (95% CI) | Baseline AUC (95% CI) | Model Brier | Baseline Brier |
| --- | --- | --- | --- | --- | --- |
| Accrual failure | 359 / 34 | 0.701 (0.623–0.774) | 0.661 (0.593–0.723) | 0.0827 | 0.0841 |
Accrual failure AUC 70.1%.
Replay retrained Brier 0.078241 vs frozen 0.076678.
Replay retrained AUC in 2020-01: 0.864.
Train rows: 1,424.
Hit rate unavailable; simulation ablation not computed; amendment model not trained.
Demo trial NCT03232892 accrual probability 0.123 (docs/demo_results/NCT03232892.json).
1. Open http://localhost:8501 in 20 seconds; see screen 2 on 2026-10-11.
"""
    assert not check_text(good, source, "fixture.md")[0]
    for bad in (good.replace("0.701 (", "0.661 ("), good.replace("0.0827 | 0.0841", "0.0841 | 0.0827"),
                "Accrual failure AUC 0.702.", "Hit rate: 0%.", "Simulation ablation: 0.",
                "Accrual failure AUC -0.701.", "## Accrual failure AUC 0.999",
                "Amendment AUC 0.701.", "Shortfall AUC 0.701.",
                "Accrual failure baseline AUC 0.701. <!-- numbers: metrics.targets.accrual_fail.auc.value -->",
                "Accrual failure model AUC 0.661. <!-- numbers: metrics.targets.accrual_fail.baseline_auc.value -->"):
        assert check_text(bad, source, "tampered.md")[0], bad
    annotated = "Train/test: 1,424 / 360. <!-- numbers: metrics.n_train metrics.n_test -->"
    assert not check_text(annotated, source, "fixture.md")[0]
    assert check_text(annotated.replace("360", "359"), source, "tampered.md")[0]
    assert matches("70.1%", 0.7010859728506788)
    assert matches(".701", 0.7010859728506788)
    assert not matches("70.2%", 0.7010859728506788)
    print("Self-test passed: rounded values, CIs, percentages, scoped baselines, missing metrics and tampering.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-missing", action="store_true", help="Allow later presentation artifacts to be absent during T41; never use for G4.")
    parser.add_argument("--self-test", action="store_true", help="Run offline checker self-tests instead of checking documents.")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    root = Path(__file__).resolve().parents[1]
    try:
        source = sources(*(json.loads((root / name).read_text(encoding="utf-8-sig")) for name in ("docs/metrics.json", "docs/replay.json")))
    except (OSError, ValueError) as exc:
        print(f"Cannot load evaluation sources: {exc}")
        return 1
    errors: list[str] = []
    checked = 0
    for filename in DOCUMENTS:
        path = root / filename
        if not path.is_file():
            if args.allow_missing and filename != "README.md":
                print(f"SKIP absent artifact: {filename}")
            else:
                errors.append(f"Missing expected artifact: {filename}")
            continue
        found, count = check_text(path.read_text(encoding="utf-8-sig"), source, filename)
        errors.extend(found)
        checked += count
    for error in errors:
        print(error)
    print(f"Checked {checked} numeric claims; {len(errors)} mismatches.")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
