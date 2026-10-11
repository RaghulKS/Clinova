"""Licensed synthetic life context, importance-resampled to NSCLC age/sex margins.

No network calls happen during cohort generation. Install the optional source with
``python -m scripts.get_personas``. Missing, invalid, or unlicensed data uses the
same deterministic interface with a census-inspired synthetic fallback. Social
priors are assumptions, not measured patient properties; see PERSONAS_ATTRIBUTION.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import yaml

from clinova.common.config import get_settings

LOGGER = logging.getLogger(__name__)
DATASET = "nvidia/Nemotron-Personas-USA"
SAMPLE_NAME = "nemotron_personas_20k.parquet"
ASSUMPTIONS_PATH = Path(__file__).with_name("assumptions.yaml")


def _demographics() -> dict:
    # Development fallback until the shared T13 YAML is installed; both retain citations.
    path = ASSUMPTIONS_PATH
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "docs/spikes/assumptions.draft.yaml"
    with path.open() as stream:
        return yaml.safe_load(stream)["demographics"]


def _source_rows() -> pd.DataFrame | None:
    path = get_settings().DATA_DIR / "personas" / SAMPLE_NAME
    if not path.exists():
        LOGGER.info("Persona source absent; using census_synthetic life-context assumptions")
        return None
    try:
        metadata = json.loads(path.with_suffix(".metadata.json").read_text())
        if not isinstance(metadata, dict):
            raise ValueError("source provenance must be a metadata object")
        if metadata.get("dataset") != DATASET or metadata.get("license") != "cc-by-4.0":
            raise ValueError("source provenance does not confirm the licensed NVIDIA dataset")
        if metadata.get("sha256"):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != metadata["sha256"]:
                raise ValueError("source checksum does not match provenance")
        fields = set(pq.read_schema(path).names)
        if not {"age", "sex", "occupation", "uuid"}.issubset(fields):
            raise ValueError("source is missing required fields")
        context_fields = {"uuid", "age", "sex", "occupation", "persona", "education_level",
                          "marital_status", "city", "state", "zipcode"}
        rows = pd.read_parquet(path, columns=sorted(fields & context_fields))
        if metadata.get("rows") is not None and len(rows) != metadata["rows"]:
            raise ValueError("source row count does not match provenance")
        rows = rows.copy()
        rows["age"] = pd.to_numeric(rows["age"], errors="coerce")
        rows["sex"] = rows["sex"].astype(str).str.strip().str.lower()
        rows = rows.loc[
            rows["age"].between(18, 100)
            & rows["age"].eq(np.floor(rows["age"]))
            & rows["sex"].isin(("male", "female"))
        ].reset_index(drop=True)
        if rows.empty:
            raise ValueError("source contains no valid adult records")
        return rows
    except (OSError, ValueError, KeyError, TypeError) as error:
        LOGGER.warning("Persona source unavailable (%s); using census_synthetic", error)
        return None


def importance_weights(rows: pd.DataFrame, demographics: dict) -> np.ndarray:
    """Target age-band × sex mass / observed cell count; preserve within-cell context.

    The target assumes independence of age and sex because the source YAML supplies
    only marginal distributions. Missing target cells cannot be repaired by weights:
    reject the source rather than quietly dropping their target probability.
    """
    ages = rows["age"].to_numpy()
    sexes = rows["sex"].to_numpy()
    weights = np.zeros(len(rows), dtype=float)
    for band in demographics["age"]["bands"]:
        low, high = band["range"]
        for sex, sex_p in demographics["sex"]["probs"].items():
            mask = (ages >= low) & (ages < high) & (sexes == sex)
            mass = band["p"] * sex_p
            count = int(mask.sum())
            if mass > 0 and count == 0:
                raise ValueError(f"source lacks target age/sex cell: {low}-{high}, {sex}")
            if count:
                weights[mask] = mass / count
    if not weights.sum() > 0:
        raise ValueError("source and target demographic support do not overlap")
    return weights / weights.sum()


def _social_priors() -> dict:
    with Path(__file__).with_name("personas_assumptions.yaml").open() as stream:
        return yaml.safe_load(stream)


def _choice(rng: np.random.Generator, probs: dict, n: int) -> np.ndarray:
    return rng.choice(list(probs), p=list(probs.values()), size=n)


def _fallback_rows(n: int, rng: np.random.Generator, demographics: dict, priors: dict) -> pd.DataFrame:
    bands = demographics["age"]["bands"]
    chosen = rng.choice(len(bands), size=n, p=[band["p"] for band in bands])
    age = np.array([rng.integers(*bands[int(i)]["range"]) for i in chosen])
    sex_probs = demographics["sex"]["probs"]
    return pd.DataFrame({
        "age": age,
        "sex": rng.choice(list(sex_probs), size=n, p=list(sex_probs.values())),
        "occupation": _choice(rng, priors["fallback_occupation"]["probs"], n),
        "education_level": _choice(rng, priors["fallback_education"]["probs"], n),
        "marital_status": _choice(rng, priors["fallback_marital_status"]["probs"], n),
    })


def sample_personas(n: int, seed: int) -> list[dict]:
    """Return all Persona fields except ``clinical``; deterministic for fixed source/seed.

    The source never supplies race/ethnicity: draw the NSCLC reference marginal
    without inferring race from a name, ZIP or narrative. Hispanic of any race uses
    the contract's exclusive label. Other/multiple is conservatively coded ``other``.
    Source narratives are retained and the imputation note is appended.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("n must be a nonnegative integer")
    if n == 0:
        return []
    rng = np.random.default_rng(seed)
    demographics = _demographics()
    priors = _social_priors()
    rows = _source_rows()
    source = "census_synthetic"
    if rows is not None:
        try:
            weights = importance_weights(rows, demographics)
        except ValueError as error:
            LOGGER.warning("Cannot reweight persona source (%s); using census_synthetic", error)
            rows = None
        else:
            rows = rows.iloc[rng.choice(len(rows), n, replace=True, p=weights)].reset_index(drop=True)
            source = "nemotron_personas_usa"
    if rows is None:
        rows = _fallback_rows(n, rng, demographics, priors)
    race_probs = demographics["race"]["probs"]
    race_labels = ["other" if label == "other_or_multiple" else label for label in race_probs]
    race = rng.choice(race_labels, size=n, p=list(race_probs.values())).astype(object)
    race[rng.random(n) < demographics["hispanic_ethnicity"]["p"]] = "hispanic"
    urbanicity = _choice(rng, priors["urbanicity"]["probs"], n)
    distance = np.array([rng.lognormal(np.log(priors["distance_km"]["median"][u]), priors["distance_km"]["sigma_log"])
                         for u in urbanicity])
    risk = _choice(rng, priors["risk_tolerance"]["probs"], n)
    result = []
    for i, row in enumerate(rows.to_dict("records")):
        age, sex = int(row["age"]), str(row["sex"])
        education = str(row.get("education_level", "high_school")).lower()
        educated = any(word in education for word in ("bachelor", "master", "doctor", "graduate", "professional"))
        education_group = "college" if educated else "other"
        literacy_p = priors["health_literacy"][education_group]
        income_p = priors["income_band"][education_group]
        occupation = row.get("occupation")
        occupation = None if pd.isna(occupation) else str(occupation).replace("_", " ")
        # NVIDIA's not_in_workforce category is direct; retirement for fallback is a prior.
        employed = None if occupation is None else occupation not in ("retired", "not in workforce", "unemployed")
        if source == "census_synthetic" and age >= priors["fallback_retirement"]["minimum_age"] and rng.random() < priors["fallback_retirement"]["p"]:
            occupation, employed = "retired", False
        married = "married" in str(row.get("marital_status", "")) and "never" not in str(row.get("marital_status", ""))
        care = priors["caregiver"]
        caregiver_p = (care["younger_p"] if age < care["younger_than_age"] else care["older_p"])
        caregiver_p += care["married_increment"] if married else 0.0
        access_prior = priors["travel_access"]
        access = "easy" if distance[i] < access_prior["easy_below_km"] else "moderate" if distance[i] < access_prior["moderate_below_km"] else "hard"
        narrative = row.get("persona")
        narrative = None if pd.isna(narrative) else str(narrative)
        location = ", ".join(str(row[k]) for k in ("city", "state", "zipcode") if k in row and pd.notna(row[k]))
        background = narrative or f"{age}-year-old synthetic adult; {occupation or 'occupation unavailable'}."
        if location:
            background += f" Source geography: {location}."
        note = "Travel, income, caregiving, risk, literacy and race/ethnicity are sampled assumptions."
        result.append({
            "persona_id": f"p-{source}-{seed}-{i:06d}", "age": age, "sex": sex,
            "race_ethnicity": str(race[i]), "occupation": occupation, "employed": employed,
            "caregiver": bool(rng.random() < caregiver_p), "travel_access": access,
            "distance_km": round(float(distance[i]), 2), "urbanicity": str(urbanicity[i]),
            "income_band": str(rng.choice(list(income_p), p=list(income_p.values()))),
            "risk_tolerance": str(risk[i]),
            "health_literacy": str(rng.choice(list(literacy_p), p=list(literacy_p.values()))),
            "narrative": f"{background} {note}",
            "source": source,
        })
    return result
