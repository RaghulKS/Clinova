"""Gold-set evaluator: field-level precision and recall of the parser (owner: T12).

Run: ``python -m clinova.parse.evaluate [--out docs/parse_gold_metrics.json]`` (calls Gemini, cached).
A rule is identified by (kind, field). ``strict`` additionally requires the same condition: same
operator and value after normalisation (present == eq true, eq x == in [x], stage ordinal ops expanded;
a gold value of "*" matches any operator and value). Unparseable rules are not predictions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clinova.contracts import FIELD_SPECS, ClinicalField, Operator

GOLD = Path(__file__).resolve().parents[2] / "tests" / "parse" / "gold" / "gold.json"


def _norm(field: str, op: str, value):
    """Canonical comparable form of a condition."""
    spec = FIELD_SPECS[ClinicalField(field)]
    if value == "*":
        return "*"
    if op in ("present", "absent") or (spec.kind == "boolean" and op in ("eq", "ne")):
        v = True if op == "present" else False if op == "absent" else (value is True) == (op == "eq")
        return ("bool", v)
    if spec.kind == "ordinal" and op in ("lt", "le", "gt", "ge", "eq", "ne", "in", "not_in"):
        vals = list(spec.values)
        vs = value if isinstance(value, list) else [value]
        idx = [vals.index(v) for v in vs if v in vals]
        if op in ("lt", "le", "gt", "ge") and idx:
            i = idx[0]
            keep = {"lt": range(i), "le": range(i + 1), "gt": range(i + 1, len(vals)), "ge": range(i, len(vals))}[op]
            return ("set", frozenset(vals[j] for j in keep))
        s = frozenset(vs)
        return ("set", s if op in ("eq", "in") else frozenset(vals) - s)
    if spec.kind == "categorical" and op in ("eq", "in", "ne", "not_in"):
        vs = frozenset(value if isinstance(value, list) else [value])
        vals = frozenset(spec.values)
        return ("set", vs if op in ("eq", "in") else vals - vs)
    if op in ("in", "eq") and isinstance(value, list) and len(value) == 1:
        op, value = "eq", value[0]
    return (op, tuple(value) if isinstance(value, list) else value)


def score(gold: list[dict], pred: list[dict]) -> dict:
    """gold/pred rules are dicts with kind, field, operator, value. Multiset matching per (kind, field)."""
    def keyed(rules):
        out: dict[tuple, list] = {}
        for r in rules:
            out.setdefault((r["kind"], r["field"]), []).append(_norm(r["field"], r["operator"], r["value"]))
        return out

    g, p = keyed(gold), keyed(pred)
    tp = fp = fn = strict_tp = strict_n = 0
    for k in g.keys() | p.keys():
        gl, pl = list(g.get(k, [])), list(p.get(k, []))
        m = min(len(gl), len(pl))
        tp, fp, fn = tp + m, fp + len(pl) - m, fn + len(gl) - m
        rest = list(pl)
        for x in gl:  # strict: each gold rule against any unused prediction
            if x == "*":
                continue
            strict_n += 1
            if x in rest:
                rest.remove(x)
                strict_tp += 1
    prec, rec = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec,
            "strict_value_correct": strict_tp, "strict_value_total": strict_n}


def evaluate(out_path: str | None = None) -> dict:
    from clinova.parse.parser import parse_protocol

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    per, tot = [], {"tp": 0, "fp": 0, "fn": 0, "strict_value_correct": 0, "strict_value_total": 0}
    for g in gold:
        parsed = parse_protocol(g["text"], trial_id=g["nct_id"], version_no=0)
        pred = [
            {"kind": r.kind, "field": r.field.value, "operator": r.operator.value, "value": r.value}
            for r in parsed.rules if r.parse_status == "ok" and r.operator in Operator
        ]
        s = score(g["rules"], pred)
        per.append({"nct_id": g["nct_id"], **s, "n_rules": len(parsed.rules), "n_unparseable": len(parsed.unparseable)})
        for k in tot:
            tot[k] += s[k]
    tot["precision"] = tot["tp"] / (tot["tp"] + tot["fp"]) if tot["tp"] + tot["fp"] else 0.0
    tot["recall"] = tot["tp"] / (tot["tp"] + tot["fn"]) if tot["tp"] + tot["fn"] else 0.0
    tot["strict_value_accuracy"] = tot["strict_value_correct"] / tot["strict_value_total"] if tot["strict_value_total"] else 0.0
    res = {"n_trials": len(gold), "overall": tot, "per_trial": per}
    if out_path:
        Path(out_path).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    r = evaluate(ap.parse_args().out)
    print(json.dumps(r["overall"], indent=1))
