# CT.gov ingestion data report

Generated from Tiger Cloud by clinova.ingest.report.

```json
{
  "total": 81,
  "status": {
    "TERMINATED": 81
  },
  "split": {
    "pending": 43,
    "train": 16,
    "test": 22
  },
  "originals": 81,
  "version_events": 0,
  "labels": {
    "accrual_fail": {
      "known": 34,
      "positive": 12,
      "prevalence": 0.35294117647058826
    },
    "shortfall": {
      "known": 37,
      "positive": 29,
      "prevalence": 0.7837837837837838
    },
    "amend": {
      "known": 0,
      "positive": 0,
      "prevalence": null
    }
  }
}
```

Originals count confirmed version-0 snapshots only. Missing originals are excluded from first-registration features and shortfall labels. Unknown amendment evidence is null, never a negative label. Partial dates remain in raw JSON; exact date columns are null. Condition-search hits can include basket trials. The two-pass background job loads originals before full adjacent histories; coverage can increase.
