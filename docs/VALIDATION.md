# Validation and traceability

## Intended use and boundaries

Clinova is a research prototype for protocol authors and study designers to explore operational enrollment and retention risks in NSCLC trials. It provides risk estimates, synthetic-cohort screens, and protocol-edit suggestions for human review. It does not approve protocols, make clinical decisions, assess treatment efficacy, or replace clinical, regulatory, or statistical review. It uses public ClinicalTrials.gov records and synthetic personas; it does not use real patient data. Registry outcomes and protocol edits are proxies for operational outcomes, and predictions are correlational.

## Requirements-to-tests traceability

Test identifiers below are pytest node IDs. A dash means the repository has no test that proves that checklist item; implementation or documentation alone is not test evidence.

| Requirement | Implemented / documented in | Test evidence |
| --- | --- | --- |
| `docker compose up` starts the app, API, and seeded local TimescaleDB | `docker-compose.yml`, `db/seed/`, `Makefile` (`up`) | — (no compose-and-seed startup test found) |
| Demo mode works from cached outputs without API keys or network calls | `clinova/common/llm.py`; `clinova/api/live.py`; `clinova/api/main.py` | `tests/common/test_llm.py::test_demo_miss_raises_and_never_builds_client`; `tests/common/test_llm.py::test_cache_hit_skips_client_in_live_and_demo`; `tests/api/test_wiring.py::test_demo_mode_cache_miss_falls_back_to_fixture_for_that_stage` |
| Live mode uses Gemini and optionally Tiger Cloud | `clinova/common/config.py`; `clinova/common/llm.py`; `clinova/common/db.py` | `tests/common/test_config.py` (configuration parsing); no live-service test is run by pytest |
| MIT license; MiroFish remains separate and unmodified | `LICENSE`; `clinova/simulate/mirofish_client.py`; `docs/spikes/personas_mirofish.md` | — (no license-boundary test found) |
| Parser schema and validation | `clinova/parse/`; `clinova/contracts.py` | `tests/parse/test_parse.py` |
| Eligibility screen behavior | `clinova/simulate/screen.py` | `tests/simulate/test_screen.py` |
| Metrics helpers | `clinova/model/evaluate.py` | `tests/model/test_model.py::test_metric_helpers` |
| First-registration-only feature rule (no later protocol or label-side leakage) | `clinova/model/features.py`; `clinova/model/train.py`; `clinova/ingest/pull.py` | `tests/model/test_model.py::test_features_come_from_first_version_json_alone`; `tests/ingest/test_pull.py::test_original_features_are_kept_separate_and_zero_is_valid`; `tests/ingest/test_pull.py::test_missing_first_never_copies_current_features`; `tests/integrity/test_integrity.py::test_no_test_trial_touches_training_pca_or_calibration` |
| Train/test time split: start before 2018-01-01 is train; on or after is test | `clinova/ingest/pull.py` | `tests/ingest/test_pull.py::test_split_boundary`; `tests/ingest/test_pull.py::test_month_only_start_maps_to_first_of_month`; `tests/ingest/test_pull.py::test_unknown_or_partial_start_is_pending_and_raw_precision_survives` |
| Training and replay do not consume test or future outcomes | `clinova/model/train.py`; `clinova/loop/replay.py` | `tests/model/test_model.py::test_train_rejects_non_train_rows_unless_opted_out`; `tests/loop/test_replay.py::test_training_cutoff_excludes_future_and_test_split`; `tests/loop/test_replay.py::test_real_t22_replay_never_trains_on_test_split_or_future_outcomes` |
| Predictions retain model version and timestamp; accept/reject feedback is logged | `db/schema.sql`; `clinova/loop/ledger.py`; `clinova/api/main.py` | `tests/loop/test_ledger.py::test_prediction_persists_all_fields_and_utc_timestamp`; `tests/loop/test_ledger.py::test_feedback_persists_decisions_and_user` (accept/reject cases); `tests/loop/test_ledger.py::test_audit_view_links_only_explicit_prediction_targets_and_keeps_all_rows`; `tests/api/test_routes.py::test_feedback_then_audit`; `tests/api/test_wiring.py::test_accept_on_fixtures_uses_optimizer_estimate` |
| CI runs repository checks on pushes | `.github/workflows/ci.yml`; `Makefile` (`check`) | CI runs `make check`; the local validation command is recorded in the T34 handoff |
| README has a two-minute quickstart, architecture, metrics, and limitations | `README.md` | — (documentation content is not asserted by a test) |

