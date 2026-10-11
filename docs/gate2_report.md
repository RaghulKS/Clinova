# Gate 2 report: model v1.0 frozen

The integrator wrote this at build hour 6.2. Every number comes from a committed file a script wrote. The file is named next to each number.

**Snapshot:** `docs/metrics.json`, `metrics_detail.json`, `predictions.csv` and `model_manifest.json` are from 2026-10-11T04:44:32Z. The bundle was trained at 04:32:35Z. T11 ingestion was still running, so later re-runs will move.

## Headline (copy these, with their sentences)

| | LightGBM v1.0 | Logistic baseline | Other reference |
| --- | --- | --- | --- |
| Accrual failure, test AUC (359 trials, 34 failures) | **0.701** [0.623, 0.774] | 0.661 [0.593, 0.723] | sponsor type only: 0.669 |
| Paired AUC difference (bootstrap 95% CI) | | +0.041 [-0.036, 0.116] vs baseline | +0.033 [-0.031, 0.089] vs sponsor type |
| Accrual failure, Brier | 0.0827 | 0.0841 | constant at train prevalence: 0.0858; sponsor only: 0.0826 |
| Shortfall, test AUC (353 trials, 192 positive) | 0.598 [0.538, 0.655] | 0.605 [0.554, 0.661] | sponsor type only: 0.574 |
| Shortfall, paired AUC difference vs baseline | -0.007 [-0.050, 0.032] | | |
| Shortfall, Brier | 0.2518 | 0.2483 | constant at train prevalence: 0.2572 |
| Amendment | no model: 0 positive of 22 labeled train trials | | |
| Hit rate (sponsor-widened rule in top 3 flags) | not computable: 0 amended test trials | | |
| Simulation ablation | not computable: 16 simulated trials (8 train, 7 test) | | |
| Replay, pooled Brier (updated vs frozen) | 0.0782 vs 0.0767 | | |

Sources: `metrics.json`, `metrics_detail.json`, `replay.json`.

**One honest sentence each:**
- **AUC:** on 359 unseen trials that started in 2018 or later, v1.0 ranks accrual failures above chance (AUC 0.70). It is not shown to beat a logistic baseline or sponsor type alone: both paired intervals include zero.
- **Brier:** probabilities are compressed (served test p never exceeds 0.218). The Brier score is about the same as predicting the base rate for every trial (0.0827 vs 0.0858).
- **Shortfall:** not validated. It is no better than the baseline, and its Brier is worse than the test base rate's. Show it as "not validated" or hide it.
- **Hit rate:** not computed. CT.gov version histories are not loaded (`trial_versions` has 0 rows), so no test trial has an amendment label.
- **Ablation:** not computed. Only 16 trials have simulations (the Gemini daily quota stopped T24), so we cannot say whether simulation helps.
- **Replay:** over 48 retrospective months (2020–2023), monthly retraining did not beat the frozen model. There were 1,736 overlapping windows but only 324 unique trials and 28 unique failures.
- **Least censored years:** on 2018–2019 starts alone, accrual AUC is 0.604 (143 trials, 12 failures) vs 0.651 for the baseline (`metrics_detail.json` `by_start_period`).

## Why v1.0 uses first-registration features only (a gate decision)

The first v1.0 candidate used every feature: parsed rules, screen results on a virtual cohort, and embedding components. It scored accrual AUC 0.867 [0.792, 0.931] vs baseline 0.735 (`docs/g2_discarded_allfeatures/metrics.json`). It was discarded before any number was published, for a reason visible on train data.
- **Processing order tracks the outcome.** Only trials T12 has already parsed or embedded have rule and embedding features, and T12 worked through terminated and accrual-labeled trials first.
- **Train shows it.** Accrual failures are 62.6% of the 99 parsed train trials vs 5.8% of the 1,284 unparsed ones (`docs/g2_discarded_allfeatures/parse_status.json`). "Has parsed rules" alone has train AUC 0.713.
- **The model learned the proxy.** The candidate served a mean p of 0.634 to parsed test trials, whose real prevalence is 0.325. Every protocol a user pastes is parsed, so every deployed score would have been inflated.

