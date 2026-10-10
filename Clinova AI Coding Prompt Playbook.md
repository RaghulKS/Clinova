# Clinova: AI Coding Prompt Playbook

Oct 10, 2026 · @Suvil

## How to run this

Every step of Clinova is a copy-paste prompt for Claude Code below. Up to six sessions run at once, each in its own git worktree and owning its own folders, and they meet at two integration gates. People review output, pick the demo trial, and record the video.

### Before hour 0 (one person, about 15 minutes)

- [ ] Install Claude Code, Docker Desktop and `uv` on every laptop that will run a lane.
- [ ] Create an empty GitHub repo named `clinova`, clone it, copy the architecture file in as `docs/ARCHITECTURE.md`, and push.
- [ ] Get a Gemini API key with billing or hackathon credits. The batch simulation makes thousands of calls, and free-tier limits will stall it.
- [ ] Create a Tiger Cloud service and copy its connection string. It is the shared dev database across laptops and the live-demo database; judges get a local TimescaleDB instead.
- [ ] Make a scratch folder `../clinova-spikes` outside the repo for the three research spikes.

### Rules that make parallel work safe

1. **Contracts first.** P0.1 freezes the shared data models, database schema, function signatures and test fixtures. It is the only stretch where every lane waits.
2. **One worktree per lane.** Same repo, separate folders, separate branches, so sessions never overwrite each other's files.
3. **Folder and table ownership.** A lane writes only to its own folders and tables (lane map below). A lane that needs a shared change files prompt U4 instead of editing.
4. **Fixtures over waiting.** Downstream lanes build against `tests/fixtures/` and swap in real upstream output at the next gate.
5. **Subagents inside a lane.** Each lane prompt names the pieces Claude should fan out to parallel subagents.
6. **Background jobs for Gemini.** Long LLM runs are resumable background processes with a call budget. They never block coding.
7. **One integrator.** One person runs both gate prompts on `main`, in plan mode.

### Worktree commands

```bash
# after P0.1 is pushed, from the main clone
for L in A B C D E M; do git worktree add ../clinova-$L -b w1/$L main; done
cd ../clinova-A && cp ../clinova/.env . && claude

# starting a new wave in an existing worktree (worktrees share one repo, so merged main is visible at once)
cd ../clinova-A && git switch -c w2/A main
```

On separate laptops, clone the repo and run `git switch -c w1/A` instead. Point every laptop's `DATABASE_URL` at the same Tiger Cloud service.

### Lane map

| Lane | Wave 1 | Wave 2 | Owns folders | Owns tables |
| --- | --- | --- | --- | --- |
| A | Data ingestion | Learning loop | `ingest/`, `loop/` | trials, trial\_versions, outcomes, predictions, prediction\_scores, feedback |
| B | Criteria parser and embeddings | Model and validation | `parse/`, `model/` | criteria\_rules, trial\_embeddings |
| C | Virtual cohort and code screen | Protocol optimizer | `cohort/`, `simulate/screen.py`, `simulate/diversity.py`, `optimize/` | fixes |
| D | Persona agents | Batch simulation | `simulate/agents.py`, `simulate/runner.py`, `simulate/surrogate.py` | sim\_runs, persona\_outcomes |
| E | App shell on fixtures | API and app wiring | `api/`, `app/` | drafts |
| M | MiroFish (optional, stop at hour 8) | none | `simulate/mirofish_client.py`, `mirofish/` | none |

Shared files change only through the integrator: `clinova/contracts.py`, `clinova/common/`, `db/schema.sql`, `pyproject.toml`, `docker-compose.yml` and `CLAUDE.md`. The API writes feedback only by calling functions in `loop/`.

### How to use each prompt

- Paste the text in each block as the first message of a fresh Claude Code session in the worktree its heading names. Fill every `{{double brace}}` first.
- Turn on plan mode (Shift+Tab) for P0.1 and both gates, and read the plan before approving.
- Every lane ends by running `make check`, committing, pushing, and writing `docs/handoff/<lane>.md`. The integrator reads those handoffs first.

### Timeline

&#91;embedded content: build plan · 6 lanes, 4 waves, 2 gates\]

Lanes D and E finish after Wave 2, so Waves 3 and 4 run three sessions each; Wave 4 is protected and never cut.

## Phase 0: bootstrap and spikes (0:00–0:45)

Four sessions run at once: P0.1 builds the skeleton and contracts in the main clone while three research spikes check the riskiest assumptions in `../clinova-spikes`. Copy each spike's markdown output into `docs/spikes/` once P0.1 is pushed.

### P0.1 Bootstrap: repo, contracts, CLAUDE.md (main clone, plan mode)