## Audit trail

Each ledger prediction stores `prediction_id`, `trial_id`, `target`, probability and interval, `predicted_at`, and `model_version` in the `predictions` hypertable. Human decisions use the `feedback` hypertable and record a decision (`accept`, `reject`, `rate`, or `comment`), target, user, and timestamp. The accept endpoint records `decision='accept'`; `POST /feedback` records rejects and other feedback. `GET /audit` returns prediction and feedback rows. When the database is unavailable, the API uses an in-memory fixture audit store; those rows disappear when the API process restarts and are identified by the returned source.

For a persisted joined view, query `audit_log`, which keeps predictions and feedback as separate rows unless feedback explicitly targets a prediction:

```sql
SELECT prediction_id, trial_id, target, p, ci_low, ci_high,
       model_version, predicted_at, decision, user_name, created_at
FROM audit_log
WHERE trial_id = 'NCT03232892'
ORDER BY COALESCE(created_at, predicted_at) DESC;
```

The view and endpoint are useful for traceability, but the current model bundle and source dataset are not both archived with the published metrics; see the reproducibility limitation below.

## Data provenance and labels

The ClinicalTrials.gov API v2 search probe ran on **2026-10-10 22:45 UTC** (`docs/spikes/ctgov.md`). It queried `GET https://clinicaltrials.gov/api/v2/studies` with:

```text
filter.advanced=AREA[ConditionSearch]("non-small cell lung cancer") AND AREA[StudyType]INTERVENTIONAL AND (AREA[Phase]PHASE2 OR AREA[Phase]PHASE3) AND NOT AREA[Phase]PHASE1
countTotal=true
pageSize=1
```

The probe returned 3,766 indexed studies. The indexed cohort may include basket or mixed-histology studies, so it is not a manually confirmed set of exclusively NSCLC trials. The T11 ingestion snapshot in `docs/handoff/T11.md` was recorded at **2026-10-11T03:06:06.038844Z**; subsequent ingestion and month-only relabeling continued on 2026-10-11. The model evidence below uses its own frozen report timestamp, **2026-10-11T04:44:32.596506Z**. Counts can differ between these snapshots.

Model protocol features come from the first registered (`version 0`) record only. Train trials started before **2018-01-01**; test trials started on or after that date. A month-only start is mapped to the first of the month for the split; unresolved partial or unknown dates remain pending. Accrual-failure labels are positive only for terminated trials whose stop reason explicitly indicates recruitment or enrollment failure. Shortfall is actual enrollment below **80%** of the first-version estimated enrollment. Amendment is positive for a post-start eligibility change classified as widened or mixed, negative only when history is complete, and otherwise unknown. These rules are implemented in `clinova/ingest/labels.py` and documented in `docs/ARCHITECTURE.md` §5.1.

At Gate 1, a reviewer from the other model family (Claude) independently labeled 40 raw stop reasons without seeing the Gemini/Codex labels. Agreement was **34/40 (85.0%)** (`docs/gate1_report.md`, `docs/labels_spotcheck.csv`). The prompt was unchanged because agreement was not below the stated 85% threshold. This spot-check is distinct from the later Gate 2 keyword-rule comparison used to relabel month-only dates (`docs/relabel_G2.md`); Gate 2 reports 98.63% accuracy / κ 0.9708 on 73 train examples and 97.5% / κ 0.939 on 120 frozen-rule test examples. Keyword labels contribute to the training data and are disclosed as a separate label source.

## Model validation snapshot

Values below are copied at full stored precision from `docs/metrics.json` (model `v1.0`, generated **2026-10-11T04:44:32.596506Z**, split date `2018-01-01`; 1,424 train rows and 360 test rows overall). Test rows with a missing target are excluded from that target's metric.