v1.0 keeps only the features computable from the first registered version's JSON for every trial: design, operations, visit estimate and eligibility-text length (`clinova/model/train.py` `FIRST_VERSION_FEATURES`). Its mean served p is now 0.088 for parsed test trials and 0.087 for unparsed ones (`docs/g2_parse_status.json`), so the proxy is gone.

What this costs: the risk model no longer reads the criteria themselves. Flags therefore rank rules by how many virtual patients each one excludes, not by a modeled risk change, and every flag's `why` text says so. `--feature-set all` restores the full set once every trial is parsed and embedded.

## Merged
- T24, T22, T23, T21, T25 and T26 were merged into main with `--no-ff`, in that order, with no conflicts.
- T16 is `cut:`, so it was not merged.
- All six were `partial` except T26 (`ok`). The suite passed after the merges; the API test fix is described below.

## Contract changes (each file now has a "G2 status" section)
- **T21:**
  - audit view added to `db/schema.sql`: applied.
  - loop swapped to the T22 trainer: applied.
- **T22:**
  - month-only start dates: applied, with a relabel (below).
  - `MODELS_DIR` setting: applied, plus `MODEL_VERSION=v1.0` so a stray retrain cannot replace the served model.
- **T23:**
  - `score_samples`: applied.
  - API DB preflight: applied.
  - completed-trial evidence coverage: not applied (it needs Gemini and CT.gov history passes).
- **T24:**
  - scoped `safe.directory`: applied.
  - `db-apply` recreates `sim_features`: applied.

## Data fix: 909 month-only start dates (`docs/relabel_G2.md`)
- **The problem.** `map_trial` placed a trial on a side of 2018-01-01 only when its start date had a day. 909 ended trials with `YYYY-MM` starts sat in `pending` with no labels.
- **The fix.** They are now split (898 train, 11 test) and labeled through T11's own `label_trial` / `load_trial`.
- **Labeled accrual training data grew** from 264 trials with 21 failures to 1,376 with 136.
- **Where the stop reasons came from.** With Gemini's daily quota gone:
  - 25 came from the Gemini cache;
  - 220 from a keyword rule that implements the same definition. Checked against Gemini labels, it agreed on 98.6% of 73 train-split stop texts (kappa 0.97) and, checked once with the rules frozen, 97.5% of 120 test-split texts (kappa 0.94).
- **Disclosure.** 92 of the 136 train accrual failures carry keyword labels. Training only on exact-date trials gives test AUC 0.677 (`docs/reviews/G2.md`), so the label mix does not explain the result.
- **Open.** T11's still-running job uses the old code and adds new month-only trials as pending. Re-run `CLINOVA_MODE=demo uv run python scripts/relabel_month_starts.py` after it finishes.

## Stubs and fixtures
- **Swapped:**
  - `model.predict.score` / `flag_criteria` / `score_samples` serve v1.0;
  - the optimizer uses `ModelScorer`: fitted surrogate, T22 risk model, 30 bootstrap models;
  - the replay and checker use `model.train`;
  - API `live.score` / `flag_criteria` / `optimize` / the quick re-run reach the real modules whenever a bundle and the DB exist.
- **Left on purpose:**
  - `predict.score` raises `NotImplementedError` with no bundle on disk, so the API falls back to its fixtures. Tests point `MODELS_DIR` at an empty temp dir for that reason.
  - `optimize.search.optimize` refuses `FIXTURE-*` ids.
  - `/pulse` shows "not run" (T16 cut).
  - The amendment risk is a labeled 0.5 placeholder (no positive labels).
  - The reveal timeline has no events (`trial_versions` is empty).

