-- Clinova database schema (Tiger Cloud: PostgreSQL + TimescaleDB + pgvector)
-- Source of truth: docs/ARCHITECTURE.md section 6 (contracts-v1).
--
-- ONLY THE INTEGRATOR EDITS THIS FILE. Task agents that need a schema change
-- file a request under docs/contract_changes/ instead of editing here.
--
-- Owner task per object (owner = the task that writes the rows):
--   trials ............................ T11
--   trial_versions (hypertable) ....... T11
--   criteria_rules .................... T12
--   trial_embeddings (pgvector) ....... T12
--   sim_runs (hypertable) ............. T14 / T24
--   persona_outcomes .................. T14 / T24
--   sim_features (view) ............... T24 (created by T24, NOT here)
--   predictions (hypertable) .......... T21
--   outcomes (hypertable) ............. T11 initial load, T21 updates
--   prediction_scores (hypertable) .... T21
--   prediction_scores_monthly (cagg) .. T21
--   feedback (hypertable) ............. T21
--   fixes ............................. T23
--   drafts ............................ T25
--   llm_cache ......................... T00 (shared)
--   llm_usage (hypertable) ............ T00 (shared)
--   schema_meta ....................... T00 (integrator)
--
-- Rules for this file:
--   * Idempotent: every statement is IF NOT EXISTS / if_not_exists / ON CONFLICT.
--     It is applied to a shared database, never DROP anything here.
--   * No DO blocks or dollar-quoted bodies, and no semicolon inside comments or
--     literals, so it can run as one multi-statement string (autocommit) or be
--     split on a semicolon at end of line.
--   * Hypertable primary keys include the time (partitioning) column.
--   * Chunk intervals are wide on purpose: hackathon volumes are small and several
--     tables hold historical dates (2000s-2020s), so 7-day chunks would mean
--     thousands of near-empty chunks.
--   * Verified against TimescaleDB 2.30.2 / PostgreSQL 18.6 / pgvector 0.8.7.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;

-- schema_meta (T00): schema version marker
CREATE TABLE IF NOT EXISTS schema_meta (
    key   text PRIMARY KEY,
    value text
);
INSERT INTO schema_meta (key, value) VALUES ('schema_version', 'contracts-v1')
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;