| Target | Test rows / positives | LightGBM AUC (95% CI) | Logistic baseline AUC (95% CI) | LightGBM Brier | Logistic baseline Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Accrual failure | 359 / 34 | 0.7010859728506788 (0.6234740409308932–0.7739021388817245) | 0.6605429864253394 (0.5934327268372507–0.7233020787336866) | 0.0827264574773474 | 0.08406123693283175 |
| Enrollment shortfall | 353 / 192 | 0.5976805124223602 (0.5377748162637274–0.6546937434557876) | 0.6049430641821947 (0.553905883459633–0.6608609339230066) | 0.2517696043985752 | 0.24825484656988628 |

Amendment risk was not trained: the train set has 0 positive and 22 negative amendment labels. Hit rate is `null` because there are no labeled amended test trials. Simulation ablation is `null`: the snapshot has 16 simulated trials, below the stated sample requirement, and does not have both classes in each required split. Simulation features were not used. Do not interpret either `null` as zero performance.

`docs/replay.json` was generated **2026-10-11T04:35:39.572989Z**. Its 48-month retrospective replay (2020-01 through 2023-12) reports pooled Brier **0.078241 retrained vs 0.076678 frozen**, over **1,736** overlapping scored windows, **324** unique trials, and **216** initial training trials. The replay did not beat the frozen model on pooled Brier. Completion dates are retrospective proxies for when outcomes became observable; this is provisional event-time evidence, not a publication-time backtest.

The T26 cross-model review (`docs/reviews/T26.md`) found **0 critical, 4 major, and 2 minor** findings. Gate 2 reports the critical metric-artifact, held-out ladder, watcher, and denominator findings as addressed or disclosed, with the embedding fallback limitation remaining. The final verdict was that the accrual result is provisional exploratory evidence of above-chance ranking with poor probability calibration and no established improvement over logistic regression. The snapshot does not support amendment, criterion-hit-rate, or simulation-benefit claims. Although the current v1.0 row predictions reproduced from the delivered bundle, T26's original historical metric snapshot could not be independently reproduced because the original frozen bundle and source dataset were unavailable to that review.

## Known limitations

- ClinicalTrials.gov version history can be unavailable or unreliable. Some comparisons use earliest available versus current protocol; without dated adjacent versions, a change cannot be claimed to have occurred at a particular time. In this snapshot `trial_versions` is empty, so amendment labels, the hit rate, and the demo's widening date are unverified.
- The test set contains ended trials that began in 2018 or later. Trials still running are absent, and early stops are over-represented. On the least-censored 2018–2019 subset, accrual AUC is **0.604** over **143** trials and **12** failures, versus **0.651** for the baseline (`docs/gate2_report.md`).
- Accrual probabilities are compressed: the maximum served test probability is **0.218**. The demo, NCT03232892, was an accrual-model miss (p **0.072**). Shortfall prediction is not validated; amendment prediction is a placeholder, and no amendment dollars or simulation benefit should be claimed.
- Replay counts many trials in overlapping monthly windows and did not improve pooled Brier over the frozen model. Its observation dates are retrospective proxies.
- The cohort search may include basket trials. Accrual labels combine Gemini labels, cached Gemini labels, and a frozen keyword rule for month-only records; the labeler mixture and its agreement results must be considered when interpreting model performance.
- Too few clean NSCLC labels can limit stable estimates; widening to solid tumors is an architecture fallback, not evidence that this snapshot has done so. Synthetic persona traits include published-distribution assumptions and do not represent actual patients. Simulation may add no predictive accuracy.
- Additional architecture risks: enrollment restrictions and protocol burden may have weaker predictive value than assumed; clinicaltrials.gov edits are a public proxy for amendments; free-text stop reasons can misclassify business or funding decisions; protocols and cohorts evolve, so retrospective associations may not transport to future trials. Gemini quota/cost and runtime limits can reduce simulation coverage. See `docs/ARCHITECTURE.md` §10, `docs/gate1_report.md`, `docs/gate2_report.md`, and `docs/reviews/T26.md` for the full evidence and disclosures.
