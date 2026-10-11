"""CLI (owner: T22): ``python -m clinova.model train [--version v0.x]``, ``python -m clinova.model evaluate``
and ``python -m clinova.model watch`` (retrain + evaluate when simulations or labels grow). No Gemini calls."""

from __future__ import annotations

import argparse
import json
import time
import warnings


def _train(version: str | None, bootstraps: int, use_sim: str) -> dict:
    from clinova.model.features import load_dataset
    from clinova.model.train import save_bundle, train

    df, ref = load_dataset()
    cfg = {"ref": ref, "version": version, "bootstraps": bootstraps,
           "use_sim": {"auto": None, "yes": True, "no": False}[use_sim]}
    bundle = train(df[df["split"] == "train"], cfg)
    path = save_bundle(bundle)
    print(json.dumps({"version": bundle["version"], "path": str(path), "n_train": bundle["n_train"],
                      "skipped": bundle["skipped"], "use_sim": bundle["use_sim"],
                      "features": len(bundle["feature_names"]), "tuning": bundle["tuning"]}, default=str, indent=1))
    return bundle


def _evaluate(version: str | None) -> None:
    from clinova.model.evaluate import evaluate
    from clinova.model.features import load_dataset
    from clinova.model.train import load_bundle

    bundle = load_bundle(version)
    df, _ = load_dataset(ref=bundle["ref"])
    vm = evaluate(bundle, df)
    print(vm.model_dump_json(indent=1, exclude={"targets": {"__all__": {"reliability"}}}))


def _counts() -> tuple[int, int]:
    """(simulated train trials, labeled train trials). Simulated trials are counted in the same population
    ``train.feature_list`` uses to switch simulation features on: first-version runs of train-split trials."""
    from clinova.common.db import get_conn

    with get_conn() as c:
        n_sim = c.execute("SELECT count(DISTINCT s.trial_id) FROM sim_runs s JOIN trials t ON t.nct_id = s.trial_id "
                          "WHERE s.version_no = 0 AND t.split = 'train'").fetchone()[0]
        n_lab = c.execute("SELECT count(*) FROM trials WHERE split = 'train' AND label_accrual_fail IS NOT NULL").fetchone()[0]
    return int(n_sim), int(n_lab)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m clinova.model")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="Build features from the database and train a versioned bundle.")
    t.add_argument("--version", default=None)
    t.add_argument("--bootstraps", type=int, default=30)
    t.add_argument("--use-sim", choices=["auto", "yes", "no"], default="auto")
    e = sub.add_parser("evaluate", help="Write docs/metrics.json and docs/figures/*.png on the test split.")
    e.add_argument("--version", default=None)
    w = sub.add_parser("watch", help="Every --interval s: retrain + evaluate when sims reach --min-sim or labels grow.")
    w.add_argument("--interval", type=int, default=1800)
    w.add_argument("--min-sim", type=int, default=300)
    w.add_argument("--label-growth", type=float, default=0.2, help="Retrain when labeled train trials grow by this share.")
    a = p.parse_args(argv)
    warnings.filterwarnings("ignore", category=UserWarning)
    if a.cmd == "train":
        _train(a.version, a.bootstraps, a.use_sim)
    elif a.cmd == "evaluate":
        _evaluate(a.version)
    else:
        from clinova.model.train import load_bundle

        try:  # resume from the latest bundle instead of retraining at once
            b = load_bundle()
            last_lab, sim_done = b["n_train"]["accrual_fail"]["n"], b["use_sim"]
        except FileNotFoundError:
            last_lab, sim_done = 0, False
        sim_tried = -1  # simulated-train count at the last retrain while simulation features were still off
        while True:
            n_sim, n_lab = _counts()
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} simulated_train={n_sim} labeled_train={n_lab}", flush=True)
            # Keep checking until a bundle actually uses simulation features; retry only when the count grew.
            sim_due = n_sim >= a.min_sim and not sim_done and n_sim > sim_tried
            if sim_due or n_lab >= last_lab * (1 + a.label_growth) + 1:
                bundle = _train(None, 30, "auto")
                _evaluate(bundle.get("version"))  # the bundle just trained, not the pinned served one
                last_lab, sim_done, sim_tried = n_lab, bool(bundle["use_sim"]), n_sim
            time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
