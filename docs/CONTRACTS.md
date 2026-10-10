# Clinova contracts (contracts-v1)

Plain-English guide to the shared pieces every task builds against: models, tables, function signatures, fixtures, and which task fills each stub. The code is the final word: `clinova/contracts.py` (models and vocabularies, with a description on every field), `db/schema.sql` (tables), and the stub modules (signatures and docstrings). Only the integrator (T00, G1 to G4) edits these. If you need a change, write the exact diff and the reason in `docs/contract_changes/<ID>.md` and work around it in your own folder.

## Ground rules

- **Settings** come only from `clinova.common.config.get_settings()`. It reads `.env` from the working tree and then from the main clone. Gemini model ids appear only there (`GEMINI_MODEL_FAST`, `GEMINI_EMBED_MODEL`, checked against Google's docs on 2026-10-10). The amendment cost is `config.amendment_cost(phase)`: $141,000 for phase2, $535,000 for phase3 and phase2_3, about 3 months of delay (Regeneron Starter Sheet 01).
- **Gemini** is called only through `clinova.common.llm`:
  - `generate_json(prompt, Schema, *, namespace, seed, temperature=0.2) -> Schema` and `embed(texts, *, namespace, dim=768) -> list[list[float]]`, plus async `agenerate_json` / `aembed`.
  - Every response is cached in `llm_cache`. In demo mode a cache miss raises `CacheMiss` and nothing goes to the network.
  - Live mode backs off on 429 and 5xx, logs every request to `llm_usage`, and raises `BudgetExceeded` once this key reaches `MAX_GEMINI_CALLS`.
  - Pick one stable `namespace` per use, e.g. `parse`, `stop_reason`, `amend_class`, `agents`, `trials_embed`. Keep prompts deterministic so the cache hits.
  - Sync calls must not run inside a running event loop. Use the `a*` versions there.
- **Database**: `clinova.common.db.get_conn()` is a pooled psycopg 3 connection with pgvector registered. It commits on success and rolls back on error. `apply_schema()` / `make db-apply` re-applies `db/schema.sql`, which is idempotent.
- **Fixtures**: `clinova.common.fixtures.load_fixture(name, Model)` loads a file from `tests/fixtures/`, for tests and for API fixture fallbacks, e.g. `load_fixture("risk_scores.json", list[RiskScore])`.
- **Leakage**: features come only from a trial's first registered version. `TrialRecord` marks every outcome-side field `LABEL-SIDE` in its description, and these are never features.
- **Split**: train if `start_date` < 2018-01-01, test if on or after. A trial that has not ended (`ENDED_STATUSES`) is `pending` and has no labels.

## Models (`clinova/contracts.py`)

All models reject unknown keys. Enums serialize as plain strings.

| Model | What it is | Produced by | Used by |
| --- | --- | --- | --- |
| `TrialRecord` | One CT.gov trial (row of `trials`). Design fields come from the FIRST version; status, why_stopped, actual enrollment, current text and labels are LABEL-SIDE. Also holds `history_source` (`versions` or the `earliest_vs_current` fallback). | T11 | T12, T22, T21, API |
| `VersionEvent` | One field change between consecutive versions (row of `trial_versions`). Eligibility changes carry `change_class` (widened, narrowed, mixed, administrative). | T11 | T22, T23 precedents, backtest reveal |
| `ClinicalField`, `Operator`, `FIELD_SPECS` | The controlled vocabulary for rules. `FIELD_SPECS` gives each field's kind (numeric, ordinal, categorical, boolean), canonical unit, allowed values and plausible range. Ordered vocabularies: `STAGES`, `BrainMets`. Categorical vocabularies: `HISTOLOGIES`, `BIOMARKER_STATUSES`, `RACE_ETHNICITIES`. | T00 | T12 parser and sanity checks, T13 screen |
| `CriteriaRule` | One atomic rule (row of `criteria_rules`). An inclusion rule must hold; a patient who satisfies an exclusion rule is excluded; a missing value passes. `field=other` always means `unparseable`. Rules on `LOCKED_BY_DEFAULT` fields (pregnancy, consent_capacity) are `locked`. Ids are `I1..`, `E1..`. | T12 | T13, T22, T23 |
| `Visit` | One explicit visit, with cycles expanded over 12 months. `estimated=True` when built from design fields. Procedures use `PROCEDURE_VOCAB`. | T12 | T14 agents and surrogate, T23 |
| `ParsedProtocol` | Rules, visits, setting, phase, title, summary, arms and randomized for one trial version or draft. `unparseable` is recomputed from the rules. | T12 (and T25 for drafts) | everyone downstream |
| `RuleChange` | One per-field diff between two parsed versions: relaxed, tightened, removed or added. | T12 `diff_rules` | T22 hit rate, backtest |
| `ClinicalTraits` | One virtual patient's clinical state. Field names equal `ClinicalField` values, plus smoking, weight and creatinine. | T13 | T13 screen, T14 |
| `Persona` | Life context (age, sex, race_ethnicity, work, caregiving, travel, income, risk tolerance, health literacy, narrative) plus `clinical`. | T13 | T14 agents |
| `COHORT_COLUMNS` | The column list of the cohort dataframe, so `df[rule.field]` works for every field except `other`. | T13 `cohort_frame` | screen, surrogate, diversity |
| `ScreenResult`, `FunnelStage`, `RuleExclusion`, `Funnel` | Screening output. The sequential funnel applies rules in protocol order. Marginal exclusion: `excluded_n` counts personas failing a rule regardless of the others (the hit-rate baseline ranks by this); `excluded_only_n` counts personas failing only that rule. | T13 | UI funnel, T22, T14 |
| `AgentDecision` | One persona agent's choice: enroll, dropout_visit (None means they complete), reason, burden tags (`BurdenTag`). | T14 | runner |
| `SimRunSummary` | One (trial, seed) run (row of `sim_runs`): eligible fraction, enroll and completion rates, cumulative dropout curve by visit, top reasons, funnel. | T14 / T24 | T22 via `sim_features`, UI |
| `RiskScore`, `Driver` | Calibrated p with a range, a confidence badge and SHAP drivers in plain words, for each `Target` (accrual_fail, shortfall, amend). | T22 | API, ledger |
| `CriterionFlag` | A rule ranked by risk drop: where, why, what to do, `delta_p` (positive means risk drops), and the suggested `Edit`. | T22 `flag_criteria` | UI, hit rate |
| `Edit` | relax_rule (`new_value`, optional `new_operator`), drop_rule, cut_visit, merge_visits (with the next visit), make_remote. `target_id` is a rule_id, or str(visit_no) for visit edits. | T23 / T22 | screen `apply_edits`, optimizer |
| `Fix`, `FixRanges`, `Precedent`, `Interval` | One trade-off fix with 1 to 3 edits: fastest, lowest_dropout or balanced. Holds the deltas, their ranges, `dollar_value` = max(0, -delta_p_amend) x phase cost (an expected value), and precedents. | T23 | UI, `fixes` |
| `Prediction` | A logged score (row of `predictions`), with `context` live, backtest, replay, validation or fixture. | T21 ledger | replay, audit |
| `Feedback` | A human accept, reject, rate or comment (row of `feedback`). | T21 ledger via API | audit |
| `RunStatus`, `RunResult`, `RunStage` | Payload of POST/GET /runs. Progress by stage; when done, the result holds protocol, funnel, sims, risks, flags and fixes. | T15 / T25 | app |
| `BacktestResult` | Payload of GET /trials/{nct}/backtest: the trial, the first-version run, version events and rule changes. | T25 | app reveal |
| `ValidationMetrics` (+ `TargetMetrics`, `MetricCI`, `ReliabilityBin`, `HitRate`, `Ablation`) | Shape of `docs/metrics.json`. | T22 | /validation, deck |
| `ReplayResult` (+ `ReplayMonth`, `ReplayArm`) | Shape of `docs/replay.json`. | T21 | /replay, video |

## Tables (`db/schema.sql`, applied to Tiger Cloud: PostgreSQL 18.6, TimescaleDB 2.30.2, pgvector 0.8.7)

Write only to your own tables. Read anything. Hypertable primary keys include the time column.

| Table | Kind (time column) | Owner | Matches |
| --- | --- | --- | --- |
| `trials` | table | T11 | `TrialRecord` (jsonb `first_version`, `current`) |
| `trial_versions` | hypertable (`version_date` date) | T11 | `VersionEvent` |
| `criteria_rules` | table, PK (nct_id, version_no, rule_id); `nct_id` may hold a draft or fixture id | T12 | `CriteriaRule` (+ nct_id, version_no; `value` jsonb) |
| `trial_embeddings` | table, `embedding vector(768)`, HNSW cosine index | T12 | `parse.embed` |
| `sim_runs` | hypertable (`run_at`) | T14 / T24 | `SimRunSummary` (curve, reasons, funnel as jsonb) |
| `persona_outcomes` | table, PK (run_id, persona_id), `persona` jsonb snapshot | T14 / T24 | `AgentDecision` + eligibility |
| `sim_features` | view, one row per simulated trial; **created by T24**, not by the schema | T24 | T22 features |
| `predictions` | hypertable (`predicted_at`) | T21 | `Prediction` |
| `outcomes` | hypertable (`observed_at`); label in accrual_fail, shortfall, amend, status | T11 initial, T21 updates | |
| `prediction_scores` | hypertable (`scored_at`): p, y, month, context, model_version | T21 | |
| `prediction_scores_monthly` | continuous aggregate: monthly n, Brier, prevalence, mean_p by target, context and model_version; real-time. After a replay, run `CALL refresh_continuous_aggregate('prediction_scores_monthly', NULL, NULL)` in autocommit. | T21 | replay chart |
| `feedback` | hypertable (`created_at`); column is `user_name` ("user" is reserved) | T21 | `Feedback` |
| `fixes` | table, edits and metrics jsonb | T23 | `Fix` |
| `drafts` | table | T25 | pasted protocols |
| `llm_cache`, `llm_usage` | table and hypertable (`created_at`) | T00 (shared, written only by `common/llm.py`) | |
| `schema_meta` | table; `schema_version = contracts-v1` | integrator | |

## Signatures (stubs raise `NotImplementedError` until the owner fills them)

| Function | Owner | Contract |
| --- | --- | --- |
| `ingest.pipeline.run(limit=None, *, scope="nsclc", resume=True) -> dict[str, int]` | T11 | Pull, history, labels, load. CLI: `python -m clinova.ingest run [--limit N] [--scope solid_tumors]` |
| `parse.parser.parse_protocol(eligibility_text, schedule_text=None, *, trial_id, version_no=0, phase=None) -> ParsedProtocol` | T12 | One rule per atomic condition, canonical units, flag never guess, sanity checks |
| `parse.visits.extract_visits(schedule_text, *, trial_id) -> list[Visit]`, `estimate_visits(trial: TrialRecord) -> list[Visit]` | T12 | Estimated visits have `estimated=True` |
| `parse.diff.diff_rules(old, new) -> list[RuleChange]` | T12 | Pure |
| `parse.embed.embed_trials(nct_ids=None) -> int`, `nearest(query, k=10, *, exclude=()) -> list[tuple[str, float]]` | T12 | `query` is an NCT id or a vector; returns cosine distances, ascending. CLI: `python -m clinova.parse run --versions first|amended`, `python -m clinova.parse embed` |
| `cohort.generate.generate_cohort(n, seed, setting="any") -> list[Persona]`, `cohort_frame(personas) -> DataFrame`, `generate_cohort_df(n, seed, setting) -> DataFrame` | T13 | Deterministic per seed; columns = `COHORT_COLUMNS` |
| `simulate.screen.screen(rules, cohort_df, *, trial_id="") -> (np.ndarray[bool], Funnel)` | T13 | Mask aligned with rows; 10k x 25 in under 50 ms |
| `simulate.screen.screen_results(rules, cohort_df) -> list[ScreenResult]`, `apply_edits(rules, edits) -> list[CriteriaRule]` | T13 | `apply_edits` handles rule edits only, raises on locked rules, never mutates. Visit edits are applied in `optimize/` |
| `simulate.diversity.diversity_index(eligible_df) -> float` | T13 | 1.0 matches the NSCLC reference |
| `simulate.agents.trial_offer(protocol) -> str`, `arun_agents(personas, protocol, seed)` (async), `run_agents(...) -> list[AgentDecision]` | T14 | 25 personas per call, via `llm.agenerate_json` |
| `simulate.runner.simulate_trial(protocol, n=1000, seeds=(1, 2), max_agents=200, *, persist=True) -> list[SimRunSummary]` | T14, T24 | One summary per seed; writes `sim_runs` and `persona_outcomes` |
| `simulate.surrogate.Surrogate.fit(outcomes_df)`, `.score(protocol, cohort_df) -> (enroll_rate, dropout_risk)`, `.save(path)`, `Surrogate.load(path=None)` | T14, T24 | `score` under 20 ms. CLI: `python -m clinova.simulate batch --n-trials N --seeds 2 --max-calls M --dry-run` |
| `model.features.build_features(trials_df, protocols=None, sim_features=None) -> DataFrame` | T22 | First version only, no start year |
| `model.train.train(df, cfg=None) -> dict`, `load_bundle(version=None) -> dict` | T22 | Bundle keys are in the docstring; T21 calls `train` in the replay |
| `model.predict.score(protocol_or_trial, *, sim=None, model_version=None) -> list[RiskScore]`, `flag_criteria(protocol, *, k=3) -> list[CriterionFlag]` | T22 | Scores in target order: accrual_fail, shortfall, amend |
| `optimize.search.optimize(protocol, *, max_edits=3, n_trials=300, seed=42) -> list[Fix]` | T23 | Three picks, under 10 s. CLI: `python -m clinova.optimize [--trial NCT]` |
| `loop.ledger.record_prediction(trial_id, risk, *, context="live", predicted_at=None) -> Prediction`, `record_feedback(feedback) -> Feedback` | T21 | |
| `loop.checker.check_outcomes(*, limit=None, retrain_threshold=25) -> dict[str, int]` | T21 | CLI: `python -m clinova.loop check` |
| `loop.replay.run_replay(*, freeze=2019-12-31, start="2020-01", end="2023-12", horizon_months=6, target=accrual_fail) -> ReplayResult` | T21 | CLI: `python -m clinova.loop replay` |
| `clinova.api.main:app` (FastAPI), `clinova/app/main.py` (Streamlit) | T15, then T25, then T32 | Launched by `python -m clinova.api` / `python -m clinova.app` |

`tests/test_stubs.py` checks that every name above exists and that every CLI parses `--help`. Keep the names and signatures; add whatever else you need.

## Fixtures (`tests/fixtures/`, synthetic, validated by `tests/test_contracts.py`)

| File | Model | Notes |
| --- | --- | --- |
| `protocol_2L_eligibility.txt`, `protocol_2L_schedule.txt` | text | Synthetic second-line advanced NSCLC protocol |
| `parsed_protocol.json` | `ParsedProtocol` | `FIXTURE-2L-001`: 22 atomic rules (I1-I16, E1-E6), 8 visits. E6 (oral medication) is the unparseable example. I16 (consent) and E5 (pregnancy) are locked |
| `personas.json` | `list[Persona]` | 200 personas from `make_personas.py` (seed 42) |
| `funnel.json` | `Funnel` | Output of `make_funnel.py`'s reference screen run on the 200 personas: 56 eligible (28%); top exclusion I12, CrCl >= 60 |
| `agent_decisions.json` | `list[AgentDecision]` | Hand-written decisions for 25 eligible personas: 16 enroll, 5 drop out |
| `sim_run_summary.json` | `SimRunSummary` | Derived from the funnel and the decisions by `make_funnel.py` |
| `risk_scores.json`, `criterion_flags.json`, `fixes.json` | `list[RiskScore]`, `list[CriterionFlag]`, `list[Fix]` | Illustrative model and optimizer outputs |
| `trial_record.json`, `version_events.json` | `TrialRecord`, `list[VersionEvent]` | Fictional NCT99999901, terminated for accrual, eligibility widened |
| `metrics.json`, `replay.json` | `ValidationMetrics`, `ReplayResult` | Final shapes. Numbers are **illustrative, not results** (marked in notes and summary) |

Fixture numbers are never quoted as results anywhere (honesty rule).
