# ClinicalTrials.gov API and history spike (T01)

Live probe: **2026-10-10 22:45 UTC**. Research only; no clinical conclusions.

## Query and observed counts

`GET https://clinicaltrials.gov/api/v2/studies`, with these URL-encoded parameters:

```text
filter.advanced=AREA[ConditionSearch]("non-small cell lung cancer") AND AREA[StudyType]INTERVENTIONAL AND (AREA[Phase]PHASE2 OR AREA[Phase]PHASE3) AND NOT AREA[Phase]PHASE1
countTotal=true
pageSize=1
```

HTTP 200 returned **3,766** studies. Add `filter.overallStatus=COMPLETED,TERMINATED`: HTTP 200, **1,897**. TERMINATED alone, fetched in one page with `pageSize=1000`: HTTP 200, **463**. These are observed search-cohort counts, not a claim that every study is exclusively NSCLC. Basket studies and condition indexing require downstream validation.

The phase field is an array: Phase 2/3 is `["PHASE2", "PHASE3"]`. Matching PHASE2 alone also includes Phase 1/2; `NOT PHASE1` excludes that unwanted combined phase. Without that exclusion the same condition-search query returned 4,576 total, 2,288 ended, 611 terminated. The broad `query.cond=non-small cell lung cancer` probe returned 4,774 and included a small-cell-only title: do not silently treat that broad search as an exact cohort.

