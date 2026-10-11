# App screenshots (T32)

All screens are 1920×1080. `python -m clinova.app.screenshots --url http://127.0.0.1:8501` took them. The API and app were running against the shared database, and `CLINOVA_MODE=demo` was set. They show demo trial **NCT03232892** on the frozen **v1.0** outputs.

Risk, flags and fixes for that exact protocol come from `docs/demo_results/` and are tagged `source: cache`. The funnel, agents, trial record and rule diff are live.

| File | View | Video scene |
| --- | --- | --- |
| `01-input.png` | Start screen: load a registered trial or paste a draft; how-it-works strip | 0:20 |
| `02-parsed-rules.png` | Trial as originally registered on 25 Jul 2017; editable rule table, unparseable rows in amber | 0:20 |
| `03-funnel-risk.png` | Funnel in protocol order with the flagged criterion (I3, prior platinum) highlighted; risk cards with percentile context, "not validated" and "no model yet" states | 0:20–1:10 |
| `04-flags.png` | Three flags: where, why, what to do, and the dollar line (not estimated: no amendment model) | 0:20–1:10 |
| `05-reveal.png` | Timeline (registered, start, terminated for low accrual); the sponsor later removed I3, the rule Clinova flagged | 1:10–1:30 |
| `06-fixes.png` | Three trade-off fixes with ranges; amendment value marked not estimated | 1:30–2:10 |
| `07-fix-accepted.png` | After Accept: before/after table (basis: optimizer estimate on this machine) | 1:30–2:10 |
| `08-validation.png` | AUC vs baseline with intervals, calibration, hit rate and ablation empty states, caveats | 2:10–2:40 |
| `09-replay.png` | Retrained vs frozen Brier, month by month, with the computed sentence | 2:10–2:40 |
| `10-pulse.png` | Community Pulse "not run" state (T16 was cut) | none |
| `11-audit.png` | Audit log: decisions and logged predictions | none |

## Best five for the deck
1. `05-reveal.png`: the sponsor removed the rule Clinova flagged first.
2. `03-funnel-risk.png`: where enrollment stalls, plus honest risk context.
3. `06-fixes.png`: three trade-offs with ranges.
4. `08-validation.png`: the proof tab, with intervals and caveats.
5. `04-flags.png`: where, why, what to do.

## Design review notes (what I looked for and fixed)
- **Layout:** results sit above the fold at 1080p. Funnel and risk share the first row, with flags, reveal and fixes below in video order. Fixes are three columns.
- **Numbers:** percentages show at most 1 decimal and dollars are rounded to $1K. Each sentence is built from API numbers. A probability placeholder (amendment) is never shown as a number.
- **Honesty labels:**
  - "fixture data" is an amber tag.
  - "Not validated" comes from metrics: the AUC interval must be above 0.5 and the Brier score must beat the baseline.
  - "No model yet" means the target has no test metrics.
  - A note under the results says when results come from the frozen cache.
- **Fixed during review:**
  - overlapping funnel tick labels;
  - the clipped hit-rate axis title;
  - calibration zoomed to the data (probabilities are compressed; the gate 2 report says served test p never exceeds 0.218);
  - Python list syntax in edit text;
  - the "+0.00 better" coloring below shown precision;
  - the misleading "fixture" tag on the real trial's history;
  - the before/after enroll rate that the optimizer's relative delta pushed to 100% (now enrollment speed, share of screened);
  - the legend that claimed simulation features v1.0 does not use.
- **Known and left:**
  - The optimizer's fix summaries read technically. That text comes from `clinova/optimize`.
  - Fix cards differ in height, so the Accept buttons do not line up.
  - The timeline has no eligibility-change date: `trial_versions` is empty, so the reveal says the change compares first and current text.
