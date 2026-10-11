# Gate 4 ship report (v1.0)

Integrator: A-claude (G4). Repo: https://github.com/RaghulKS/Clinova. Release: https://github.com/RaghulKS/Clinova/releases/tag/v1.0

## Merged

- `task/T41-readme` (README, `scripts/check_numbers.py`), `task/T42-deck` (12-slide deck, PDF, renders, outline), `task/T43-video` (video script and checklist, Devpost text, `scripts/demo_reset.sh`). Each was merged with `--no-ff`, with no conflicts.
- Not merged: T16 (`cut:`; MiroFish not run), as in gates 1 to 3. Every other `done/*` branch was already in main.
- No new `docs/contract_changes/` requests. T41's note (run the checker in strict mode at G4) was applied; see below.

## G4 fixes

1. **Number check (`scripts/check_numbers.py`).** The first strict run reported 25 problems, and none of them was a wrong number. Each was a claim the checker could not tie to a source. The causes were word counts in speaker-note headers, file names, `CC BY 4.0`, Unicode minus signs, `95% bootstrap interval`, paired differences that live only in `docs/metrics_detail.json`, demo numbers in `docs/demo_results/`, and literature figures. Fixes:
   - New checker sources: `detail.*` (metrics_detail.json), `demo.*` / `demo_opt.*` (the demo trial's frozen results), and `metrics.split_year`. New `<!-- external: <file> -->` marker for literature figures: every number on that line must appear verbatim in the cited file (docs/ARCHITECTURE.md section 2). Structural numbers are now stripped. The self-test is extended.
   - The deck outline is annotated at its source: `OUTLINE_SOURCES` in `docs/deck/build_deck.py`, regenerated with `write_outline`. Its text is unchanged and the PDF/PPTX are untouched. `write_outline` now writes UTF-8 with LF line endings, so it also works on Windows.
   - `docs/submission/devpost.md` is annotated. Wording changes: "+4.4 dropout points" became "+4.4% absolute dropout" (likewise -2.0 and +3.6), and the 16-simulated-trials sentence was reworded so it can be annotated.
2. **The zero-network demo test now runs in CI.** `tests/demo/test_demo_mode.py::test_demo_flow_*` always skipped, because `tests/conftest.py` pins `DATABASE_URL` to a dead port. The conftest now honors an opt-in `CLINOVA_TEST_DATABASE_URL`, and CI's demo-smoke job runs those two tests against the seeded compose DB. The job fails unless both pass.

## Readiness checklist (ARCHITECTURE.md section 9, plus G4)

| Item | Result | Evidence |
| --- | --- | --- |
| Fresh clone, no `.env`, `docker compose up` gives a working app; demo trial end to end in a browser | **Pass on CI** (Ubuntu). Docker untested on this Windows host (no WSL) | CI run [38117527195](https://github.com/RaghulKS/Clinova/actions/runs/38117527195), job demo-smoke: `docker compose up --build --wait` started 06:16:34Z, app healthy 06:18:03Z (about 90 s cold). `Seeded demo run completed with no fixture fallback.` Playwright `clean_path.browser_flow()`: `{"load_s": 2.4, "run_s": 1.6, "accept_s": 1.9, "fix_cards": 3}` |
| Demo mode makes zero network calls (test passes) | **Pass** | Same run, step "Demo mode makes zero network calls against the seeded DB": `2 passed, 5 deselected`. The tests refuse every socket except the DB host, with no `GEMINI_API_KEY` |
| CI green on main | **Pass** | Run 38117527195 on `eb00f22`: job `check` (`make check`: ruff `All checks passed!`, `521 passed, 2 skipped`) and job `demo-smoke` both succeeded. Locally (Windows, no make): `uv run ruff check .` clean; `uv run pytest -q` 519 passed, 2 skipped (the DB demo tests skip locally) |
| Live mode needs only `GEMINI_API_KEY` and optionally `DATABASE_URL` | As documented | README "Live mode"; `.env.example`. Not re-run: the Gemini daily quota is exhausted |
| `LICENSE` is MIT; no MiroFish code | **Pass** | `LICENSE` line 1: `MIT License`. `mirofish/` does not exist. `git grep -i -E "agpl\|mirofish" -- clinova scripts db` returns only the `SKIP_MIROFISH` config flag. Remaining mentions are docs and the cut compose profile comment ("never copied into this repo") |
| pytest covers parser, screen, metrics, leakage, demo mode | Pass | `tests/parse`, `tests/cohort`, `tests/model`, `tests/demo`; 521 tests on CI |
| README: quickstart, architecture diagram, metrics, limitations | Pass | README.md: "Try the demo", mermaid diagram, "Results, including the misses", "Limits, responsible use and licenses" |
| `python scripts/check_numbers.py`: zero mismatches across README, deck outline, video script, Devpost | **Pass** | `Checked 123 numeric claims; 0 mismatches.` `--self-test` passes |
| No secrets in repo or history | **Tree: pass. History: one known exposure** | Working tree: 0 hits for `AIza…`, `sk-…`, `ghp_…`, `github_pat_`, private-key headers, `tsdb.cloud.timescale.com`, the Tiger host, the Tiger password and the Gemini key. History (`git log --all -S/-G`): no Gemini key, `sk-` or `ghp_` in any pushed commit. **The Tiger Cloud host and password are in main's history**: `d758d65` "Add files via upload" added `tiger-cloud-db-96056-credentials.txt`, and `15a2a02` deleted it. History is not rewritten on origin (protocol rule 10), so **rotate the Tiger password**. A local-only agent checkpoint ref (`refs/agents/8b050107…`, commit `9be65bd`) holds a `.env` snapshot with the Gemini key. It is on no remote branch or tag. Never push it (no `git push --all` / `--mirror`); delete it locally if convenient |

## Headline metrics (frozen model v1.0, from docs/metrics.json, metrics_detail.json and replay.json)

- Split: 1,424 train trials (start before 2018-01-01) and 360 test trials.
- Accrual failure: AUC 0.701 (95% CI 0.623 to 0.774) on 359 test trials with 34 failures. The logistic baseline scores 0.661 (0.593 to 0.723). The paired difference is +0.041 (-0.036 to +0.116), so improvement over the baseline is not established. Brier is 0.0827 vs 0.0841 for the baseline.
- Shortfall: not validated (AUC 0.598 vs 0.605 for the baseline).
- Amendment hit rate and simulation ablation are not computable (no labeled amended test trials; 16 simulated trials).
- 48-month replay: pooled Brier 0.0782 retrained vs 0.0767 frozen over 1,736 overlapping windows. Retraining did not help.
- Demo NCT03232892: the model misses it (accrual-failure risk 0.072). The top exclusion flag is prior platinum, a rule absent from the current record, but the timing of that change is unverified.

## Release

- Tag `v1.0` on main. The GitHub Release is created through the REST API, with `docs/deck/clinova.pdf` attached. No seed asset is needed: T31 keeps the 25 MB seed in git under `db/seed/`.

## Human to-do

1. Record the video with `docs/video/script.md` and `docs/video/checklist.md`, then upload it.
2. Submit using `docs/submission/devpost.md`. Strip the `<!-- numbers: ... -->` comments before pasting, because they are checker annotations.
3. Rotate the Tiger Cloud password (it is in git history, `d758d65`).
4. Optional: install WSL and Docker Desktop to test `docker compose up` locally (only CI has tested it).
5. Kill leftover background jobs, including the T22 retrain watcher (PID 24028).
