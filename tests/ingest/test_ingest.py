"""Offline trust-boundary checks for the ingestion pipeline."""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from clinova.contracts import TrialRecord
from clinova.ingest.ctgov import CTGovClient
from clinova.ingest.history import HistoryResult, fetch_history, normalize
from clinova.ingest.labels import StopReason, label_trial
from clinova.ingest.pull import map_trial


@respx.mock
def test_cache_and_forbidden_no_retry(tmp_path):
    current = respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json={"protocolSection": {}}))
    blocked = respx.get("https://clinicaltrials.gov/api/int/studies/NCT00000001").mock(
        return_value=httpx.Response(403))
    with CTGovClient(tmp_path, interval=0) as client:
        assert client.get("/api/v2/studies/NCT00000001") == {"protocolSection": {}}
        client.get("/api/v2/studies/NCT00000001")
        with pytest.raises(httpx.HTTPStatusError):
            client.get("/api/int/studies/NCT00000001")
    assert current.call_count == 1
    assert blocked.call_count == 1


def test_normalization():
    assert normalize("<p>Age &gt; 18</p>\n years") == "Age > 18 years"


@respx.mock
def test_recorded_original_maps_without_network(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "NCT04405401.json").read_text(encoding="utf-8"))
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT04405401").mock(
        return_value=httpx.Response(200, json=fixture["current"]))
    respx.get("https://clinicaltrials.gov/api/int/studies/NCT04405401/history/0").mock(
        return_value=httpx.Response(200, json=fixture["first"]))
    with CTGovClient(tmp_path, interval=0) as client:
        current = client.get("/api/v2/studies/NCT04405401")
        first = client.get("/api/int/studies/NCT04405401/history/0")
    mapped = map_trial(current, first["study"], first_version_date=date(2020, 5, 27))
    assert mapped.planned_enrollment == 68
    assert mapped.first_version["designModule"]["enrollmentInfo"]["type"] == "ESTIMATED"
    assert mapped.status == "TERMINATED" and mapped.split == "test"


def test_zero_based_history_and_adjacent_events():
    class Client:
        def get(self, path, params=None):
            if path.endswith("/NCT00000001"):
                return {"history": {"changes": [{"version": i, "date": f"2020-01-0{i+1}"} for i in range(2)]}}
            version = int(path[-1])
            return {"studyVersion": version, "study": {"protocolSection": {
                "eligibilityModule": {"eligibilityCriteria": "Age > " + str(18 - version)},
                "designModule": {"enrollmentInfo": {"count": 100 - version, "type": "ESTIMATED"}},
            }}}
    result = fetch_history(Client(), "NCT00000001", {}, full=True)
    assert result.complete and result.first is not None and result.source == "versions"
    assert result.first_version_date == date(2020, 1, 1)
    assert {e.field for e in result.events} == {"eligibility", "enrollment_count"}
    assert all(e.version_no == 1 for e in result.events)


def trial():
    return TrialRecord(nct_id="NCT00000001", title="Example", phase="phase2", status="TERMINATED",
                       why_stopped="Poor accrual", planned_enrollment=100, actual_enrollment=0,
                       start_date=date(2019, 1, 1), eligibility_text_first="Age >= 18", split="test")


def test_labels_zero_actual_and_missing_original(monkeypatch):
    monkeypatch.setattr("clinova.ingest.labels.llm.generate_json", lambda *a, **k: StopReason(category="accrual"))
    history = HistoryResult(first={"protocolSection": {}}, complete=True)
    labeled, _ = label_trial(trial(), history)
    assert labeled.label_accrual_fail is True and labeled.label_shortfall is True
    assert labeled.label_amend is False
    labeled, _ = label_trial(trial(), replace(history, first=None, complete=False))
    assert labeled.label_shortfall is None and labeled.label_amend is None
    pending = trial().model_copy(update={"status": "RECRUITING", "split": "pending"})
    labeled, _ = label_trial(pending, history)
    assert labeled.label_accrual_fail is None and labeled.label_shortfall is None
