# Demo trial

**Selected: NCT03232892** (G1, hour 4.6). Test split, TERMINATED, stop reason "Low Accrual" (label_accrual_fail = true), terminated 2019-09-11. Eligibility was widened between the first registered text and the current text: the inclusion rule requiring prior platinum therapy (`prior_platinum`) was removed, and the cardiac-condition exclusion (`cardiac_condition`) was removed. Both map to ClinicalFields other than `other`. Most parseable first-version rules among widened candidates (20). Setting inferred: second_line_plus.

**Backups:**
1. NCT03417882: 17 parseable rules, terminated 2022-12-05 ("Recruitment rate too slow"). Caveat: the diff shows age, ECOG, life expectancy and lab thresholds "removed", which looks like a restructured current text that the parser read differently, not a real widening. Check by hand before using it.
2. NCT06333678: 11 parseable rules, terminated 2026-01-27 ("lack of enrollment"); interstitial-lung-disease exclusion removed.

**How this was picked:** `scripts/pick_demo_trial.py` (output in `.data/G1-demo-pick.json` in the main clone). It parses the first and current eligibility text of every test-split trial with stop_category accrual, runs `parse.diff.diff_rules`, keeps fields that were relaxed or removed (not `other`), and ranks by parseable first-version rules, then by earliest termination. `DEMO_NCT_ID` was not set in `.env`.

**Limits (honest):**
- CT.gov version histories are not loaded yet (`trial_versions` has 0 rows), so "widened **before termination**" is not verified by date. It rests on the current text of a terminated trial differing from its first registered text.
- "Removed" changes can be parser variance between two separate parses, not real protocol changes.
- 6 of 16 accrual candidates (NCT03805841, NCT04372927, NCT04908956, NCT04926831, NCT05215951, NCT05715229) could not be checked: Gemini returned 429 on the current-text parse. NCT05715229 was not parsed at all, so in principle it could outrank the pick. Re-run the script when the quota recovers.
- NCT03520686 is classified accrual, but its stop text ("no longer active subjects") gives no accrual reason. It was not chosen.

## Ingestion report output (clinova.ingest.report)

# Demo trial candidates

Selection: test split, terminated for accrual, exact eligibility widening before completion; ranked by original eligibility characters / 1000, capped at 10.

No fully verified candidates yet. History enrichment is incomplete; no reserve is presented as verified.
