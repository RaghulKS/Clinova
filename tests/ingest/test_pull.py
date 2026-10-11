from copy import deepcopy
from datetime import date

import pytest

from clinova.contracts import Phase, Split
from clinova.ingest.pull import iter_studies, map_trial


def study(nct="NCT12345678", *, status="COMPLETED", start="2017-12-31"):
    return {"protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": "Current title"},
        "statusModule": {"overallStatus": status, "startDateStruct": {"date": start},
                         "studyFirstPostDateStruct": {"date": "2015-02"}},
        "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"type": "ACTUAL", "count": 0}},
        "eligibilityModule": {"eligibilityCriteria": "Current criteria", "minimumAge": "18 Years"},
        "contactsLocationsModule": {"locations": [{"country": "United States"}]},
    }}


def test_missing_first_never_copies_current_features():
    current = study()
    original = deepcopy(current)
    trial = map_trial(current)
    assert current == original
    assert trial.title == "Current title" and trial.phase == Phase.phase2
    assert trial.first_version == {} and trial.eligibility_text_first == ""
    assert trial.planned_enrollment is None and trial.n_sites is None and trial.min_age_years is None
    assert trial.actual_enrollment == 0 and trial.eligibility_text_current == "Current criteria"
    assert trial.current["_clinova_provenance"]["discovery_metadata_fields"] == ["title", "phase"]


def test_original_features_are_kept_separate_and_zero_is_valid():
    current = study(start="2018-01-01")
    first = study()
    p = first["protocolSection"]
    p["identificationModule"]["briefTitle"] = "Original title"
    p["eligibilityModule"] = {"eligibilityCriteria": "Original criteria", "minimumAge": "6 Months"}
    p["designModule"]["enrollmentInfo"] = {"type": "ESTIMATED", "count": 0}
    p["designModule"]["phases"] = ["PHASE2", "PHASE3"]
    p["contactsLocationsModule"]["locations"] = []
    trial = map_trial(current, first, first_version_date="2015-01-03", history_source="versions")
    assert trial.title == "Original title" and trial.phase == Phase.phase2_3
    assert trial.eligibility_text_first == "Original criteria" and trial.min_age_years == 0.5
    assert trial.planned_enrollment == trial.actual_enrollment == trial.n_sites == trial.n_countries == 0
    assert trial.first_version_date == date(2015, 1, 3) and trial.split == Split.test
    p["eligibilityModule"]["eligibilityCriteria"] = "changed"
    assert trial.eligibility_text_first == "Original criteria"


@pytest.mark.parametrize("start", [None, "2017", "2017-13", "2017-02-30"])
def test_unknown_or_partial_start_is_pending_and_raw_precision_survives(start):
    trial = map_trial(study(start=start))
    assert trial.start_date is None and trial.first_posted is None and trial.split == Split.pending
    assert trial.current["statusModule"]["startDateStruct"]["date"] == start
    assert "split_note" in trial.current["_clinova_provenance"]


@pytest.mark.parametrize("status,start,day,split", [
    ("COMPLETED", "2017-12", date(2017, 12, 1), Split.train),
    ("TERMINATED", "2018-01", date(2018, 1, 1), Split.test),
    ("RECRUITING", "2016-06", date(2016, 6, 1), Split.pending),
])
def test_month_only_start_maps_to_first_of_month(status, start, day, split):
    trial = map_trial(study(status=status, start=start))
    assert trial.start_date == day and trial.split == split
    assert trial.current["statusModule"]["startDateStruct"]["date"] == start
    assert "split_note" not in trial.current["_clinova_provenance"]


@pytest.mark.parametrize("status,start,split", [
    ("COMPLETED", "2017-12-31", Split.train),
    ("TERMINATED", "2018-01-01", Split.test),
    ("WITHDRAWN", "2020-01-01", Split.test),
    ("RECRUITING", "2010-01-01", Split.pending),
])
def test_split_boundary(status, start, split):
    assert map_trial(study(status=status, start=start)).split == split


@pytest.mark.parametrize("phases", [["PHASE1", "PHASE2"], ["PHASE1"], [], ["NA"], ["PHASE2", "PHASE4"]])
def test_reject_unsupported_phases(phases):
    current = study()
    current["protocolSection"]["designModule"]["phases"] = phases
    with pytest.raises(ValueError, match="Unsupported phases"):
        map_trial(current)


def test_mismatched_original_is_rejected():
    with pytest.raises(ValueError, match="IDs differ"):
        map_trial(study(), study("NCT87654321"))


class Client:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.requests = []

    def get(self, path, params=None):
        self.requests.append((path, dict(params)))
        return next(self.pages)


def test_pagination_filters_and_deduplication():
    a, b, c = [study(f"NCT0000000{i}") for i in range(1, 4)]
    client = Client([{"studies": [a], "nextPageToken": "opaque"}, {"studies": [a, b]},
                     {"studies": [b]}, {"studies": [a, b, c]}])
    assert list(iter_studies(client)) == [a, b, c]
    params = [p for _, p in client.requests]
    assert params[1]["pageToken"] == "opaque"
    assert all('AREA[ConditionSearch]("non-small cell lung cancer")' in p["filter.advanced"] for p in params)
    assert all('NOT AREA[Phase]PHASE1' in p["filter.advanced"] for p in params)
    assert params[0]["filter.overallStatus"] == "TERMINATED"
    assert params[2]["filter.overallStatus"] == "COMPLETED,WITHDRAWN"
    assert "filter.overallStatus" not in params[3] and "pageToken" not in params[3]


def test_limit_stops_without_extra_requests():
    client = Client([{"studies": [study()], "nextPageToken": "unused"}])
    assert len(list(iter_studies(client, limit=1))) == 1
    assert len(client.requests) == 1
    assert list(iter_studies(Client([]), limit=0)) == []
    with pytest.raises(ValueError, match="nonnegative"):
        list(iter_studies(Client([]), limit=-1))
    with pytest.raises(ValueError, match="scope"):
        list(iter_studies(Client([]), scope="unknown"))


def test_repeated_token_fails_instead_of_looping():
    client = Client([{"nextPageToken": "same"}, {"nextPageToken": "same"}])
    with pytest.raises(ValueError, match="pagination token"):
        list(iter_studies(client))