## Cross-model review (T26): 0 critical, 4 major, 2 minor
- **Major 1 (results not reproducible):** fixed. Every evaluation writes `predictions.csv` (per-row p), `model_manifest.json` (data hashes and the ids behind training, calibration, PCA and ladders) and `metrics_detail.json`. The second reviewer reproduced every row from the bundle (max |Δp| about 1e-16).
- **Major 2 (held-out protocols shaped the edit ladders):** fixed. Ladders come from train-split protocols only (`ref["ladder_ids"]`). An integrity test checks this.
- **Major 3 (watcher latch):** fixed. It uses one population (simulated train trials), takes `sim_done` from the trained bundle, and has a test. The old watcher process (PID 24028, T22 worktree) still runs old code. G2 could not stop it: the permission was denied. It writes `v0.*` bundles into `models/`, and `MODEL_VERSION=v1.0` keeps it from being served. **Please stop it by hand.**
- **Major 4 (hit-rate denominator):** fixed. The note and `metrics_detail.json` report amended test trials, evaluated trials and exclusions by reason. Both baselines use the evaluated set.
- **Minor 1 (train() accepted test rows):** fixed. `train()` raises on non-train rows unless `allow_non_train=True`.
- **Minor 2 (embedding fallback):** T12-owned and not fixed. 0 affected rows at T26's query. Limitation: "an embedding could, in principle, come from a current title when the original registration is missing; none do in this snapshot."

## Final training and caches
- **Surrogate refit** (`docs/surrogate_metrics.json`):
  - It reproduced T24 exactly, because no new agent decisions exist: 2,695 decisions on 5 protocols (1 registered, 4 fixtures).
  - Enrollment agreement 0.685 vs majority 0.515; dropout 0.515 vs majority 0.563, so worse than majority.
  - Scoring 1,000 personas takes a median 7.5 ms.
  - This is agreement with synthetic Gemini decisions, not clinical accuracy.
