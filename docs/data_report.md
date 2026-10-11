# CT.gov ingestion data report

Generated from Tiger Cloud by clinova.ingest.report.

```json
{
  "total": 1494,
  "status": {
    "COMPLETED": 1017,
    "TERMINATED": 419,
    "WITHDRAWN": 58
  },
  "split": {
    "train": 1186,
    "pending": 14,
    "test": 294
  },
  "originals": 1494,
  "version_events": 0,
  "labels": {
    "accrual_fail": {
      "known": 1428,
      "positive": 170,
      "prevalence": 0.11904761904761904
    },
    "shortfall": {
      "known": 934,
      "positive": 477,
      "prevalence": 0.5107066381156317
    },
    "amend": {
      "known": 21,
      "positive": 0,
      "prevalence": 0.0
    }
  }
}
```

Originals count confirmed version-0 snapshots only. Missing originals are excluded from first-registration features and shortfall labels. Unknown amendment evidence is null, never a negative label. Partial dates remain in raw JSON. A month-only start (YYYY-MM) is stored as the 1st of the month for the 2018-01-01 split; other partial dates leave the date column null. Condition-search hits can include basket trials. The two-pass background job loads originals before full adjacent histories; coverage can increase.
