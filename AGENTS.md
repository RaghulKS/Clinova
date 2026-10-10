# AGENTS.md: rules for every AI agent in this repo

Read this first, every session. It applies to Claude Code and Codex alike, to orchestrators and to task agents.

## What we are building
Clinova predicts where a draft NSCLC trial protocol will stall enrollment or lose patients, and proves it against real ClinicalTrials.gov outcomes. Full spec: `docs/ARCHITECTURE.md`. Coordination rules: `orchestration/PROTOCOL.md`. Your task: `orchestration/tasks/<ID>.md`.

## No human is watching
Nobody will answer questions during the build. Never stop to ask. Make the most reasonable decision, write it under "Decisions" in your handoff, and keep going. If you are truly blocked, follow the failure steps in PROTOCOL.md.

## Ownership (who may edit what)
| Area | Owner task(s) |
| --- | --- |
| `clinova/contracts.py`, `clinova/common/`, `db/schema.sql`, `pyproject.toml`, `Makefile`, `docker-compose.yml` (until T31), `AGENTS.md`, `docs/ARCHITECTURE.md`, `orchestration/` | Integrator only (T00, G1–G4) |
| `clinova/ingest/` | T11 |
| `clinova/loop/` | T21 |
| `clinova/parse/` | T12 |
| `clinova/model/` | T22 |
| `clinova/cohort/`, `clinova/simulate/screen.py`, `clinova/simulate/diversity.py` | T13 |
| `clinova/optimize/` | T23 |
| `clinova/simulate/agents.py`, `runner.py`, `surrogate.py`, `__main__.py` | T14, then T24 |
| `clinova/api/`, `clinova/app/` | T15, then T25, then T32 |
| `clinova/simulate/mirofish_client.py`, `mirofish/` | T16 |
| `docker-compose.yml`, `Dockerfile*`, `db/seed/` | T31 |
| `scripts/` | any task may add the scripts its task file names; never edit `orch.sh` |
| `tests/` shared infra, `.github/` | T33 (other tasks add tests for their own modules under `tests/<module>/`) |
| `docs/handoff/<ID>.md`, `docs/contract_changes/<ID>.md`, `docs/reviews/<ID>.md` | that task only |

Database tables have owners too (see ARCHITECTURE.md section 6). Write only to your tables. Read anything.

Need a change in a file you don't own? Don't edit it. Write the exact proposed diff and the reason in `docs/contract_changes/<ID>.md`, work around it locally behind a small shim in your own folder, and the integrator applies it at the next gate.

## Engineering rules
- Python 3.11 with uv. `make check` (ruff + pytest) must pass before you finish.
- Every Gemini call goes through `clinova/common/llm.py`. It caches, enforces `MAX_GEMINI_CALLS`, and makes demo mode work. Never import the Gemini SDK anywhere else.
- Tests never hit the network: use fixtures, recorded responses (respx) or the cache.
- Leakage rule: model features come only from a trial's first registered version.
- Time split: train = start date before 2018-01-01; test = on or after.
- Honesty rule: report numbers as they are. Never tune on the test split. Never write a number in docs that a script didn't produce.
- No real patient data. Clinova advises; it never approves a protocol.
- This repo is MIT. Never copy MiroFish (AGPL-3.0) code into it.
- Secrets: `.env` is never committed, printed or pasted into logs or docs.

## Every worktree needs `.env`
`.env` lives only in the main clone. In a worktree, link it: `ln -sf "$(git rev-parse --path-format=absolute --git-common-dir)/../.env" .env`.

## Long-running jobs
Run them with `nohup`, log to `.data/logs/<ID>-<name>.log` under the main clone, make them resumable (skip finished items), and record the command and how to check progress in your handoff. They keep running after your task ends.