```text
You are bootstrapping Clinova, a hackathon project. Read docs/ARCHITECTURE.md end to end first. Your job is NOT to implement components. It is to build the skeleton and the frozen contracts that six parallel Claude Code sessions will build against, so they never collide. Show me a plan, then execute it.

Build:

1. Repo layout exactly as in the architecture's "Repo layout", as a Python 3.11 package `clinova/` managed with uv (pyproject.toml). Subpackages: ingest, parse, cohort, simulate, model, optimize, loop, api, app, common. Plus db/, tests/, tests/fixtures/, docs/, docs/handoff/, docs/spikes/, scripts/. Dependencies: httpx, tenacity, pydantic v2, google-genai, psycopg[binary,pool], pgvector, pandas, pyarrow, numpy, lightgbm, scikit-learn, shap, optuna, fastapi, uvicorn, streamlit, plotly, pyyaml, matplotlib; dev: pytest, pytest-asyncio, hypothesis, ruff, respx.

2. clinova/common/config.py: settings from env via pydantic-settings: CLINOVA_MODE (demo|live, default demo), GEMINI_API_KEY, GEMINI_MODEL_FAST, GEMINI_EMBED_MODEL, EMBED_DIM=768, GEMINI_CONCURRENCY=8, DATABASE_URL, DATA_DIR, SEED=42, and amendment cost constants (Phase II $141,000 and Phase III $535,000 median per substantial amendment, about 3 months delay; source: Regeneron Starter Sheet 01). Check Google's current Gemini API docs for the right model ids and SDK usage before choosing defaults; never hardcode a model id anywhere else.

3. clinova/common/llm.py: the ONLY place in the codebase that calls Gemini. Functions: generate_json(prompt, schema: type[BaseModel] | list-of-model, *, namespace, seed, temperature=0.2) and embed(texts, *, namespace). Structured JSON output using the SDK's response schema support, validated with pydantic, one repair retry on validation failure. Cache every call in a Postgres table llm_cache(key text pk, namespace, model, request jsonb, response jsonb, created_at), key = sha256 of namespace+model+prompt+schema+seed. Demo mode is cache-only: a miss raises CacheMiss and never touches the network. Live mode: asyncio semaphore at GEMINI_CONCURRENCY, exponential backoff with jitter on 429 and 5xx, token usage appended to llm_usage. Provide sync and async variants.

4. clinova/common/db.py: connection pool, get_conn(), and a helper to run db/schema.sql idempotently.

5. db/schema.sql: every table in the architecture's data model, plus llm_cache, llm_usage, drafts (pasted protocols), fixes (optimizer output, jsonb), prediction_scores (hypertable, time = scored_at). Enable timescaledb and vector. Hypertable primary keys must include the time column. trials gets columns for first-version fields, current outcome fields, three label columns (label_accrual_fail, label_shortfall, label_amend, nullable), stop_category, and split ('train'|'test'|'pending'). trial_embeddings uses vector(768) with an HNSW cosine index. The monthly accuracy continuous aggregate goes over prediction_scores, because TimescaleDB cannot build a continuous aggregate over a join of two hypertables; confirm against the TimescaleDB docs. Idempotent: safe to re-run.

6. clinova/contracts.py: pydantic models that every lane imports. Include: TrialRecord; VersionEvent; ClinicalField (an enum: the controlled vocabulary shared by parser, cohort and screen, covering age, sex, ecog, stage, histology, egfr_status, alk_status, ros1_status, pdl1_tps, brain_mets (none|treated_stable|untreated), leptomeningeal, prior_lines, prior_platinum, prior_immunotherapy, crcl_ml_min, anc, platelets, hemoglobin, ast_x_uln, alt_x_uln, bilirubin_x_uln, life_expectancy_months, measurable_disease, autoimmune_disease, interstitial_lung_disease, active_infection, cardiac_condition, pregnancy, other); Operator (lt, le, gt, ge, eq, ne, in, not_in, present, absent); CriteriaRule (rule_id, kind inclusion|exclusion, field, operator, value, unit, raw_text, parse_status ok|unparseable, locked: bool); Visit (visit_no, name, day, procedures, est_hours, requires_travel, invasive, estimated: bool); ParsedProtocol (trial_id, version_no, setting first_line|second_line_plus|any, rules, visits, unparseable); ClinicalTraits; Persona (persona_id, age, sex, race_ethnicity, life context fields, clinical: ClinicalTraits); ScreenResult; Funnel (ordered stages plus per-rule marginal exclusions); AgentDecision (persona_id, enroll, dropout_visit, reason, burden_tags); SimRunSummary (run_id, trial_id, seed, n_screened, eligible_frac, enroll_rate, dropout_curve, top_reasons, model_version); RiskScore (target accrual_fail|shortfall|amend, p, ci_low, ci_high, confidence high|medium|low, top_drivers); CriterionFlag (rule_id, where, why, what_to_do, delta_p); Edit (kind relax_rule|drop_rule|cut_visit|merge_visits|make_remote, target_id, new_value); Fix (fix_id, edits, delta_enroll, delta_dropout, delta_diversity, ranges, dollar_value, precedents); Prediction; Feedback. Use Field descriptions generously: they are the spec.

7. Stub modules with final public signatures, docstrings and `raise NotImplementedError`: ingest.pipeline.run, parse.parser.parse_protocol, parse.embed.embed_trials, parse.embed.nearest, cohort.generate.generate_cohort, simulate.screen.screen, simulate.diversity.diversity_index, simulate.agents.run_agents, simulate.runner.simulate_trial, simulate.surrogate.Surrogate, model.features.build_features, model.predict.score, model.predict.flag_criteria, optimize.search.optimize, loop.ledger.record_prediction, loop.ledger.record_feedback, loop.checker.check_outcomes, loop.replay.run_replay. Each subpackage gets a `__main__.py` CLI stub.

8. Fixtures in tests/fixtures/, hand-written and realistic: one synthetic NSCLC second-line eligibility text and visit schedule; its ParsedProtocol JSON (about 15 rules, 8 visits); 200 Persona records; a ScreenResult and Funnel; 25 AgentDecisions; a SimRunSummary; RiskScores; CriterionFlags; three Fixes. Plus tests/test_contracts.py that round-trips every fixture through its model.

9. docker-compose.yml: db (timescale/timescaledb-ha image; confirm it bundles pgvector) with schema.sql in initdb; api and app services as placeholders; a `mirofish` profile placeholder that is off by default.

10. Makefile targets: up, down, db-up, db-reset, check (ruff, then pytest -q), test. .env.example, .gitignore, MIT LICENSE, a stub README, and a GitHub Actions workflow running `make check` on every push.

11. docs/CONTRACTS.md: plain-English guide to every model, table, signature and fixture.

12. CLAUDE.md, under 120 lines, loaded by every future session. Include: one-paragraph product summary; the lane map and folder/table ownership exactly as follows: A owns ingest/ and loop/ and tables trials, trial_versions, outcomes, predictions, prediction_scores, feedback; B owns parse/ and model/ and tables criteria_rules, trial_embeddings; C owns cohort/, simulate/screen.py, simulate/diversity.py, optimize/ and table fixes; D owns simulate/agents.py, simulate/runner.py, simulate/surrogate.py and tables sim_runs, persona_outcomes; E owns api/, app/ and table drafts; M owns simulate/mirofish_client.py and mirofish/; shared files are integrator-only. Rules: never edit outside your ownership, file a change request in docs/CONTRACT_CHANGES.md instead; all Gemini calls go through common/llm.py; tests never hit the network (use fixtures, respx, or the cache); leakage rule (features only from a trial's first registered version); time split (train starts before 2018-01-01, test starts on or after); honesty rule (report numbers as they are); no real patient data; MIT repo, never copy MiroFish code in (AGPL); Clinova advises, never approves; finish every task with make check, commit, push, and docs/handoff/<lane>.md covering what works, what is stubbed, how to run it, and open issues.

Verify: `make db-up` starts the database, schema.sql applies twice without error, `make check` passes. Commit, push to main, and tag contracts-v1. Finish by printing the worktree commands for lanes A, B, C, D, E and M.
```

### P0.2 Spike: ClinicalTrials.gov and version history (`../clinova-spikes`)

```text
Research spike, about 40 minutes. Write findings to ./ctgov.md and a runnable probe to ./ctgov_probe.py. Do not build the product.

Answer with evidence from real requests:
1. The exact ClinicalTrials.gov API v2 query that returns interventional Phase 2, Phase 3 and Phase 2/3 trials for non-small cell lung cancer. Report the total count, and the count with overall status COMPLETED or TERMINATED.
2. JSON paths for: eligibility criteria text, min/max age, enrollment count and type (ESTIMATED vs ACTUAL), overall status, why stopped, start date, primary completion date, phases, arms and interventions, location list, sponsor class, and first-posted date.
3. Version history. The public website shows a "History of Changes" for each study. Find how to fetch (a) the list of versions with dates and (b) the full record at a given version, for example by watching which endpoint the site itself calls. Test it on 5 trials, report the URL pattern, response shape, and polite rate limit. If no reliable access exists, document the fallback: compare the earliest available record to the current one and state what that loses (first-version enrollment target, eligibility diffs).
4. Using history, find 5 candidate demo trials: NSCLC, Phase 2 or 3, started 2018 or later, TERMINATED with a why-stopped reason about accrual or enrollment, whose eligibility text changed after the start date. For each give the NCT ID, title, start date, why stopped, and a one-line summary of what changed in eligibility and when.
5. Anything that will bite us: pagination, missing fields, rate limiting, date formats.
```

