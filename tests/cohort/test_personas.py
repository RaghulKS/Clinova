"""Persona and download tests use local synthetic rows and mocked HTTP only."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from clinova.cohort import personas
from clinova.contracts import RACE_ETHNICITIES, Persona
from scripts.get_personas import HF_API, VIEWER_API, download_personas


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(personas, "get_settings", lambda: SimpleNamespace(DATA_DIR=tmp_path))
    return tmp_path


def source_records() -> list[dict]:
    return [
        {"uuid": f"source-{i}", "age": age, "sex": sex, "occupation": "not_in_workforce",
         "education_level": "some_college", "marital_status": "married_present",
         "city": "Example city", "state": "CA", "zipcode": "00000", "persona": f"Source narrative {i}."}
        for i, (age, sex) in enumerate((age, sex) for age in (40, 60, 70, 80)
                                       for sex in ("Female", "Male"))
    ]


def install_source(data_dir: Path, records: list[dict] | None = None, license_name: str = "cc-by-4.0"):
    path = data_dir / "personas" / personas.SAMPLE_NAME
    path.parent.mkdir(parents=True)
    pd.DataFrame(records or source_records()).to_parquet(path)
    path.with_suffix(".metadata.json").write_text(json.dumps({"dataset": personas.DATASET, "license": license_name}))
    return path


def test_fallback_deterministic_valid_and_complete(data_dir):
    first = personas.sample_personas(1000, 19)
    assert first == personas.sample_personas(1000, 19)
    assert first != personas.sample_personas(1000, 20)
    assert len({p["persona_id"] for p in first}) == 1000
    expected_fields = set(Persona.model_fields) - {"clinical"}
    assert all(set(p) == expected_fields for p in first)
    assert all(p["source"] == "census_synthetic" for p in first)
    assert all(18 <= p["age"] < 91 and p["race_ethnicity"] in RACE_ETHNICITIES for p in first)
    assert {p["race_ethnicity"] for p in first} >= {"hispanic", "asian", "white", "black", "other"}
    assert personas.sample_personas(0, 19) == []


@pytest.mark.parametrize("n", [-1, 0.5, True])
def test_invalid_size(n):
    with pytest.raises(ValueError, match="nonnegative integer"):
        personas.sample_personas(n, 42)


def test_source_mapping_keeps_real_fields_and_unique_ids(data_dir):
    install_source(data_dir)
    result = personas.sample_personas(500, 42)
    assert result == personas.sample_personas(500, 42)
    assert all(p["source"] == "nemotron_personas_usa" for p in result)
    assert all(p["age"] in (40, 60, 70, 80) for p in result)
    assert all(p["occupation"] == "not in workforce" and not p["employed"] for p in result)
    assert all("Source narrative" in p["narrative"] and "Example city" in p["narrative"] for p in result)
    assert len({p["persona_id"] for p in result}) == 500


def test_importance_weight_target_cell_masses():
    rows = pd.DataFrame(source_records() + source_records()[:3])
    rows["sex"] = rows["sex"].str.lower()
    demographics = personas._demographics()
    weights = personas.importance_weights(rows, demographics)
    assert weights.sum() == pytest.approx(1)
    for band in demographics["age"]["bands"]:
        low, high = band["range"]
        for sex, probability in demographics["sex"]["probs"].items():
            mask = rows["age"].between(low, high, inclusive="left") & rows["sex"].eq(sex)
            assert weights[mask].sum() == pytest.approx(band["p"] * probability)


def test_sample_marginals_approximate_yaml(data_dir):
    install_source(data_dir)
    result = pd.DataFrame(personas.sample_personas(20000, 42))
    demographics = personas._demographics()
    for band in demographics["age"]["bands"]:
        low, high = band["range"]
        observed = result["age"].between(low, high, inclusive="left").mean()
        assert observed == pytest.approx(band["p"], abs=0.015)
    assert result["sex"].eq("male").mean() == pytest.approx(0.536, abs=0.015)
    assert result["race_ethnicity"].eq("hispanic").mean() == pytest.approx(0.06, abs=0.012)


@pytest.mark.parametrize("broken", ["unlicensed", "invalid", "coverage", "missing_metadata", "checksum"])
def test_bad_source_falls_back(data_dir, broken, caplog):
    path = install_source(data_dir, license_name="research-only" if broken == "unlicensed" else "cc-by-4.0")
    if broken == "invalid":
        path.write_bytes(b"not a parquet file")
    elif broken == "coverage":
        pd.DataFrame(source_records()[:2]).to_parquet(path)
    elif broken == "missing_metadata":
        path.with_suffix(".metadata.json").unlink()
    elif broken == "checksum":
        metadata_path = path.with_suffix(".metadata.json")
        metadata = json.loads(metadata_path.read_text())
        metadata["sha256"] = "incorrect"
        metadata_path.write_text(json.dumps(metadata))
    result = personas.sample_personas(100, 42)
    assert all(p["source"] == "census_synthetic" for p in result)
    assert "using census_synthetic" in caplog.text


def test_download_mocked_license_provenance_and_resume(data_dir, respx_mock):
    card = {"sha": "revision1", "cardData": {"license": "cc-by-4.0"}}
    respx_mock.get(HF_API).mock(return_value=httpx.Response(200, json=card))
    respx_mock.get(f"{VIEWER_API}/size").mock(return_value=httpx.Response(200, json={
        "size": {"splits": [{"split": "train", "config": "default", "num_rows": 200}]}}))
    rows_calls = []

    def response(request):
        offset = int(request.url.params["offset"])
        rows_calls.append(offset)
        records = source_records()
        rows = [{"row": dict(records[i % len(records)], uuid=f"uuid-{offset + i}")} for i in range(100)]
        return httpx.Response(200, json={"rows": rows})

    respx_mock.get(f"{VIEWER_API}/rows").mock(side_effect=response)
    with httpx.Client() as client:
        path = download_personas(150, 42, data_dir, client=client)
        first = pd.read_parquet(path)
        second_path = download_personas(150, 42, data_dir, client=client)
    pd.testing.assert_frame_equal(first, pd.read_parquet(second_path))
    assert len(first) == 150 and first["uuid"].is_unique
    assert len(rows_calls) == 2  # second call resumes entirely from cached pages
    metadata = json.loads(path.with_suffix(".metadata.json").read_text())
    assert metadata["license"] == "cc-by-4.0"
    assert metadata["retrieval_revision"] == "revision1"
    assert metadata["rows"] == 150 and len(metadata["sha256"]) == 64


def test_download_rejects_unlicensed_source(data_dir, respx_mock):
    respx_mock.get(HF_API).mock(return_value=httpx.Response(200, json={"cardData": {"license": "other"}}))
    with httpx.Client() as client, pytest.raises(ValueError, match="unverified license"):
        download_personas(10, data_dir=data_dir, client=client)
    assert not (data_dir / "personas").exists()

