# Clinova

**Tagline:** Test-fly an NSCLC trial protocol on virtual patients before enrolling real ones.

## Inspiration

Eligibility rules can narrow a trial's reachable population, while travel and visit burden can affect enrollment and retention. Protocol teams need a practical way to inspect those trade-offs before recruitment starts, grounded in what happened to earlier studies. Clinova is designed as a decision aid for that review.

## What it does

Clinova loads a trial's first registered protocol from ClinicalTrials.gov or accepts pasted eligibility criteria and a visit schedule. It parses and lets a study team review and lock criteria, screens a synthetic cohort, shows where people are excluded, and presents enrollment-risk estimates with uncertainty and a validation context. An optimizer proposes three edits with trade-offs in enrollment speed, dropout, and cohort diversity. Accepting a suggestion records human feedback and rechecks the edited protocol. Validation, replay, and audit views expose evidence and limitations.

The demo is NCT03232892, a real NSCLC trial terminated for low accrual. Its prior-platinum rule is the top exclusion-share flag, and the sponsor's current record no longer includes that rule. The stored version history is empty, however, so the timing of that change is unverified. The model misses this trial: its cached accrual-failure probability is 0.072.

## How we built it

Python, Streamlit, FastAPI, PostgreSQL with TimescaleDB and pgvector (Tiger Data), LightGBM, Optuna, SHAP, and Gemini through a shared cached-call layer. ClinicalTrials.gov supplies public trial records; synthetic personas are used for screening and decision simulations. We evaluated MatrAIx Persona 1M but rejected it because its non-commercial, mixed-source license cannot be redistributed here. The implementation uses NVIDIA Nemotron-Personas-USA under CC BY 4.0 when available, with an explicitly labeled deterministic synthetic fallback. MiroFish was not used: its optional integration was cut, and no MiroFish code was copied. Four AI orchestrators—two Claude Code and two Codex sessions—coordinated task agents across two laptops using git tags and a shared Tiger Cloud database.

The local Docker Compose stack includes a seeded database and cached demo outputs so judges can run it without an API key. Gate 3 reports that the Dockerized smoke test passed on GitHub's Ubuntu runner in 89 seconds, including the browser flow. Docker itself was not tested on the build host.

## Challenges

The first model candidate appeared much stronger when it used parsed-rule and embedding features. We found that the processing order favored terminated trials, making “already parsed” a leakage proxy. We discarded that candidate and froze v1.0 with first-registration JSON features available across the cohort. Data were still arriving, and only a small number of trials had simulation coverage or usable amendment histories. We kept the absent hit-rate and ablation results unavailable instead of filling them with guesses. The backtest also misses its featured trial, and the optimizer's outputs are estimates rather than clinical effects.

## Accomplishments and evidence

- **1,424 training trials and 360 test trials** in the frozen v1.0 snapshot; trial starts before 2018 train the model, and starts on or after that date test it.
- **Accrual-failure AUC: 0.701** on 359 labeled test trials (34 failures), 95% bootstrap interval 0.623–0.774. The logistic baseline is 0.661 (0.593–0.723). The paired AUC difference is +0.041 (-0.036 to +0.116), so improvement over the baseline is not established.
- **Calibration:** accrual Brier score 0.0827 versus 0.0841 for the logistic baseline; the constant-prevalence reference is 0.0858. Served test probabilities are compressed, and the featured trial is an accrual miss.
- **Enrollment shortfall is not validated:** AUC 0.598 versus baseline 0.605, and Brier 0.2518 versus 0.2483.
- **Amendment hit rate and simulation ablation are unavailable.** There are no labeled amended test trials; the snapshot has only 16 simulated trials, below the stated ablation requirement.
- **Replay:** 48 retrospective months report pooled Brier 0.0782 retrained versus 0.0767 frozen, across 1,736 overlapping windows and 324 unique trials. Retraining did not improve pooled Brier.
- **Demo optimizer estimates:** the fastest option estimates +101% enrollment speed (+98% to +137% across five seeds), +4.4 dropout points, and +0.039 diversity; the lowest-dropout option estimates +53% speed (+51% to +66%), -2.0 dropout points, and +0.019 diversity; the balanced option estimates +95% speed (+90% to +128%), +3.6 dropout points, and +0.037 diversity. These are synthetic-cohort estimates. Expected amendment value is $0 because no amendment model is available, not because an edit is proven to save no money.

Metric values come from `docs/metrics.json`, `docs/replay.json`, and `docs/demo_results/NCT03232892_optimize.json`; validation caveats are in `docs/VALIDATION.md` and `docs/gate2_report.md`. The metrics snapshot was generated 2026-10-11 and labels were still loading, so these are a dated, provisional snapshot.

## What we learned

A polished interface does not make a weakly validated estimate strong. First-version-only features and explicit leakage checks mattered more than a higher headline AUC. Missing histories and thin simulation coverage should remain visible, and a single backtest is a demonstration rather than validation of individual predictions.

## What's next

Increase the clean labeled cohort, load dated protocol version histories, and repeat evaluation on a later frozen snapshot. Validate calibration and decision utility prospectively, test whether simulation adds useful signal, and improve uncertainty and precedent evidence before expanding beyond NSCLC. Keep clinical, statistical, and regulatory review in the loop.

## Built with

Python · Streamlit · FastAPI · PostgreSQL · TimescaleDB · pgvector · Tiger Data · Gemini · LightGBM · Optuna · SHAP · ClinicalTrials.gov · NVIDIA Nemotron-Personas-USA (CC BY 4.0) · Docker Compose · Claude Code · Codex