### P0.3 Spike: personas, MiroFish and licenses (`../clinova-spikes`)

```text
Research spike, about 40 minutes. Write findings to ./personas_mirofish.md. Do not build the product.

1. MatrAIx Persona 8B: find the public 1M-persona coreset (paper arXiv 2608.04205; likely on Hugging Face). Report the exact location, license and whether it permits use in an MIT-licensed open-source project, the schema (every field), and how to download a 20,000-row sample to parquet. Download that sample to ./personas_sample.parquet if the license allows. Map its fields to these targets: age, sex, race_ethnicity, occupation, caregiving duties, travel or transport access, income band, risk tolerance, health literacy. If it is unavailable or the license is a problem, find the best permissively licensed alternative persona dataset and document the same things for it.
2. MiroFish-Offline (github.com/nikmcfly/MiroFish-Offline): license, how to run it unmodified with Docker, which LLM providers it supports (and whether Gemini's OpenAI-compatible endpoint works), what inputs it takes, what output it produces and where, and the minimal steps to get one report. Estimate setup time honestly.
3. A licensing table for every dependency and data source in the architecture doc: name, license, obligations, and any risk to an MIT repo.
```

### P0.4 Spike: NSCLC clinical trait distributions (`../clinova-spikes`)

```text
Research spike, about 40 minutes. Write ./clinical_distributions.md and a draft ./assumptions.yaml. Every number needs a cited, linked source (SEER, NCI, published cohort studies, guideline papers). Never invent a number; if you can't find one, say so and propose a labelled assumption.

For US patients with advanced or metastatic NSCLC who are candidates for systemic therapy, give distributions for: age; sex; race and ethnicity; stage at trial-relevant timepoints; histology split; ECOG 0/1/2/3+; EGFR, ALK and ROS1 positivity (with variation by ancestry and smoking history); PD-L1 TPS bands; brain metastases (none, treated and stable, untreated); number of prior systemic lines by setting; creatinine clearance (or age-based estimates via Cockcroft-Gault); and lab abnormalities that commonly breach eligibility thresholds (ANC, platelets, hemoglobin, AST/ALT, bilirubin). Note key correlations (ECOG with age, EGFR with never-smoking and Asian ancestry, CrCl with age). Also find published estimates of the share of real NSCLC patients who meet typical trial eligibility criteria; we will use it as a sanity check on the simulator.
```

## Wave 1: core engine lanes (0:45–4:30)

Six sessions run at once, one per worktree; none waits on another because each builds against the P0.1 fixtures. Before opening them, the integrator copies the spike outputs into `docs/spikes/` (and `assumptions.yaml` into `clinova/cohort/`), commits to main, then creates the worktrees.

### W1-A Data ingestion (`../clinova-A`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (component 1 and the data model), docs/CONTRACTS.md and docs/spikes/ctgov.md. You are lane A and own clinova/ingest/ plus tables trials, trial_versions and outcomes.

Goal: about 1,500 Phase 2/3 interventional NSCLC trials in the database, with time-stamped version events, three labels and a train/test split.

Build first, alone: ingest/ctgov.py, an httpx client with retries, polite rate limiting, and a raw-response disk cache under DATA_DIR/raw so nothing is fetched twice. Then fan out three parallel subagents:
- Subagent 1, pull: search with the spike's query, fetch each current record, map it to TrialRecord.
- Subagent 2, history: fetch each trial's version list and its first registered version; diff consecutive versions on eligibility text, enrollment count and type, primary completion date, overall status and location count; write each change as a VersionEvent. If history access fails, implement the spike's documented fallback and say so in the handoff.
- Subagent 3, labels: (a) stop_category: classify why-stopped text into accrual, safety, efficacy_futility, business_funding, other or unknown with common.llm.generate_json and a pydantic schema; label_accrual_fail = TERMINATED and stop_category == accrual. (b) label_shortfall = actual enrollment below 80% of the first version's anticipated enrollment, only for COMPLETED or TERMINATED trials with an ACTUAL count. (c) label_amend = eligibility text changed after the start date and Gemini classifies the old-vs-new change as widened or mixed (other classes: narrowed, administrative).

Then: ingest/load.py writes everything to the database; the split is train when start date is before 2018-01-01, test on or after; trials not yet ended get split 'pending' and no labels. Features must come from the first version only: store first-version fields separately from current fields and never overwrite them.

Deliverables:
- CLI: `python -m clinova.ingest run [--limit N]`, resumable. Run the full pull as a background process logging to logs/ingest.log, and keep working while it runs.
- docs/labels_spotcheck.csv: 40 random stop-reason classifications for a person to check by hand.
- docs/data_report.md: counts by status, split and label; label prevalence; history coverage; anything odd. If fewer than 800 trials end up labeled, add a --scope solid_tumors option and recommend it.
- docs/demo_trial.md: the 5 best demo-trial candidates from the test split (terminated for accrual, eligibility widened before termination, rich eligibility text), with a reason for each. A person will pick one.
- Tests using recorded API responses (respx), no network.

Finish per CLAUDE.md: make check, commit, push, docs/handoff/A.md.
```

### W1-B Criteria parser and embeddings (`../clinova-B`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (component 2), docs/CONTRACTS.md and the fixtures. You are lane B and own clinova/parse/ plus tables criteria_rules and trial_embeddings.

Goal: free-text eligibility criteria and visit schedules become validated ParsedProtocol objects that the code screen can run.

Fan out three parallel subagents:
- Subagent 1, parser: parse.parser.parse_protocol(eligibility_text, schedule_text=None, *, trial_id, version_no) -> ParsedProtocol via common.llm.generate_json. One rule per atomic condition. Map every rule to the ClinicalField vocabulary and normalize units (CrCl in mL/min; liver labs as multiples of ULN; counts per µL or 10^9/L, stated). Anything that doesn't map goes to field=other with parse_status=unparseable: flag it, never guess. Infer the line-of-therapy setting. After the model returns, run deterministic sanity checks (plausible threshold range per field, operator valid for the field type) and flag violations. Export the JSON schema to parse/schema.json.
- Subagent 2, visits: extract visits from schedule text when given. CT.gov records usually have none, so add estimate_visits(trial_record) that builds a schedule from design fields (cycle length, route, number of arms, imaging cadence typical for the phase), every visit marked estimated=True.
- Subagent 3, gold set and diff: build tests/fixtures/parse_gold/ with 12 real NSCLC eligibility texts (fetch them directly from the CT.gov API if lane A's data isn't in the database yet), annotated by you reading the text, not by Gemini. Write an evaluator reporting field-level precision and recall. Also write parse.diff.diff_rules(old, new) -> list of changes per field (relaxed, tightened, removed, added), which the hit-rate metric needs later.

Then: parse/embed.py with embed_trials(nct_ids) using common.llm.embed at EMBED_DIM, stored in trial_embeddings, and nearest(vector_or_nct, k) using pgvector cosine distance.

Batch CLI: `python -m clinova.parse run --versions first` parses every trial's first version, and `--versions amended` parses the post-change eligibility version for trials with label_amend. Both resumable, run as background processes. Until lane A's trials land, develop against fixtures and the gold set; poll the trials table and start the batch when rows appear.

Report gold-set precision and recall in docs/handoff/B.md. Finish per CLAUDE.md.
```