- **Models:** two models (accrual, shortfall), 30 bootstraps each, isotonic calibration, frozen as `models/v1.0/` (gitignored) and tagged `model-v1.0`. Simulation features were off: there are fewer than 300 simulated trials.
- **Replay:** re-run with the real trainer, fixed hyperparameters in both arms (`docs/replay.json`, `docs/figures/replay.png`).
- **Demo trial and backups:** score, flags and optimizer results cached in `docs/demo_results/`; fixes persisted to `fixes`.
- **Optimizer speed.** With the real model the optimizer takes 117–144 s per trial (vs T23's 4.7 s with the heuristic scorer), even after the G2 caches in `predict`. The API should serve the cached results for these three trials.

## Demo trial NCT03232892 (test split, terminated for low accrual)
- **Risk** (`docs/demo_results/NCT03232892.json`):
  - accrual-failure p 0.072 (range 0.059–0.117): 50th percentile of test trials, 23rd among real failures. **The accrual model misses this trial.**
  - shortfall p 0.666 (98th percentile), but that model is not validated.
  - amendment p is the 0.5 placeholder.
- **Is the widened criterion in the top 3 flags? Yes, at rank 1.** The sponsor later removed `prior_platinum` (first vs current text, not date-verified). It ranks first on exclusion share: 63% of the unconditional virtual cohort, where the second-line setting is not applied. The model predicts no risk change for any flag (delta_p 0), so this is an exclusion-share ranking, not a model result. The other two flags are ECOG ≤ 1 and non-squamous histology.
- **Three fixes** (`docs/demo_results/NCT03232892_optimize.json`):
  - Enrollment speed is the relative change in eligible fraction × enroll rate. The range covers 5 cohort seeds.
  - Every fix has Δp_amend 0 and $0 expected amendment value, because the amendment model is a placeholder.
  - No verified completed-trial precedent exists in the stored evidence.

  | pick | edits | enrollment speed | dropout | diversity |
  | --- | --- | --- | --- | --- |
  | fastest | allow squamous histology (I1); ECOG limit lifted (I6, ≥ 0); CrCl ≥ 50 → ≥ 20 mL/min (I14) | +101% (+98% to +137%) | +4.4 points | +0.039 |
  | lowest dropout | drop bilirubin ≤ 1.5×ULN (I11); drop histology (I1); drop cardiac exclusion (E6) | +53% (+51% to +66%) | −2.0 points | +0.019 |
  | balanced | ECOG ≤ 1 → ≤ 2 (I6); drop histology (I1); drop CrCl (I14) | +95% (+90% to +128%) | +3.6 points | +0.037 |

  The lowest-dropout fix drops the cardiac exclusion, which the sponsor also removed.
- **Backups:**
  - **NCT03417882:** accrual p 0.053, 3rd percentile among failures. Flags: ECOG, hemoglobin, cardiac. Its diff is suspected parser restructuring, so no widened field is claimed.
  - **NCT06333678:** accrual p 0.117, 62nd percentile among failures. Flags: stage, ECOG, ECOG; the sponsor-removed interstitial-lung-disease exclusion is not in the top 3. Two of its three fixes drop it (E5).
  - **The optimizer ignores what defines a trial.** Its +757% to +770% fixes open a stage III trial to stage IV, which changes the trial's population. The optimizer does not know which rules define the indication; the author has to lock them.

## Integrity tests (`tests/integrity/test_integrity.py`, offline)
They check:
- features rebuild from the first-version JSON alone;
- the demo trial and both backups are test-split, start on or after 2018-01-01, and appear in no fitted set;
- no test trial is in training, OOF calibration, PCA or ladder sources;
- label counts and prevalence match `docs/data_report.md`, within 1% drift while ingestion streams;
- every served probability, and every demo range, is in [0, 1];
- the manifest matches `metrics.json`.

## Second review (skeptical biostatistician)
The full findings are in `docs/reviews/G2.md`.
- **Cheap fixes made:**
  - sponsor-only reference;
  - AUC by start period;
  - percentile ranks for the demo trials.
- **Limitations to show:**
  1. not shown to beat sponsor type or logistic;
  2. compressed probabilities, and the demo trial is an accrual miss;
  3. shortfall not validated;
  4. test holds only ended trials, so early stops are over-represented;
  5. replay counts each trial about 5 times, with 28 unique failures, and retraining did not help.

## Cuts recommended for the time left (hour 6.2 of 13)
1. **Do not claim** a hit rate, a simulation benefit, amendment risk or amendment dollars. Their inputs (version histories, 300+ simulated trials) cannot arrive today without Gemini.
2. **T32 (UI):** show accrual risk as a percentile with its range; label shortfall "not validated"; hide or grey out amendment and dollars; serve the three cached demo results instead of a live 2-minute optimizer run; present flags as "largest exclusions" rather than "model-ranked".
3. **Lead the story with what holds:**
   - first-version-only modeling, with leakage and integrity tests;
   - a stated, falsifiable result (AUC 0.70, not shown to beat simple baselines);
   - the parse-status trap caught and removed;
   - the demo's top flag is the rule the sponsor later removed.
4. **Keep the T16/MiroFish cut.** Do not spend time on new agent simulations until the quota resets (about 20 h).

## Background jobs and loose ends
- **T11 ingest** (`.worktrees/T11`, PID 8652): still running old `map_trial`; re-run the relabel script after it finishes.
- **T12 parse** (two `clinova.parse run --versions first` processes): still running.
- **T22 watcher** (PID 24028): stop it by hand (see Major 3).
- **Full suite:** `uv run ruff check . && uv run pytest -q` is green: ruff clean, 470 passed. Known flaky items: `tests/simulate/test_diversity.py::test_score_is_bounded_and_replication_invariant` (Hypothesis) and the T13 screen wall-clock benchmark failed once each under concurrent load and pass in isolation.
