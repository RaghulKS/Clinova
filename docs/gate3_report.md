# Gate 3 report: packaged, tested, clean-clone verified

The integrator wrote this at build hour 7.23. Model numbers are unchanged from gate 2 (`docs/gate2_report.md`, `docs/metrics.json`); nothing in this gate retrained or re-scored anything.

## Merged (`--no-ff`, in order, no conflicts)
- T31 (ok): demo seed in git (`db/seed/`, 25 MB), Dockerfile, compose (db, one-shot seed, api, app), `scripts/seed.py`, `scripts/clean_path.py`.
- T33 (partial): offline test guard, metric examples, CI with a Docker demo smoke job. Its "partial" was only the smoke job waiting for T31; it now runs and passes (below).
- T32 (ok): polished app on the frozen v1.0 outputs, `api/demo_cache.py` (frozen risk/flags/fixes for the three demo trials), 11 screenshots.
- T34 (ok): `docs/VALIDATION.md`.
- Not merged: T16 (`cut:`, as in gates 1 and 2). `task/T42-deck` is Wave 4 work and was left for G4.

## Contract changes (each file has a "G3 status" section)
- **T31 #1 Makefile targets:** applied. **#2 demo optimizer cache:** applied, folded into T32's `demo_cache`: `live.optimize` serves `demo_cache.lookup(protocol).fixes` in demo mode. `scripts/demo_serve.py` is deleted; Dockerfile, compose and `clean_path.py` run `python -m clinova.api`. **#3 warm backups/draft:** not applied (needs Gemini).
- **T32 #1 macOS libomp:** applied in the `make test` recipe as proposed (variable set inline). Untested on macOS.
- **T33:** packaging gate removed from CI; the smoke now accepts `source` `live` or `cache` (since T32, frozen risk/flags/fixes make the demo run's source `cache`) and still rejects any fixture fallback.

## Fixes made at the gate
- **Linux container ran the demo's agent stage on fixtures.** Cause, confirmed by diffing the prompt on both platforms: personas carry unrounded floats (e.g. `crcl_ml_min` 97.9168847096342 on Windows vs 97.91688470963422 on Linux), so the exact cache key differs and 8 of 16 agent prompts missed. Fix: in demo mode only, `clinova/common/llm.py` accepts a cached response whose prompt matches after rounding long float literals to 10 significant digits (same namespace, model, seed, schema, temperature). Live-mode keys are unchanged. Root fix for later: round persona floats in `agent_prompt`, which needs a Gemini re-run to refill the cache.
- `clean_path.py` waited for the pre-T32 heading "4. Results"; it now waits for T32's results heading.
- `test_score_is_bounded_and_replication_invariant` is deterministic (`derandomize=True, deadline=None`).

## Checks
- `uv run ruff check . && uv run pytest -q` (no GNU make on this host): ruff clean, **519 passed, 2 skipped**. The 2 skips are T31's seeded-DB demo-flow tests, which the shared test config always skips (it points `DATABASE_URL` at a dead port); the clean-path runs below cover that flow.
- **CI on Ubuntu, run 38116167494 (commit ef05114), both jobs green:**
  - `check`: `make check` in 55 s.
  - `demo-smoke`: `docker compose up --build --wait` from a fresh checkout with no `.env` and no API key, cold, in **89 s** (image build, TimescaleDB, seed load, api and app healthy); `POST /runs` for NCT03232892 finished with no fixture fallback, 3 risks and 3 fixes; then Playwright drove the demo trial through the Dockerized app: load 3.4 s, run 2.4 s, accept a fix 2.0 s, 3 fix cards.
- **Clean clone without Docker on the build host** (`uv run python scripts/clean_path.py`: fresh temp clone, no `.env`, seeded Tiger Cloud DB, Playwright): pass. uv sync 18.2 s, api+app up 59.7 s, load 13.1 s, run 5.4 s, accept 11.0 s, 3 fix cards.
- **Docker untested on build host** (no WSL). Docker was tested on GitHub's Ubuntu runner instead (above).

## Open items for Wave 4
- Backups NCT03417882 / NCT06333678 and the example draft still run agents on labelled fixture simulation in demo mode (no cached decisions; Gemini quota).
- README badge line is in `docs/handoff/T33.md`.
- Gate 2's loose ends (T11/T12 background jobs, T22 watcher PID 24028) were not touched here.
