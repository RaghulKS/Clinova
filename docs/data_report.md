# CT.gov ingestion data report

Generated from Tiger Cloud by clinova.ingest.report.

```json
{
  "total": 2097,
  "status": {
    "COMPLETED": 1400,
    "TERMINATED": 419,
    "WITHDRAWN": 89,
    "UNKNOWN": 79,
    "SUSPENDED": 4,
    "RECRUITING": 40,
    "ACTIVE_NOT_RECRUITING": 36,
    "ENROLLING_BY_INVITATION": 1,
    "NOT_YET_RECRUITING": 29
  },
  "split": {
    "train": 1449,
    "pending": 288,
    "test": 360
  },
  "originals": 2097,
  "version_events": 0,
  "labels": {
    "accrual_fail": {
      "known": 1757,
      "positive": 170,
      "prevalence": 0.09675583380762663
    },
    "shortfall": {
      "known": 1148,
      "positive": 544,
      "prevalence": 0.4738675958188153
    },
    "amend": {
      "known": 24,
      "positive": 0,
      "prevalence": 0.0
    }
  }
}
```

Originals count confirmed version-0 snapshots only. Missing originals are excluded from first-registration features and shortfall labels. Unknown amendment evidence is null, never a negative label. Partial dates remain in raw JSON. A month-only start (YYYY-MM) is stored as the 1st of the month for the 2018-01-01 split; other partial dates leave the date column null. Condition-search hits can include basket trials. The two-pass background job loads originals before full adjacent histories; coverage can increase.