Canonical documentation: [API v2](https://clinicaltrials.gov/data-api/api). The runnable probe preserves each exact encoded request URL, HTTP status and raw JSON response locally.

## JSON paths

Paths below are relative to each `studies[]` item (single-study API responses omit that outer array). `p` means `protocolSection`.

| Field | Path |
|---|---|
| NCT ID and title | `p.identificationModule.nctId`, `.briefTitle` |
| Eligibility text | `p.eligibilityModule.eligibilityCriteria` |
| Age bounds | `p.eligibilityModule.minimumAge`, `.maximumAge` |
| Enrollment | `p.designModule.enrollmentInfo.count`, `.type` (`ESTIMATED` / `ACTUAL`) |
| Status and reason | `p.statusModule.overallStatus`, `.whyStopped` |
| Start date | `p.statusModule.startDateStruct.date`, `.type` |
| Primary completion | `p.statusModule.primaryCompletionDateStruct.date`, `.type` |
| Phases | `p.designModule.phases[]` |
| Arms | `p.armsInterventionsModule.armGroups[]` (`label`, `type`, `description`, `interventionNames[]`) |
| Interventions | `p.armsInterventionsModule.interventions[]` (`type`, `name`, `description`, `armGroupLabels[]`) |
| Locations | `p.contactsLocationsModule.locations[]` (`facility`, `city`, `state`, `country`, optional `geoPoint`) |
| Sponsor class | `p.sponsorCollaboratorsModule.leadSponsor.class` |
| First posted | `p.statusModule.studyFirstPostDateStruct.date`, `.type` |

Do not confuse `identificationModule.organization.class` with lead sponsor class. The live sample had minimum age `18 Years` and no maximum-age key. Absence is not age zero or unlimited eligibility without qualification.

## History discovery and five-trial tests

The site's [study page](https://clinicaltrials.gov/study/NCT04405401?tab=history) loads `main-PAEXOP4S.js`. Its imported `chunk-AYTQ3KXZ.js` contains `getStudyDetails`, which adds `history:"true"`, and `getStudyVersion`, which constructs `/studies/${id}/history/${version}` with optional `?patchToVersion=${otherVersion}`. `chunk-O5R2NEDA.js` sets `backendUrl:"/api/int"`. These asset names are a dated observation, not stable API dependencies.

1. Version list: `GET https://clinicaltrials.gov/api/int/studies/{NCT}?history=true`.
2. Full historical record: `GET https://clinicaltrials.gov/api/int/studies/{NCT}/history/{version}`.
3. Optional comparison: the same version URL with `?patchToVersion={otherVersion}`; response semantics were not verified because the test returned 403.

Detail response shape observed: `{study: {protocolSection, derivedSection, hasResults}, history: {changes: [...], originalData: {...}, lastUpdateVersions: {...}, ...}, topics?: {...}}`. Each `history.changes[]` has `version` (integer), `date` (YYYY-MM-DD), `status`, `studyType`, `moduleLabels[]`, `lastUpdateSubmitQcDate`.

Version response shape observed: `{studyVersion: 1, study: {...}}`. Numbering is **zero-based**: NCT03644823 index starts at version 0, 2018-08-21; `/history/1` reports `studyVersion:1` and last-update submission 2018-08-23. Do not assume URL version 1 is the original registration. UI display numbering may differ.

| Trial | Detail with history | `/history/1` | Indexed versions |
|---|---|---|---:|
| NCT03644823 | 200 | 200 | 5 |
| NCT04405401 | 200 | 200 | 8 |
| NCT06333678 | 200 | 200 | 13 |
| NCT03425331 | 200 | 200 | 21 |
| NCT03496766 | 200 | 200 | 19 |

Access is **not reliable enough to assume bulk first-version coverage**. Initial default-httpx requests returned 403. A later client with a descriptive research User-Agent obtained the ten successful responses above. Further requests to versions 0, 2, 3, 6 and 7, including the documented comparison form, returned 403; the User-Agent is not a proven fix. No authentication bypass was attempted. Preserve HTTP status and stop or defer blocked records; do not label them successfully fetched.

Polite policy: one sequential request per second, reuse downloaded records, no retry loop on 403. This is our proposed research limit, **not a verified published service quota**. Handle 429 with `Retry-After`/backoff in production. The small probe does not establish a sustainable bulk rate.

Fallback: keep the earliest **successfully retrieved** snapshot, its actual version number/date and an explicit provenance flag. Current-record fallback must be marked current-only. Neither may be passed off as first registration. Current eligibility, enrollment, sites and status can leak amendments/outcomes into model features. Exclude missing-first-version trials from first-registration training, amendment direction/hit-rate scoring and planned-versus-actual shortfall labels unless the missing target can be independently recovered. Dates are submitted/QC dates in the observed history index, not independently verified effective amendment dates.

## Demo candidates: verified parts and remaining gaps

All five below were returned by the Phase 2/3 interventional query, started in 2018 or later and are TERMINATED. Reasons are summarized from the real current records. Three have nonempty normalized eligibility differences between retrieved version 1 and current, plus post-start Eligibility entries in their history index. Adjacent snapshots at those amendment dates returned 403, so assigning each individual edit to a particular dated version is not fully verified. Text changes alone do not prove operational benefit or a clinically valid relaxation.

| Trial / title | Start | Why stopped | Eligibility evidence |
|---|---|---|---|
| [NCT04405401](https://clinicaltrials.gov/study/NCT04405401), Stereotactic Ablative Radiotherapy for Oligo-Progressive Non Small Cell Lung Cancer | 2021-01-05 | Poor accrual | Version-1/current diff expands qualifying oligoprogression from ICI/TKI to systemic therapy generally. Only indexed post-start Eligibility change: 2026-09-24 (v7). Exact adjacent snapshots unavailable. |
| [NCT06333678](https://clinicaltrials.gov/study/NCT06333678), A Study Comparing Sotorasib With Durvalumab in People With NSCLC | 2024-03-20 | Closed for lack of enrollment | Version-1/current diff permits durvalumab consolidation already started, alongside planned initiation. Only indexed post-start Eligibility change: 2025-01-07 (v3). Exact adjacent snapshots unavailable. |
| [NCT03496766](https://clinicaltrials.gov/study/NCT03496766), Tipifarnib in Advanced Squamous NSCLC With HRAS Mutations | 2018-05-07 | Recruitment closed early for slow recruitment | Version-1/current diff lowers ANC 1,500 to 1,000 cells/µL and platelets 100,000 to 75,000/µL; also changes tissue-confirmation requirements and medication exclusions. Indexed Eligibility dates: 2019-11-21 (v12), 2024-03-28 (v17). Exact attribution between those dates unverified. |
| [NCT03644823](https://clinicaltrials.gov/study/NCT03644823), Com-IT-1 Irradiation and PD-1 Blockade in Advanced NSCLC | 2018-08-15 | Slow recruitment | **Reserve only, not qualified:** history index has no Eligibility change; no post-start eligibility amendment established. |
| [NCT03425331](https://clinicaltrials.gov/study/NCT03425331), Biomarkers of Response to Ipilimumab and Nivolumab in First-line NSCLC | 2018-04-10 | Slow accrual, competing studies and lack of efficacy | **Reserve only, not qualified:** no Eligibility change in history index; reason is mixed accrual/efficacy. |

Thus **five fully qualified demo trials were not established**. Do not silently choose a reserve as a verified amendment backtest. The spike fallback is to expose that gap. NCT04405401 and NCT06333678 are the strongest follow-up candidates, subject to original-version retrieval and exact amendment verification.

## Gotchas and reproduction

- Pagination uses opaque `nextPageToken` → `pageToken`. Keep filters fixed. `totalCount` requires `countTotal=true`; it is a search result count. A daily-refreshing public index is not a sealed snapshot.
- Arrays/modules/whyStopped/max age may be absent. Preserve missing values; enrollment count 0 is valid. ESTIMATED means anticipated, not observed enrollment.
- Dates may be year, year-month or full date. Preserve precision instead of inventing a day. History uses YYYY-MM-DD in these examples. Do not treat posting dates as study start or amendment effective dates.
- History eligibility contains HTML and encoded entities; v2 text is formatted differently. Decode entities and normalize markup before semantic comparison; raw string differences can be administrative.
- A condition-search hit can be a basket or mixed-histology trial. Validate inclusion and label reasons. Keyword matching is candidate discovery, not definitive accrual classification.
- First posted can follow start, as in Com-IT-1: a registered snapshot is not necessarily a truly pre-enrollment protocol.
- `/api/int` is a site's internal endpoint, with no stable public API guarantee. Archive successful metadata with retrieval timestamp and schema checks.

```sh
uv run --no-project --with httpx python scripts/spikes/ctgov_probe.py --self-check
uv run --no-project --with httpx python scripts/spikes/ctgov_probe.py
uv run --no-project --with httpx python scripts/spikes/ctgov_probe.py --amendments
```

Output defaults to `.data/ctgov_probe.json` (public metadata only, no credentials), recording encoded request URLs, statuses, sample records and successful history payloads. `--amendments` resumes that file and saves per-candidate progress. Its `exact_adjacent_versions` flag distinguishes exact dated diffs from available-version/current comparisons. It may find fewer than five due to blocked access or lack of qualifying changes. No product code, database writes, Gemini calls or model metrics were involved.
