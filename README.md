# Clinova

Test-fly an NSCLC trial protocol on virtual patients before enrolling a single real one, with every prediction graded against real ClinicalTrials.gov outcomes.

Clinova advises protocol authors; it never approves a protocol. No real patient data. MIT license.

> Work in progress. T41 writes the full README (quickstart, architecture diagram, metrics, limitations).

## Developer quickstart

```bash
uv sync                 # Python 3.11 + dependencies
cp .env.example .env    # fill in GEMINI_API_KEY and DATABASE_URL (never commit .env)
make db-apply           # apply db/schema.sql (idempotent)
make check              # ruff + pytest
```

Spec: `docs/ARCHITECTURE.md`. Shared models, tables and signatures: `docs/CONTRACTS.md`. Agent rules: `AGENTS.md`.
