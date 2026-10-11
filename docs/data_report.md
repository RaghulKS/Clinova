# CT.gov ingestion data report

Generated from Tiger Cloud by clinova.ingest.report.

```json
{
  "total": 171,
  "status": {
    "TERMINATED": 171
  },
  "split": {
    "train": 27,
    "pending": 96,
    "test": 48
  },
  "originals": 171,
  "version_events": 0,
  "labels": {
    "accrual_fail": {
      "known": 69,
      "positive": 25,
      "prevalence": 0.36231884057971014
    },
    "shortfall": {
      "known": 71,
      "positive": 58,
      "prevalence": 0.8169014084507042
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
