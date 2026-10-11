# G2: month-only start dates relabel

Numbers come from `scripts/relabel_month_starts.py` (logs: `.data/logs/G2-relabel.log`, `-2.log` and `-3.log` in the main clone) and one SQL count over rows with `start_date_precision = month`. Run on 2026-10-11, `CLINOVA_MODE=demo`, so Gemini answered from `llm_cache` only. No network calls were made.

## What changed
- `clinova/ingest/pull.py` (T22 contract change item 1): a `YYYY-MM` start maps to the 1st of that month. A month is enough to place a trial on one side of 2018-01-01. Invalid months such as `2017-13` stay null and pending.
- `scripts/relabel_month_starts.py` rebuilds every pending ended trial with a month-only start from its stored row. It uses T11's own path: `map_trial`, then `label_trial`, then `load_trial(trial, [])`. It keeps the stored `_clinova_provenance` and adds `start_date_precision = month` and, for TERMINATED/WITHDRAWN rows, `stop_category_source`.
- The static note in `clinova/ingest/report.py` now describes month-only starts. `docs/data_report.md` was regenerated. `write_reports` also writes `docs/demo_trial.md` and `docs/labels_spotcheck.csv`, which would overwrite G1's demo pick. So the report went to `.data/G2-report/` and only `data_report.md` was copied into `docs/`.

## Counts
The T11 job kept loading trials during the runs, so the script ran three times. Runs 2 and 3 only picked up month-only rows that T11 loaded after run 1.

| run | candidates | to train | to test |
| --- | --- | --- | --- |
| 1 | 864 | 853 | 11 |
| 2 | 38 | 38 | 0 |
| 3 | 7 | 7 | 0 |

All rows now marked month precision: 909 (898 train, 11 test).

Per split, before run 1 and after run 3:

| split | trials | accrual labeled / positive | shortfall labeled / positive |
| --- | --- | --- | --- |
| train before | 276 | 264 / 21 | 226 / 92 |
| train after | 1186 | 1135 / 136 | 645 / 307 |
| test before | 276 | 275 / 34 | 271 / 157 |
| test after | 294 | 293 / 34 | 289 / 170 |
| pending before | 876 | 0 / 0 | 0 / 0 |
| pending after | 14 | 0 / 0 | 0 / 0 |

Some of the growth in the totals comes from T11 loading new trials during the runs. T11 keeps loading new month-only trials as pending. A final check after run 3 found 5 more (14 rows had no start date), and a re-run picks them up.

## Where the stop labels come from (909 month-only rows)
| stop_category_source | train | test | train accrual positives |
| --- | --- | --- | --- |
| gemini_cache: an identical stop text was already in the Gemini cache | 24 | 1 | 23 |
| keyword_G2: cache miss, keyword rules | 210 | 10 | 92 |
| no_stop_text: label_trial sets `unknown`; TERMINATED gets a null accrual label | 46 | 0 | 0 |
| COMPLETED: accrual label false, no stop category | 618 | 0 | 0 |

Rows labeled by keyword get `stop_category` `accrual` or `other`, never safety, efficacy_futility or business_funding. Rows with no stop text get `unknown`, which is label_trial's own rule. As in label_trial, WITHDRAWN rows get `label_accrual_fail = false` whatever their category. Keyword labels also reach `outcomes` with source `ctgov:T11`, through T11's unchanged `load_trial`.

## Keyword rules vs Gemini (accrual vs not)
The rules follow the labels.py prompt: accrual only when an enrollment or recruitment problem is explicit; mixed reasons count as other. They were written only against Gemini labels on train-split trials and on pre-2018 cached pending trials, then frozen before the test numbers were computed.

| set | n | accuracy | Cohen's kappa | both accrual | both not | keyword-only accrual | Gemini-only accrual |
| --- | --- | --- | --- | --- | --- | --- | --- |
| train (gate set) | 73 | 0.9863 | 0.9708 | 45 | 27 | 1 | 0 |
| test (checked once, rules frozen) | 120 | 0.975 | 0.939 | 33 | 84 | 1 | 2 |

The gate was accuracy >= 0.90 and kappa >= 0.70 on train. It passed, so keyword labels were applied. Run 2 saw one more test trial loaded by T11 (n = 121, accuracy 0.9752, kappa 0.9392). The rules did not change.

## Known bias
- In train, 92 of the 115 new accrual positives come from keyword rules, not Gemini. Labels of month-only trials therefore partly come from a different labeler than the rest of the data. On test it disagrees with Gemini on 3 of 120 stop texts.
- Month-only trials are almost all pre-2018, so they enlarge train much more than test.
- T11's background job still runs the old `map_trial`. If its history pass reaches these rows, it reloads them as pending with no labels and replaces their provenance. New month-only trials it loads also arrive as pending. The script is re-runnable and resumable: it only touches pending rows, so re-run it after the T11 job finishes:
  `CLINOVA_MODE=demo uv run python scripts/relabel_month_starts.py` (add `--dry-run` to only compute the labels and agreement).