### W1-C Virtual cohort and code screen (`../clinova-C`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (components 3 and 4), docs/CONTRACTS.md, docs/spikes/personas_mirofish.md, docs/spikes/clinical_distributions.md and clinova/cohort/assumptions.yaml. You are lane C and own clinova/cohort/, simulate/screen.py and simulate/diversity.py.

Goal: a deterministic, seeded virtual NSCLC cohort and a millisecond-fast eligibility screen.

Fan out three parallel subagents:
- Subagent 1, personas: cohort/personas.py loads the persona sample parquet (location per the spike; add a download script at scripts/get_personas.py, not the data itself, if the license requires), maps fields to Persona, and reweights to NSCLC demographics (age, sex, race and ethnicity) from assumptions.yaml by raking or importance resampling. Deterministic for a given seed.
- Subagent 2, clinical traits: cohort/traits.py samples ClinicalTraits conditional on persona demographics and the trial setting (first_line vs second_line_plus), using assumptions.yaml and the documented correlations (ECOG with age, EGFR with ancestry and smoking, CrCl from age, sex and sampled weight and creatinine via Cockcroft-Gault). Every distribution reads from assumptions.yaml, where each number carries its source. Then cohort.generate.generate_cohort(n, seed, setting) -> list[Persona], plus a dataframe form for speed.
- Subagent 3, screen: simulate/screen.py evaluates CriteriaRules against the cohort dataframe, vectorized with numpy. Skips unparseable rules and reports them. Returns ScreenResults and a Funnel holding both the sequential funnel and each rule's marginal exclusions (patients excluded by that rule alone). Add apply_edits(rules, edits) -> rules for the optimizer. Target: 10,000 personas x 25 rules in under 50 ms; add a benchmark test. Also simulate/diversity.py: diversity_index(eligible_df) comparing the eligible cohort's age, sex and race/ethnicity mix to the NSCLC reference distribution (1.0 = matches the reference).

Tests: hypothesis property tests (relaxing a threshold never shrinks the eligible pool; an empty rule set makes everyone eligible), plus a golden test on the fixture protocol. Write docs/cohort_sanity.md comparing the fixture protocol's eligible fraction with the published eligibility shares from the spike. Report the comparison honestly. Finish per CLAUDE.md.
```

### W1-D Persona agents (`../clinova-D`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (component 5), docs/CONTRACTS.md and the fixtures. You are lane D and own simulate/agents.py, simulate/runner.py and simulate/surrogate.py plus tables sim_runs and persona_outcomes.

Goal: eligible virtual patients act as Gemini agents who decide whether to enroll and when they would drop out, batched, seeded and cached.

1. simulate/agents.py: build a plain-language trial offer from a ParsedProtocol (what the trial is, randomization, each visit's day, procedures, hours and travel, total duration). run_agents(personas, protocol, seed) sends batches of 25 personas per call through common.llm.generate_json and returns validated AgentDecisions. Each persona block gives life context plus a short clinical summary. The prompt asks each person to decide as that person would, with a concrete reason and burden tags (travel, time off work, caregiving, procedure fear, side effects, cost, distrust, feeling too unwell). Don't tell the model any base rates or outcomes. Retry personas missing from a response. Async, batches run concurrently.
2. simulate/runner.py: simulate_trial(protocol, n=1000, seeds=(1,2), max_agents=200) generates a cohort (via lane C's generate_cohort; use the fixture personas and a fixture screen until it lands), screens, samples up to max_agents eligible personas, runs agents, and aggregates into SimRunSummary (eligible_frac, enroll_rate, dropout_curve by visit, median dropout visit, top reasons). Writes sim_runs and persona_outcomes. Multiple seeds give a mean and spread.
3. Behavior check, the most important test: write three contrasting fixture protocols (light: 6 visits, no biopsy; medium; heavy: 20 visits, 2 mandatory biopsies, long infusions, weekly travel). Run live with 2 seeds each and record in docs/agent_sanity.md whether enroll rate falls and dropout rises with burden. If they don't, iterate on the prompt (persona specificity, asking for reasoning before the decision) and log what changed. Report honestly.
4. simulate/surrogate.py: a fast surrogate (logistic or small LightGBM) fit on persona_outcomes that predicts enroll and dropout from visit-burden features and persona traits, so the optimizer can re-score edits in milliseconds without Gemini. Fit it on whatever runs exist; it gets refit at Gate 2.
5. CLI: `python -m clinova.simulate batch --n-trials N --seeds 2 --max-calls M --dry-run` prints the planned call count and estimated tokens before spending anything.

Finish per CLAUDE.md, including the measured cost per simulated trial in the handoff.
```

### W1-E App shell on fixtures (`../clinova-E`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md ("User experience and demo flow"), docs/CONTRACTS.md and the fixtures. You are lane E and own clinova/api/ and clinova/app/.

Goal: the full user experience running end to end on fixture data, so the real engine drops in at the gates without UI work.

In parallel subagents:
- API: FastAPI app in api/main.py with response models from contracts.py. Routes: POST /protocols/parse, POST /runs (start a full run, returns run_id), GET /runs/{run_id} (status plus results), GET /trials/{nct_id}/backtest, POST /optimize, POST /feedback, GET /validation, GET /replay, GET /pulse/{nct_id}, GET /audit. Each route calls the real module and falls back to fixtures when it raises NotImplementedError, with a `source: fixture|live|cache` field in every response. TestClient tests for every route.
- App: Streamlit in app/, talking to the API only through app/client.py. Input: paste eligibility text and visit schedule, or enter an NCT ID ("loads the protocol as originally registered"). Parsed rules in an editable table (field, operator, value, unit, raw text, lock toggle, unparseable rows highlighted), then a Run Clinova button. One results screen with three panels: Funnel (plotly funnel of screened, then excluded by each criterion, enrolled, completing, with the biggest exclusion highlighted); Risk (three cards with probability, range, confidence badge and top drivers, each flag saying where, why and what to do); Fixes (cards with effects on enrollment speed, dropout and diversity, ranges, dollar value of the avoided amendment, precedent trials, Accept and Reject buttons calling /feedback). Tabs: Validation, Replay, Community Pulse, Audit log, reading fixture JSON for now.

Design: calm and clinical, one accent color, readable at 1920x1080 for screen recording. Wording always frames output as suggestions for the protocol author; Clinova never approves a protocol. Run the app, screenshot every view with Playwright into docs/screens/, look at the screenshots, and fix what looks off. Finish per CLAUDE.md.
```

### W1-M MiroFish deep-dive, optional (`../clinova-M`)

```text
Read CLAUDE.md and docs/spikes/personas_mirofish.md. You are lane M and own clinova/simulate/mirofish_client.py and mirofish/. This lane is optional and has a hard stop: if there is no cached report by hour 8, write the handoff saying it is cut and stop.

