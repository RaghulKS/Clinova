"""Gate 2 diagnostic: does T12 processing status (parsed rules / embedding present) act as an outcome proxy?

Prints, per split, accrual_fail prevalence, mean served p and AUC within parsed / unparsed and embedded /
not-embedded trials for a bundle, plus the AUC of the bare "has parsed rules" indicator.

    uv run python scripts/g2_parse_status_diagnostic.py --version v1.0 [--out docs/g2_parse_status.json]
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

from sklearn.metrics import roc_auc_score

from clinova.model.features import load_dataset
from clinova.model.train import load_bundle, predict_p


def _auc(y, p):
    return round(float(roc_auc_score(y, p)), 3) if y.nunique() > 1 else None


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    b = load_bundle(a.version)
    df, _ = load_dataset(ref=b["ref"])
    emb = next(c for c in df if c.startswith("emb_pc"))
    out = {"bundle_dir": a.version, "model_version": b["version"], "target": "accrual_fail", "splits": {}}
    for split in ("train", "test"):
        d = df[(df.split == split) & df.label_accrual_fail.notna()].copy()
        d["p"] = predict_p(b, "accrual_fail", d)
        y = d.label_accrual_fail.astype(int)
        rows = {"all": {"n": len(d), "pos": int(y.sum()), "auc": _auc(y, d.p)},
                "auc_of_has_parsed_rules_indicator": _auc(y, d.n_rules.notna().astype(int))}
        for name, mask in [("parsed", d.n_rules.notna()), ("unparsed", d.n_rules.isna()),
                           ("embedded", d[emb].notna()), ("not_embedded", d[emb].isna())]:
            dd = d[mask]
            yy = dd.label_accrual_fail.astype(int)
            rows[name] = {"n": len(dd), "pos": int(yy.sum()), "prevalence": round(float(yy.mean()), 3) if len(dd) else None,
                          "mean_p": round(float(dd.p.mean()), 3) if len(dd) else None, "auc": _auc(yy, dd.p)}
        out["splits"][split] = rows
    text = json.dumps(out, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
