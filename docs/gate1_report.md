# Gate 1 report

Hour 4.7 after kickoff. The integrator wrote this report. Every number below comes from a script output (`scripts/e2e_demo.py` writes `docs/gate1_e2e.json`; `scripts/pick_demo_trial.py`; the SQL counts listed under "Data") or from a task handoff.

## Merged
T11 (partial), T12 (partial), T13 (ok), T14 (partial) and T15 (ok) were merged into main with `--no-ff`, in that order, with no conflicts. T16 is `cut:` and was not merged, as G1 requires. There were no requests in `docs/contract_changes/`.

Integrator fixes:
- `common/llm.py`: `_call` now holds a strong reference to the `genai.Client`. `genai.Client.__del__` closes its HTTP session. `lru_cache` can build a duplicate client under a thread race and then garbage-collect it while its `.models` bound method is still running, which caused T12's "client has been closed" error.
- `api/live.py`:
  - Parse and simulate stages raise `LiveUnavailable` without a database (Gemini goes through `llm_cache`), so `CLINOVA_API_NO_DB` really is offline.
  - API simulations are no longer persisted, because edited or draft runs must not pollute first-version `sim_runs`.
  - `protocol_from_trial` reuses `simulate.__main__.registered_protocols`, so it gets the same title, arms and visits as the batch path. A trial with no first version now raises `LookupError`.
- `simulate/__main__.py`: `registered_protocols` skips trials with an empty first version or empty first eligibility text.
- Tests: API tests updated now that cohort and screen run as the real modules (the funnel is live, the later stages still use fixtures offline). The config test now accepts the new `DEMO_NCT_ID` default.

Fixture swap status:
- The runner calls the real T13 cohort and screen.
- The API and app call the real parse, cohort, screen and simulate.
- Score, flags, optimize, ledger and backtest are still fixtures, labelled `source: fixture`, until T21–T25.

## Demo trial
**NCT03232892** (test split, terminated "Low Accrual", 2019-09-11). Backups: NCT03417882 and NCT06333678. The reasons and the caveats are in `docs/demo_trial.md`. Version histories are not loaded yet, so "widened before termination" is inferred from first text vs current text and is not confirmed by date.

## End-to-end run (NCT03232892, first registered version)
The trial parsed into 25 rules: 20 parseable and 5 skipped as unparseable (I2, E7–E10). 15 visits were estimated. Setting is second_line_plus. Each seed screened 1,000 personas; up to 200 eligible personas were run as live Gemini agents; both runs are stored in `sim_runs` / `persona_outcomes`.

| | seed 1 | seed 2 |
| --- | --- | --- |
| run_id | aa0f5c1b… | 698b7c20… |
| eligible | 214 / 1000 (21.4%) | 237 / 1000 (23.7%) |
| agents | 200 | 200 |
| enroll rate | 0.505 | 0.580 |
| completion rate (of enrolled) | 0.822 | 0.716 |
| median dropout visit | 6 | 6 |
| dropout curve, cumulative by visit 4 / 8 / 15 | 0.030 / 0.149 / 0.178 | 0.060 / 0.250 / 0.284 |
| top burden reasons | travel 0.74, time off work 0.51, caregiving 0.27, cost 0.27 | travel 0.74, time off work 0.57, caregiving 0.32, cost 0.24 |

**Top exclusion: ECOG ≤ 1 (I6).** Marginal exclusions:
- Seed 1: ECOG 353 (35%), histology non-squamous 326, prior platinum 115, hemoglobin 106, CrCl 100.
- Seed 2: ECOG 334, histology 302, CrCl 123, hemoglobin 106, prior platinum 100.

Sequential funnel, seed 1: 1000 → histology 674 → prior platinum 592 → … → ECOG 360 → … → 214.

**Eligible fraction vs sanity range:** 21.4% and 23.7% are just below the 25–65% plausibility band from the T03 spike (`docs/cohort_sanity.md`). T13 saw the same thing (17.4% on its fixture protocol), with ECOG and prior-therapy priors as the main drivers. Nothing was tuned.

**Did the sponsor-widened criterion rank in the top 3 exclusions?** The sponsor removed two rules:
- `prior_platinum`: third-largest marginal exclusion for seed 1 (115), fifth for seed 2 (100). So it is in the top 3 for one of the two seeds only.
- `cardiac_condition`: not in the top 5 for either seed (12 and 20 removed sequentially).

So the answer is a partial yes.