MiroFish is AGPL-3.0. Never copy its code into this repo. Clone and run it unmodified in ../mirofish-service, outside the repo. mirofish/ holds only our compose override, config templates and a README on how to start it.

1. Get MiroFish running locally per the spike, with the cheapest working LLM configuration (Gemini through its OpenAI-compatible endpoint if supported).
2. Add a `mirofish` profile to a compose override in mirofish/ that is never started by default.
3. simulate/mirofish_client.py talks to the running service over HTTP or files only. It sends a seed pack: plain-language protocol summary, consent summary, visit schedule, and 20 personas from the fixture cohort. It requests a simulation of patients and site coordinators discussing the trial, then collects the narrative report on perceived burden.
4. Save output to the llm_cache table under namespace mirofish plus a markdown copy in docs/pulse/<trial_id>.md, so the Community Pulse tab works without MiroFish running.
5. Run it on the fixture protocol now and on the real demo trial once it is chosen.

Finish per CLAUDE.md, with setup time and reliability noted in the handoff.
```

## Gate 1: integrate and run the demo trial (4:30–5:30)

Gate 1 passes when the real demo trial runs end to end from its first registered protocol: parse, cohort, screen, agents, summary. Before running it, a person picks the demo trial from `docs/demo_trial.md`, spot-checks `docs/labels_spotcheck.csv`, and leaves lanes A and B's background jobs running (they don't need to finish). Lane M keeps going until hour 8.

### G1 Integrate (main clone, plan mode)

```text
You are the integrator at Gate 1. Read CLAUDE.md, every docs/handoff/*.md, docs/CONTRACT_CHANGES.md, docs/data_report.md and docs/agent_sanity.md. Plan, then execute.

1. Merge branches w1/A, w1/B, w1/C, w1/D and w1/E into main, in that order. Resolve conflicts by ownership: the owning lane's version wins in its folders. Apply each request in docs/CONTRACT_CHANGES.md to the shared files, bump the contracts version, mark the request done, and re-run schema.sql. Run make check after each merge and fix breakage at once.
2. Swap fixtures for real modules wherever both sides now exist: the runner uses the real cohort and screen; the API and app call real parse, screen and simulate.
3. Write scripts/e2e_demo.py. For demo trial {{DEMO_NCT_ID}}: load its first registered version from the database, parse it, generate 1,000 personas for its setting, screen, run agents live with 2 seeds, and store the run. Run it.
4. Write docs/gate1_report.md with: the funnel numbers and which criterion excludes the most patients; eligible fraction against the published sanity range; enroll rate and dropout curve; whether the criterion the sponsor later widened (from trial_versions) is among the top 3 exclusions, stated honestly either way; data counts (trials, labeled, parsed, embedded) and the background jobs' progress; and every output that looks wrong, with a suspected cause.
5. Launch a review subagent with the U1 reviewer brief on the merged main: leakage, ownership violations, untested logic, silent fallbacks. Fix anything cheap; list the rest in the report.
6. Push main, tag gate-1, and print the Wave 2 worktree commands (git switch -c w2/<lane> main in worktrees A to E).
```

## Wave 2: model, optimizer, loop, API (5:30–10:00)

Five sessions run at once; start W2-D first because the batch simulation is the longest job and the model's simulation features depend on it. The model, optimizer and loop lanes start on non-simulation features and stub scorers, then swap in real inputs as they land.

### W2-D Batch simulation (`../clinova-D`, start first)

```text
Read CLAUDE.md, docs/handoff/D.md and docs/gate1_report.md. You are lane D for Wave 2.

1. Choose about 400 trials for simulation: stratified by split and by each label so positives are well represented, including every test-split trial with label_amend and the demo trial. Save the list to data/sim_sample.csv with the sampling rule written in docs/handoff/D.md.
2. Dry-run the cost, print it, and keep total calls under {{MAX_GEMINI_CALLS}}. Then launch `python -m clinova.simulate batch` on the sample with 2 seeds each as a resumable background process logging to logs/sim.log. Run the demo trial and 2 backup trials with 5 seeds each, for uncertainty ranges.
3. While it runs: add a sim_features view (one row per trial: eligible_frac, enroll_rate, dropout at visits 2, 4 and last, dropout-curve area, top burden-reason shares, seed spread) that lane B reads. Keep a progress line in docs/handoff/D.md (done, failed, calls used) and retry failures at the end.
4. Once at least 300 trials are done, refit simulate/surrogate.py on all persona_outcomes and report its held-out accuracy. Expose Surrogate.score(protocol, cohort_df) -> (enroll_rate, dropout_risk) in under 20 ms.

Finish per CLAUDE.md.
```

### W2-B Model and validation (`../clinova-B`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (component 6 and "Validation and metrics") and docs/gate1_report.md. You are lane B for Wave 2 and own clinova/model/.

Goal: calibrated, explained risk scores for accrual failure, shortfall and amendment, validated on trials the model never saw, with every metric reported honestly.

Fan out parallel subagents:
- Features: model/features.py builds one row per trial from first-version data only. Protocol: rule counts by kind; strictness per field (for example max ECOG allowed, min CrCl, brain-mets policy, max prior lines), each relative to the training-set median; unparseable count; text length. Visits: count, estimated hours, invasive procedures, travel. Design: phase, arms, masking, allocation, intervention types, first-version enrollment target. Operations: sites, countries, US share, sponsor class. Embeddings: PCA to 16 dimensions, fit on train only. Simulation: join sim_features, left as NaN when a trial wasn't simulated. Exclude start year and anything observed after the first version. Add a test that rebuilds features from first-version JSON alone and matches.
- Training: model/train.py fits one LightGBM per label on the train split, tuned with time-ordered CV inside train (for example fit before 2015, validate 2015 to 2017). Isotonic calibration on out-of-fold predictions. A logistic-regression baseline on 10 interpretable features. 30 bootstrap models per label for ranges. Saves a versioned bundle under models/<version>/.
- Metrics: model/evaluate.py computes on the test split: ROC AUC with bootstrap 95% CI for LightGBM and baseline, Brier score, a 10-bin reliability plot, and the simulation ablation (AUC with vs without simulation features, on the simulated test subset only, paired bootstrap for the difference). Writes docs/metrics.json and PNGs in docs/figures/.
- Explanations and flags: SHAP TreeExplainer, a global importance plot, and per-trial top drivers with readable names. model.predict.flag_criteria(protocol) ranks each rule by the drop in predicted accrual-failure probability when that rule is relaxed one step or removed (re-screen the cached cohort, rebuild the features, re-score). Each CriterionFlag says where, why and what to do. Hit rate: among test trials with label_amend, the share where a relaxed or removed field from parse.diff.diff_rules is in our top 3 flags. Report it against two baselines: 3 random rules, and the 3 rules with the largest marginal exclusion.
- Confidence: a badge from the protocol's embedding distance to its 10 nearest training trials, judged against the training set's own distance distribution (above the 90th percentile is low).

model.predict.score(protocol_or_trial) returns RiskScores with ranges that combine bootstrap spread and simulation-seed spread. Expose model.train.train(df, cfg) -> bundle for lane A's replay. Start immediately on non-simulation features; add simulation features when sim_features has 300 or more rows, and retrain. Write the metric numbers and an honest one-paragraph reading into docs/handoff/B.md. Finish per CLAUDE.md.
```

### W2-C Protocol optimizer (`../clinova-C`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md (component 8 and the Tiger example query) and docs/CONTRACTS.md. You are lane C for Wave 2 and own clinova/optimize/ and table fixes.

Goal: a few trade-off fixes per protocol, each with ranges, a dollar value and precedent trials, in under 10 seconds.

Define a Scorer protocol (eligible_frac, enroll_rate, dropout_risk, p_shortfall, p_amend for a rule set and visit list) and build against a stub until lane B's model.predict and lane D's Surrogate land, then swap.

Fan out parallel subagents:
- Edit space: optimize/edits.py generates candidate Edits from a ParsedProtocol. Relax ladders come from thresholds other NSCLC trials actually used, read from criteria_rules (for example CrCl 60 to 50 to 45 to 30; ECOG up to 1 to up to 2; untreated brain mets excluded to treated-and-stable allowed). Also drop rule, cut visit, merge adjacent visits, and make a visit remote when its procedures allow. Locked rules are never touched; pregnancy and consent-capacity rules are locked by default, and the author can lock more.
- Search: optimize/search.py runs Optuna NSGA-II with at most 3 edits per candidate. Objectives: enrollment speed (eligible_frac x surrogate enroll_rate, adjusted by p_shortfall), dropout risk (surrogate), and diversity (simulate.diversity on the edited eligible pool). Each evaluation re-screens a cached cohort, so it takes milliseconds. Return the Pareto front, deduplicated, and pick 3 representative fixes: fastest enrollment, lowest dropout, best balance.
- Ranges and money: re-evaluate the chosen fixes across 5 cohort seeds and the bootstrap models for ranges. Dollar value = drop in p_amend x the median amendment cost for the trial's phase from config, labelled as expected value.
- Precedents: optimize/precedents.py finds comparable NSCLC trials (pgvector nearest neighbours) that used the looser threshold and completed. Also answer the architecture's example query: among comparable trials with the original threshold, the share that later widened it and the median months after start (from trial_versions).

Optionally re-simulate the top 3 fixes with fresh persona agents (live mode only; cached for demo). Save fixes to the fixes table. CLI: `python -m clinova.optimize --trial {{DEMO_NCT_ID}}`. Tests plus a speed benchmark. Finish per CLAUDE.md.
```

### W2-A Learning loop (`../clinova-A`)

```text
Read CLAUDE.md and docs/ARCHITECTURE.md (component 9, the data model and "Validation and metrics"). You are lane A for Wave 2 and own clinova/loop/ plus tables predictions, prediction_scores, outcomes and feedback.

Use lane B's model.train.train(df, cfg) when it lands; until then use a small logistic placeholder with the same signature and swap it in.

Fan out parallel subagents:
- Ledger and feedback: loop/ledger.py with record_prediction (every score, with model_version and timestamp) and record_feedback (fix accept/reject, persona realism ratings, comment, user), plus an audit_log view joining both for the Audit tab.
- Outcome checker: loop/checker.py re-fetches status for 'pending' trials through ingest/ctgov.py, writes outcomes when a trial ends (observed_at = completion or termination date), applies the labelling rules, scores matching predictions into prediction_scores, and triggers retraining when at least {{RETRAIN_MIN_NEW}} new outcomes arrive. CLI `python -m clinova.loop check`, and a `loop` compose profile that runs it on a schedule.
- Replay: loop/replay.py runs the time machine. Freeze at 2019-12-31: train on trials whose outcome was observed by then, first-version features only. Then step monthly from 2020-01 to 2023-12. Each month, score trials whose outcome will be observed in the next 6 months with the current model and record those predictions with predicted_at set to that month. Then add that month's newly observed outcomes and retrain (quarterly is fine if monthly is slow). Run the same windows with a frozen, never-retrained model for comparison. Output docs/replay.json (monthly AUC and Brier for both, with counts) and docs/figures/replay.png. Write replay predictions into the ledger so the continuous aggregate shows them, and compute AUC in Python since the aggregate can't. If retraining doesn't beat the frozen model, say so plainly.

Finish per CLAUDE.md.
```

### W2-E API and app wiring (`../clinova-E`)

```text
Read CLAUDE.md, docs/handoff/E.md and the other lanes' handoffs. You are lane E for Wave 2.

1. Make POST /runs run the real pipeline as a background job: parse (or load the first version for an NCT ID), cohort, screen, agents, model score, flags, optimize. Report progress by stage so the app can show it. Every prediction goes through loop.ledger.record_prediction.
2. GET /trials/{nct_id}/backtest returns the first-version run plus the version-history events for the reveal: when and how the sponsor changed eligibility, enrollment target and status.
3. Accepting a fix calls loop.ledger.record_feedback, applies the edits, and re-runs screen, scoring and surrogate (fast path, no new Gemini calls), then returns before-and-after numbers for the app.
4. GET /validation reads docs/metrics.json and figures; GET /replay reads docs/replay.json; GET /pulse reads lane M's cached report; GET /audit reads the audit view.
5. App: wire everything; add a progress view during runs, a reveal timeline for the backtest, and a before-and-after comparison after accepting a fix. Keep the fixture fallback and show a small "fixture data" tag whenever it is used, so nobody mistakes a fixture for a result.

Re-take the Playwright screenshots, review them, fix what looks wrong. Finish per CLAUDE.md.
```

## Gate 2: freeze the model and lock the numbers (10:00–11:00)

Gate 2 passes when model v1.0 is frozen and `docs/metrics.json`, `docs/replay.json` and the demo trial's fixes are final; every slide, README line and video claim is copied from those files from here on. Lane M's go/no-go was decided at hour 8.

### G2 Freeze (main clone, plan mode)

```text
You are the integrator at Gate 2. Read CLAUDE.md, every docs/handoff/*.md, docs/CONTRACT_CHANGES.md and docs/gate1_report.md. Plan, then execute.

1. Merge w2/D, w2/B, w2/C, w2/A and w2/E into main in that order, by the same ownership rules as Gate 1. Apply contract change requests. make check after each merge.
2. Replace every remaining stub scorer with the real one (model.predict and Surrogate in the optimizer; model.train in the replay). Grep for NotImplementedError and fixture fallbacks on the live path, and list any left.
3. Final training: refit the surrogate on all persona_outcomes; retrain all three models with simulation features; run evaluate; freeze as model version v1.0. Re-run the replay with the real trainer. Run the optimizer and flags on {{DEMO_NCT_ID}} and the two backups.
4. Leakage and integrity checks, each as a test: features rebuild from first-version JSON alone; the demo trial and backups are in the test split; no test-split trial appears in training data, PCA fit or calibration; label prevalence per split matches docs/data_report.md; calibrated probabilities stay within [0, 1] and the reliability curve is monotone enough to be credible.
5. Launch a review subagent acting as a skeptical Regeneron judge with a biostatistics background. Give it docs/metrics.json, docs/replay.json, the figures and the methods, and ask for the 5 weakest points in our evidence. Fix the cheap ones; for the rest, write the plain-language limitation we'll show.
6. Write docs/gate2_report.md: a table of AUC (LightGBM vs baseline, with CIs), Brier, hit rate vs both baselines, ablation difference with CI, and replay result, plus one honest sentence on each; whether the demo trial's widened criterion is in our top 3 flags; the demo trial's 3 fixes with numbers; and what to cut, per the cut order in ARCHITECTURE.md, given the time left.
7. Push main, tag gate-2 and model-v1.0, and print the Wave 3 worktree commands (w3/A, w3/B, w3/C).
```

## Wave 3: make it run anywhere (11:00–14:00)

Three sessions turn the working engine into something a judge can run with one command and no API key; a short merge prompt closes the wave. Ownership in Wave 3 is by task, not module: A owns Docker, seed and demo-mode files; B owns `app/`; C owns `tests/`, CI and `docs/VALIDATION.md`.

### W3-A Demo mode, seed data, Docker, Tiger Cloud (`../clinova-A`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md ("Tech stack, repo layout and readiness") and docs/gate2_report.md. You own docker-compose.yml, Dockerfiles, db/seed/ and scripts/ for this wave (integrator-approved exception).

1. Warm the cache: in live mode, run the full app flow (parse, run, optimize, accept each of the 3 fixes) for {{DEMO_NCT_ID}}, the two backups, and the fixture draft protocol. Then prove demo mode reproduces identical results with CLINOVA_MODE=demo and no GEMINI_API_KEY, with a test that fails on any network call.
2. Seed: export the tables demo mode needs (trials, trial_versions, criteria_rules, trial_embeddings, sim_runs and persona_outcomes for the demo trials, sim_features, predictions, prediction_scores, outcomes, fixes, llm_cache, an empty feedback table) plus models/v1.0. Compress into db/seed/. If it's over 90 MB, publish it as a GitHub Release asset and add `make seed` to download and verify a checksum.
3. Dockerfiles for api and app (python:3.11-slim, uv, non-root). Compose: db loads schema plus seed on first start; api and app with healthchecks and depends_on; loop and mirofish behind profiles, off by default. Target: a clean clone runs `docker compose up` and the app is ready at localhost:8501 in under 3 minutes.
4. Tiger Cloud: `make tiger-load` loads schema and seed into the service in TIGER_DATABASE_URL, and the stack runs against it by setting DATABASE_URL. Use Tiger Cloud for the live demo and the video; judges use local TimescaleDB.
5. Test the clean path yourself: clone into a temp folder, unset every key, docker compose up, open the app, run the demo trial. Fix everything that breaks, and record the time.

Finish with make check, commit, push, docs/handoff/W3-A.md.
```

### W3-B App polish on real outputs (`../clinova-B`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md ("User experience and demo flow", including the 3-minute video script) and docs/gate2_report.md. You own clinova/app/ for this wave.

Make the app ready to record, against the real v1.0 outputs for {{DEMO_NCT_ID}}:
- The 0:20 to 1:10 beat: loading the NCT ID shows "protocol as originally registered on <date>"; the funnel visibly stalls on one criterion (highlighted, with patient counts); risk shows high with a range and the top drivers in plain words.
- The 1:10 to 1:30 reveal: a version-history timeline marks when the sponsor widened that exact rule, and when the trial was terminated.
- The 1:30 to 2:10 beat: three fix cards with trade-offs, ranges, dollar values and precedent trials; accepting one re-runs fast and shows before and after.
- Validation tab: AUC vs baseline with CIs, reliability plot, hit rate vs baselines, ablation, each with one plain-English sentence. Replay tab: the replay chart, frozen vs retrained. Community Pulse tab: lane M's report, or a clean "not run" state. Audit tab: the feedback log.
- Every flag reads as where, why and what to do, with its dollar figure. A footer states that Clinova advises and the protocol author decides.
- Loading, empty and error states everywhere; nothing shows a stack trace. Numbers formatted consistently (percent with no more than 1 decimal, dollars rounded to $1K).

Take Playwright screenshots of every view at 1920x1080 into docs/screens/, review them critically as a designer would, and fix what you find. Keep the best five for the deck. Finish with make check, commit, push, docs/handoff/W3-B.md.
```

### W3-C Tests, CI and validation evidence (`../clinova-C`)

```text
Read CLAUDE.md, the GxP starter sheet in docs/ if present, and docs/ARCHITECTURE.md (readiness checklist). You own tests/, .github/ and docs/VALIDATION.md for this wave; open a contract change request for anything outside.

1. Fill gaps so pytest covers, at minimum and without network: parser schema validation and sanity checks; screen logic (property tests and golden funnel); diff_rules; metric code (AUC, Brier, reliability bins and hit rate on small hand-computed examples); leakage checks; demo-mode no-network; and API routes.
2. CI: ruff, pytest, and a demo-mode smoke test that builds the images and hits /runs for the demo trial with docker compose in GitHub Actions. Keep it under 10 minutes. Add a passing badge to the README stub.
3. docs/VALIDATION.md, GxP-style at hackathon scale: intended use and what Clinova never does; a requirements-to-tests traceability table (requirement, where it is implemented, which test proves it); the audit trail (predictions versioned by model_version; accept/reject decisions logged); data provenance (ClinicalTrials.gov pull date, labelling rules, spot-check results); and known limitations from ARCHITECTURE.md.

Finish with make check, commit, push, docs/handoff/W3-C.md.
```

### G3 Merge (main clone)

```text
Merge w3/A, w3/B and w3/C into main. Resolve conflicts, run make check, then repeat W3-A's clean-clone test from a fresh temp folder with no API keys. Fix anything that breaks. Push, tag v1.0-rc, and print the Wave 4 worktree commands (w4/A, w4/B, w4/C).
```

## Wave 4: package and present (14:00–16:00, protected)

Three sessions produce the README, the 12-slide deck and the video script at once, all copying numbers from `docs/metrics.json`, `docs/replay.json` and `docs/gate2_report.md`. People record the video while the final ship check runs.

### W4-A README and quickstart (`../clinova-A`)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md, docs/gate2_report.md, docs/metrics.json, docs/VALIDATION.md and every handoff. You own README.md and docs/ for this wave.

Write README.md for a judge with 2 minutes:
1. One-line pitch and a hero screenshot from docs/screens/.
2. Quickstart: clone, docker compose up, open localhost:8501, click the demo trial. No API key. State the measured startup time.
3. Live mode: GEMINI_API_KEY and an optional Tiger Cloud DATABASE_URL.
4. What it does in five steps, and an architecture diagram as a Mermaid block matching the real code.
5. Results: a table of every metric with CIs and baselines, copied exactly from docs/metrics.json and docs/replay.json, plus the backtest result for the demo trial. If a number is unflattering, keep it and explain it in one sentence.
6. How we validate (time split, leakage rule, hit-rate definition) in under 150 words, linking docs/VALIDATION.md.
7. Limitations and responsible use, from ARCHITECTURE.md.
8. Licensing: MIT for this repo; MiroFish AGPL-3.0 run as a separate, unmodified service and not included; data sources and the persona dataset license.
9. Repo map and how to run tests.

Then verify every command in the README from a fresh temp clone with no keys, exactly as written. Fix the README or the code until each one works. Finish with commit, push, docs/handoff/W4-A.md.
```

### W4-B 12-slide deck (`../clinova-B`)

```text
Read docs/ARCHITECTURE.md ("Submission package" lists the 12 slides), docs/gate2_report.md, docs/metrics.json, docs/replay.json and the Regeneron judging criteria if present in docs/. Use the screenshots in docs/screens/ and figures in docs/figures/. You own docs/deck/.

1. Write docs/deck/outline.md: for each of the 12 slides, a title that states the slide's point as a sentence, at most 3 short supporting lines, the visual to use, and 60 to 90 words of speaker notes. Every number must come from the files above, with the file named in the notes. The problem slides use the figures and citations from ARCHITECTURE.md.
2. Make the charts the deck needs that don't exist yet (for example AUC vs baseline with CI whiskers; hit rate vs both baselines) as 1920x1080-friendly PNGs in docs/deck/img/, with large type and one accent color.
3. Build docs/deck/clinova.pptx with python-pptx: 16:9, clean and consistent, generous whitespace, no more than 3 text lines per slide, speaker notes included. Render every slide to PNG (LibreOffice headless), look at each one, and fix overflow, clipping and misalignment until it looks professional. Export a PDF too.
4. Map each slide to the judging criterion it serves (Utility and Relevance, Readiness, Design and Packaging) in a short table at the end of outline.md.

Finish with commit, push, docs/handoff/W4-B.md.
```

### W4-C Video script and submission text (`../clinova-C`)

```text
Read docs/ARCHITECTURE.md (the 3-minute video script table and the pitch), docs/gate2_report.md and the app in clinova/app/. You own docs/video/ and docs/submission/.

1. docs/video/script.md: a two-column shot list for exactly 3:00, following the architecture's six beats. For each beat: time range, the exact clicks and what is on screen, and word-for-word narration paced at about 140 words per minute. Every number spoken must match docs/metrics.json or the demo run.
2. scripts/demo_reset.sh: resets the app and database to a clean pre-demo state (clears feedback and runs, keeps the cache) so every take starts the same way.
3. docs/video/checklist.md: recording setup (1920x1080, browser zoom, hidden bookmarks, notifications off, Tiger Cloud connection checked, a dry run with a stopwatch) and a fallback plan if live mode fails mid-take (switch to demo mode, which is identical).
4. docs/submission/devpost.md: project name, tagline, inspiration, what it does, how we built it (naming Gemini, Tiger Data, MatrAIx and MiroFish where used), challenges, accomplishments with the real metrics, what we learned, what's next (from the roadmap), and built-with tags.

Finish with commit, push, docs/handoff/W4-C.md.
```

### G4 Ship check (main clone)

```text
Merge w4/A, w4/B and w4/C into main. Then check the readiness checklist in docs/ARCHITECTURE.md item by item and show evidence for each: the command you ran and its output, or the file and line. Specifically: a fresh clone with no keys runs docker compose up to a working app; demo mode makes zero network calls; CI is green on main; LICENSE is MIT and no MiroFish code is in the repo (search for it); every number in the README, the deck outline, the video script and devpost.md matches docs/metrics.json and docs/replay.json exactly (write a script that extracts and compares them). Fix any mismatch at its source. Push, tag v1.0, create a GitHub Release with the seed asset if one is needed, and print the final repo URL and a checklist of what a person still has to do by hand (record the video, upload it, submit).
```

## Utility prompts (any time)

Five short prompts handle the things that go wrong mid-hackathon; run them in a fresh session so they don't inherit a stuck session's assumptions.

### U1 Reviewer (read-only, any worktree)

```text
You are a read-only reviewer; don't edit files. Read CLAUDE.md and docs/CONTRACTS.md, then review `git diff main...{{BRANCH}}`. Report findings ranked by severity, each with file:line and a one-line fix: leakage (any feature from after a trial's first version, or test-split data touching training, PCA or calibration); edits outside the lane's ownership; direct Gemini calls outside common/llm.py; tests that hit the network; silent fallbacks to fixtures on the live path; logic without a test; numbers in docs that don't come from a file the code writes; anything that overstates results. End with the 3 changes that matter most.
```

### U2 Unstick (the stuck lane's worktree)

```text
Stop and don't write code yet. The current problem is: {{WHAT_IS_FAILING}}. Summarize what has been tried and what each attempt showed. List 3 hypotheses for the root cause, ranked by likelihood, and for each the smallest experiment that would confirm or rule it out. Run the experiments in that order, then fix the confirmed cause. If none is confirmed in 20 minutes, propose the simplest fallback that keeps the lane's contract intact (per CLAUDE.md and ARCHITECTURE.md "Cut if behind"), write it in docs/handoff/<lane>.md, and implement it.
```

### U3 Cut decision (main clone)

```text
It is hour {{HOUR}} of 16. Read every docs/handoff/*.md, the latest gate report and the cut order in docs/ARCHITECTURE.md (MiroFish, then React UI, then live mode; packaging is protected). For each lane, state done, at risk or behind, with evidence. Recommend exactly what to cut or simplify now so a working, honest product exists by hour 14, and what each cut costs in the demo or the deck. Output a short decision table, then the prompt changes each affected lane needs.
```

### U4 Contract change (two steps)

```text
[In the requesting lane] I need a change to a shared file: {{CHANGE}}. Don't edit it. Append a request to docs/CONTRACT_CHANGES.md with: lane, file, exact proposed diff, why, and which lanes it affects. Meanwhile adapt locally behind a small shim in your own folder so you're not blocked, and mark the shim for removal.

[In the main clone] Apply the open requests in docs/CONTRACT_CHANGES.md that are safe. Update docs/CONTRACTS.md, bump the contracts version, re-run schema.sql if needed, run make check, push, and mark each request done. Print the one-line message to send each affected lane: "git fetch && git rebase main, then remove your shim".
```

### U5 Background job status (any worktree)

```text
Check every background job for this lane (logs/*.log and the database). For each: rows or items done, failed, remaining, Gemini calls and tokens used against the budget, and an ETA from the current rate. If a job has died, find out why from the log, fix it, and resume it from where it stopped without redoing finished work. Update the progress line in docs/handoff/<lane>.md.
```