-- trials (T11): one row per trial, frozen first version plus current record
CREATE TABLE IF NOT EXISTS trials (
    nct_id                   text PRIMARY KEY,
    title                    text,
    phase                    text CHECK (phase IN ('phase2', 'phase3', 'phase2_3')),
    start_date               date,
    primary_completion_date  date,
    completion_date          date,
    first_posted             date,
    first_version_date       date,
    status                   text,
    why_stopped              text,
    stop_category            text CHECK (stop_category IN ('accrual', 'safety', 'efficacy_futility', 'business_funding', 'other', 'unknown')),
    planned_enrollment       integer,
    actual_enrollment        integer,
    sponsor_class            text,
    lead_sponsor             text,
    n_sites                  integer,
    n_countries              integer,
    n_arms                   integer,
    allocation               text,
    masking                  text,
    intervention_types       text[],
    conditions               text[],
    min_age_years            real,
    max_age_years            real,
    eligibility_text_first   text,
    eligibility_text_current text,
    first_version            jsonb,
    current                  jsonb,
    label_accrual_fail       boolean,
    label_shortfall          boolean,
    label_amend              boolean,
    split                    text CHECK (split IN ('train', 'test', 'pending')),
    history_source           text CHECK (history_source IN ('versions', 'earliest_vs_current')),
    updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trials_split_idx ON trials (split);
CREATE INDEX IF NOT EXISTS trials_status_idx ON trials (status);

-- trial_versions (T11): field-level change history, hypertable on version_date
CREATE TABLE IF NOT EXISTS trial_versions (
    nct_id       text NOT NULL,
    version_no   integer NOT NULL,
    version_date date NOT NULL,
    field        text NOT NULL,
    old_value    text,
    new_value    text,
    change_class text CHECK (change_class IN ('widened', 'narrowed', 'mixed', 'administrative')),
    PRIMARY KEY (nct_id, version_no, field, version_date)
);
SELECT create_hypertable('trial_versions', by_range('version_date', INTERVAL '1 year'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS trial_versions_nct_version_idx ON trial_versions (nct_id, version_no);

-- criteria_rules (T12): parsed eligibility rules. nct_id may be an NCT id, a
-- draft id or a fixture id, so there is deliberately no foreign key.
CREATE TABLE IF NOT EXISTS criteria_rules (
    nct_id       text NOT NULL,
    version_no   integer NOT NULL,
    rule_id      text NOT NULL,
    kind         text CHECK (kind IN ('inclusion', 'exclusion')),
    field        text NOT NULL,
    operator     text NOT NULL,
    value        jsonb,
    unit         text,
    raw_text     text,
    parse_status text CHECK (parse_status IN ('ok', 'unparseable')),
    locked       boolean NOT NULL DEFAULT false,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (nct_id, version_no, rule_id)
);
CREATE INDEX IF NOT EXISTS criteria_rules_field_idx ON criteria_rules (field);

-- trial_embeddings (T12): 768-d embeddings with HNSW cosine index
CREATE TABLE IF NOT EXISTS trial_embeddings (
    nct_id     text PRIMARY KEY,
    model      text,
    embedding  vector(768) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trial_embeddings_hnsw_idx ON trial_embeddings USING hnsw (embedding vector_cosine_ops);

-- sim_runs (T14 / T24): one row per simulation run, hypertable on run_at
CREATE TABLE IF NOT EXISTS sim_runs (
    run_id               text NOT NULL,
    trial_id             text NOT NULL,
    version_no           integer,
    seed                 integer NOT NULL,
    model_version        text,
    n_personas           integer,
    n_eligible           integer,
    eligible_frac        double precision,
    n_agents             integer,
    enroll_rate          double precision,
    completion_rate      double precision,
    median_dropout_visit integer,
    dropout_curve        jsonb,
    top_reasons          jsonb,
    funnel               jsonb,
    run_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, run_at)
);
SELECT create_hypertable('sim_runs', by_range('run_at', INTERVAL '1 month'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS sim_runs_trial_run_at_idx ON sim_runs (trial_id, run_at DESC);

-- persona_outcomes (T14 / T24): per-persona result of a run
CREATE TABLE IF NOT EXISTS persona_outcomes (
    run_id        text NOT NULL,
    persona_id    text NOT NULL,
    eligible      boolean NOT NULL,
    enrolled      boolean,
    dropout_visit integer,
    reason        text,
    burden_tags   text[],
    persona       jsonb,
    PRIMARY KEY (run_id, persona_id)
);

-- sim_features: VIEW owned and created by T24. Intentionally not created here.

-- predictions (T21): model outputs, hypertable on predicted_at
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id text NOT NULL,
    trial_id      text NOT NULL,
    model_version text NOT NULL,
    target        text CHECK (target IN ('accrual_fail', 'shortfall', 'amend')),
    p             double precision NOT NULL,
    ci_low        double precision,
    ci_high       double precision,
    context       text NOT NULL DEFAULT 'live' CHECK (context IN ('live', 'backtest', 'replay', 'validation', 'fixture')),
    predicted_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (prediction_id, predicted_at)
);
SELECT create_hypertable('predictions', by_range('predicted_at', INTERVAL '1 month'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS predictions_trial_predicted_at_idx ON predictions (trial_id, predicted_at DESC);

-- outcomes (T11 initial, T21 updates): observed labels, hypertable on observed_at
CREATE TABLE IF NOT EXISTS outcomes (
    nct_id      text NOT NULL,
    label       text NOT NULL CHECK (label IN ('accrual_fail', 'shortfall', 'amend', 'status')),
    value       double precision,
    source      text,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (nct_id, label, observed_at)
);
SELECT create_hypertable('outcomes', by_range('observed_at', INTERVAL '1 year'), if_not_exists => TRUE);

-- prediction_scores (T21): prediction joined to its outcome, materialized so a
-- continuous aggregate can sit on top (caggs cannot join two hypertables)
CREATE TABLE IF NOT EXISTS prediction_scores (
    prediction_id text NOT NULL,
    trial_id      text,
    model_version text,
    context       text NOT NULL DEFAULT 'live',
    target        text NOT NULL,
    p             double precision NOT NULL,
    y             double precision NOT NULL,
    month         date NOT NULL,
    scored_at     timestamptz NOT NULL,
    PRIMARY KEY (prediction_id, scored_at)
);
SELECT create_hypertable('prediction_scores', by_range('scored_at', INTERVAL '1 year'), if_not_exists => TRUE);

-- prediction_scores_monthly (T21): monthly Brier / prevalence for the replay chart.
-- Real-time (materialized_only = false) so rows newer than the last refresh show up.
-- Replay writes historical scored_at (2020-2023) BELOW the materialization
-- watermark, so after a replay T21 must run
--   CALL refresh_continuous_aggregate('prediction_scores_monthly', NULL, NULL)
-- (CALL cannot run inside a transaction block, use autocommit).
CREATE MATERIALIZED VIEW IF NOT EXISTS prediction_scores_monthly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 month', scored_at) AS bucket,
       target,
       context,
       model_version,
       count(*)              AS n,
       avg((p - y) * (p - y)) AS brier,
       avg(y)                AS prevalence,
       avg(p)                AS mean_p
FROM prediction_scores
GROUP BY bucket, target, context, model_version
WITH NO DATA;
-- start_offset NULL = all history (replay data is old), end_offset 1 hour.
SELECT add_continuous_aggregate_policy('prediction_scores_monthly',
    start_offset => NULL,
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

-- feedback (T21): human-in-the-loop decisions, hypertable on created_at
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id text NOT NULL,
    user_name   text NOT NULL DEFAULT 'anonymous',
    target_type text NOT NULL CHECK (target_type IN ('fix', 'flag', 'rule', 'persona', 'prediction', 'run')),
    target_id   text NOT NULL,
    trial_id    text,
    decision    text NOT NULL CHECK (decision IN ('accept', 'reject', 'rate', 'comment')),
    rating      integer,
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feedback_id, created_at)
);
SELECT create_hypertable('feedback', by_range('created_at', INTERVAL '1 month'), if_not_exists => TRUE);

-- fixes (T23): proposed protocol edits and their simulated effect
CREATE TABLE IF NOT EXISTS fixes (
    fix_id     text PRIMARY KEY,
    trial_id   text NOT NULL,
    label      text,
    edits      jsonb NOT NULL,
    metrics    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fixes_trial_idx ON fixes (trial_id);

-- drafts (T25): user-pasted draft protocols
CREATE TABLE IF NOT EXISTS drafts (
    draft_id      text PRIMARY KEY,
    text          text NOT NULL,
    schedule_text text,
    parsed        jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- llm_cache (T00, shared): deterministic LLM response cache
CREATE TABLE IF NOT EXISTS llm_cache (
    key        text PRIMARY KEY,
    namespace  text NOT NULL,
    model      text NOT NULL,
    request    jsonb NOT NULL,
    response   jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_cache_namespace_idx ON llm_cache (namespace);

-- llm_usage (T00, shared): per-call token accounting, hypertable on created_at, no PK
CREATE TABLE IF NOT EXISTS llm_usage (
    created_at      timestamptz NOT NULL DEFAULT now(),
    namespace       text NOT NULL,
    model           text NOT NULL,
    key_fingerprint text NOT NULL,
    kind            text NOT NULL DEFAULT 'generate',
    input_tokens    integer NOT NULL DEFAULT 0,
    output_tokens   integer NOT NULL DEFAULT 0,
    ok              boolean NOT NULL DEFAULT true
);
SELECT create_hypertable('llm_usage', by_range('created_at', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS llm_usage_key_created_idx ON llm_usage (key_fingerprint, created_at DESC);