## Label agreement
A Claude reviewer saw only the 40 raw stop reasons in `docs/labels_spotcheck.csv` and the class list. It agreed with the Gemini/Codex labels on **34/40 = 85.0%**. That is not below the 85% threshold, so the prompt was not changed. The disagreements:
- NCT04171284: other vs business_funding
- NCT03520686: accrual vs unknown. The stop text "no longer active subjects" gives no accrual reason, and this trial is in the accrual set.
- NCT02710396: business_funding vs efficacy_futility
- NCT05176925: business_funding vs unknown
- NCT03472560: efficacy_futility vs other
- NCT02588261: safety vs efficacy_futility

## Data (Tiger Cloud, hour 4.7)
- Trials loaded: 294 of 3,766 discovered. All are TERMINATED, because the pull goes terminated-first. Split: test 75, train 41, pending 178 (pending = no exact start date).
- Labeled: stop_category 116; accrual_fail 107 known; shortfall 109 known; amend 0 (no histories).
- Parsed, first version: 161 trials, 3,171 rules, 1,398 of them unparseable (44%).
- Embedded: 169 trials.
- `trial_versions`: 0 rows. The history pass has not started.

Background jobs:
- T11 full pull: `.data/logs/T11-full-pull.log`. Last line: loaded 294, labeled 107, failed 41.
- T12 parse batch: `.data/logs/T12-parse.log`, running from `.claude/worktrees/agent-ad5d33d71a38ddade`.

## Looks wrong / suspected cause
1. **The Gemini free-tier quota is the bottleneck: 15 requests per minute for gemini-3.5-flash-lite**, shared by T11, T12, G1 and other agents. In 15 minutes `llm_usage` logged 1,251 failed `parse` attempts against 170 successes. Every failed attempt is also counted against `MAX_GEMINI_CALLS`. The e2e needed four attempts. Fix: a paid tier, or a global rate limiter (requests per minute) in `common/llm.py`.
2. The T12 batch logs `UniqueViolation` on `criteria_rules_pkey` because concurrent rounds or workers parse the same trial. The skip check and the insert are not atomic; `ON CONFLICT DO NOTHING` in `parse.__main__.save_protocol` would fix it.
3. 41 of 294 trials failed ingestion (ValueError and others). T11 should triage them.
4. Version histories have 0 rows, so amendment labels, demo-trial date checks and the "widened" hit rate are all unverified.
5. The eligible fraction sits below the plausibility band (see above), likely because of the ECOG prior (ECOG ≥ 2 is 35% in the cohort).
6. Diff "removed" can be parser variance between two separate parses (see the NCT03417882 backup).
7. `tests/simulate/test_diversity.py::test_missing_and_unknown_are_unmatched_mass` failed once in a full run and passed on rerun. It is flaky.

## Review findings (clinova-reviewer on merged main)
Fixed:
- e2e script crash (`source_text` → `raw_text`).
- API runs no longer persisted.
- API protocol now matches the batch protocol.
- Empty-first-version filter in `registered_protocols` / `protocol_from_trial`.
- Duplicate e2e seed-1 runs created by the crashed attempts were deleted (3 runs and their 3,000 persona rows).

Open, for owners:
- **T11**: when there is no original snapshot, `eligibility_text_first` is stored as `""` rather than NULL. The parse and embed queries filter only `IS NOT NULL`.
- **T11**: the `FROZEN` upsert guard in `ingest/load.py` (the leakage guarantee) has no test.
- **T15**:
  - A draft-text run with parse down silently uses the fixture trial's protocol.
  - A single RunResult can mix live and fixture stages.
- **T14 / T24**: one invalid agent decision raises instead of retrying, and because responses are cached it fails the same way every time. A decliner with a `dropout_visit` is not rejected.
- **T12**:
  - `--versions amended` stores rules as `version_no=1`, which can clash with real CT.gov version numbers.
  - `diff._cond` raises on an ordinal value outside the vocabulary.
- **T13**: `_source_rows` re-reads and re-hashes the parquet on every cohort call. A fallback to census-synthetic personas is only logged, never surfaced.
- **T14**: `completion_rate` is 0 when nobody enrolled.
- Untested: `persist_run`, `parse.__main__._todo` / `save_protocol`, `parse.embed`, `ingest.pipeline`.
- Ownership: T12 added `scripts/fetch_gold_candidates.py`, `scripts/t12_batch.sh` and `docs/parse_gold_metrics.json`, which its task file does not name; accepted. The integrator edited T15-owned `api/live.py` and `tests/api/test_routes.py` to fix merge breakage.
