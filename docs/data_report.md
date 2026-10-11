# CT.gov ingestion data report

Generated from Tiger Cloud by clinova.ingest.report.

```json
{
  "total": 1879,
  "status": {
    "COMPLETED": 1373,
    "TERMINATED": 419,
    "WITHDRAWN": 87
  },
  "split": {
    "train": 1441,
    "pending": 85,
    "test": 353
  },
  "originals": 1879,
  "version_events": 0,
  "labels": {
    "accrual_fail": {
      "known": 1742,
      "positive": 170,
      "prevalence": 0.09758897818599312
    },
    "shortfall": {
      "known": 1135,
      "positive": 540,
      "prevalence": 0.47577092511013214
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
