# T14 live synthetic agent sanity check

These are measured Gemini responses on synthetic fixture personas, not real patient outcomes or clinical validation.

## Method

Exactly 200 fixture personas and the existing fixture reference screen: 56 eligible, 50 randomly sampled per seed. The same seed samples the same people in every burden condition. Seeds 1 and 2. All eligibility rules, purpose, and arms held fixed. Light: 6 one-hour visits every 28 days, no biopsy. Medium: 12 three-hour visits every 14 days, no biopsy. Heavy: 20 eight-hour weekly visits, two mandatory biopsies. All require site travel. The different durations are explicit; this checks the composite schedules rather than isolating any single burden.

## Prompt iterations

1. agents-v1: persona context, concrete trade-off, and visit-by-visit dropout reasoning. Enrollment increased with burden, failing the enrollment direction check. Dropout increased.
2. agents-v2: added total clinic hours, round trips and biopsies; asked each persona to assess whether they could commit to the entire schedule without inventing financial, travel or caregiving help before agreeing. No requested base rates, quotas or desired direction were included. This is the shipped prompt.

| Prompt | Burden | Seed | Agents | Enroll rate | Dropout among enrolled |
|---|---|---:|---:|---:|---:|
| agents-v1 | light | 1 | 50 | 0.560000 | 0.142857 |
| agents-v1 | light | 2 | 50 | 0.580000 | 0.034483 |
| agents-v1 | medium | 1 | 50 | 0.580000 | 0.344828 |
| agents-v1 | medium | 2 | 50 | 0.600000 | 0.233333 |
| agents-v1 | heavy | 1 | 50 | 0.660000 | 0.545455 |
| agents-v1 | heavy | 2 | 50 | 0.600000 | 0.433333 |
| agents-v2 | light | 1 | 50 | 0.500000 | 0.040000 |
| agents-v2 | light | 2 | 50 | 0.580000 | 0.103448 |
| agents-v2 | medium | 1 | 50 | 0.540000 | 0.222222 |
| agents-v2 | medium | 2 | 50 | 0.560000 | 0.392857 |
| agents-v2 | heavy | 1 | 50 | 0.320000 | 0.250000 |
| agents-v2 | heavy | 2 | 50 | 0.480000 | 0.416667 |

## Result

Mixed. In agents-v2, enrollment falls from light to heavy and dropout rises for both seeds. Dropout is ordered light < medium < heavy for both seeds. Enrollment is not strictly ordered across all three schedules: medium exceeds light for seed 1. This does not establish a reliable monotonic enrollment response. Ship the best prompt under the task fallback and let the fitted surrogate use explicit visit-burden features. The check is a small synthetic behavioral probe, not a calibrated real-world enrollment forecast.

## Persistence, tokens and cost

Verified after both iterations: 12 sim_runs, 2400 persona_outcomes, 600 actual agent decisions; 72 eligible unsampled rows retain enrolled=NULL. Each run and its persona rows commit atomically.

| Prompt | Network calls | Input tokens | Output + thinking tokens | Estimated paid-tier USD, all three trials | USD per trial, two seeds |
|---|---:|---:|---:|---:|---:|
| agents-v1 | 12 | 65429 | 24945 | 0.08199120 | 0.02733040 |
| agents-v2 | 12 | 66861 | 25451 | 0.08368580 | 0.02789527 |

Token counts are from llm_usage for the agents namespace during each isolated probe. Price estimate uses standard Gemini 3.5 Flash-Lite rates of $0.30/M input and $2.50/M output from [Google's model/pricing documentation](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6), checked 2026-10-10. This is an estimate from measured usage, not a billing invoice; account free-tier status was not checked. Per-trial cost covers 50 agents x two seeds, not the default 200 agents x two seeds.

## Surrogate

Final optimized scoring path, 1,000 personas, 100 repetitions after five warmups: median 2.275600 ms, p95 3.510220 ms, maximum 4.391400 ms. This is local timing, not a latency guarantee. Model fits enrollment plus dropout conditional on enrollment from v2 decisions and protocol burden features. Unsampled/ineligible rows are excluded from fitting. No held-out predictive-accuracy claim is made.

## Reproduce

`uv run python tests/simulate/run_sanity.py` explicitly spends live calls and writes real rows. It uses fixture adapters only inside this opt-in probe; production runner calls T13 cohort and screen. Evidence and model remain under DATA_DIR: T14-sanity.json (v1), T14-sanity-agents-v2.json, T14-surrogate-benchmark.json, models/surrogate.json. Ordinary pytest does not execute the live probe.
