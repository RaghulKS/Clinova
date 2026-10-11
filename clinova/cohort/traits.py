"""Conditional clinical sampling from sourced YAML plus explicitly marked priors.

This generates synthetic state, not individual prognosis. One exclusive driver category
is sampled. Clinical associations are conditional probabilities, not causal effects.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from clinova.contracts import ClinicalTraits, Setting


@lru_cache(maxsize=1)
def load_assumptions() -> dict[str, Any]:
    """Read the shipped distribution file; no settings or secrets are copied."""
    return yaml.safe_load(Path(__file__).with_name("assumptions.yaml").read_text())


def _categorical(rng: np.random.Generator, probs: Mapping[str, float], n: int) -> np.ndarray:
    values = np.array(list(probs), dtype=object)
    weights = np.array(list(probs.values()), dtype=float)
    return rng.choice(values, size=n, p=weights / weights.sum())


def _odds_adjust(p: np.ndarray, ratio: np.ndarray | float) -> np.ndarray:
    return p * ratio / (1 - p + p * ratio)


def sample_traits(
    personas: Sequence[Mapping[str, Any]], seed: int, setting: Setting = Setting.any,
) -> list[ClinicalTraits]:
    """Sample traits for life-context rows, deterministic for ordered input and seed."""
    setting = Setting(setting)
    n = len(personas)
    if not n:
        return []
    rng = np.random.default_rng(seed)
    a = load_assumptions()
    priors = a["implementation_priors"]
    age = np.array([p["age"] for p in personas])
    female = np.array([p["sex"] == "female" for p in personas])
    race = np.array([p["race_ethnicity"] for p in personas])
    bands = np.digitize(age, [50, 65, 75])
    band_keys = ["18_50", "50_65", "65_75", "75_91"]

    stage_groups = _categorical(rng, a["disease"]["current_stage_at_screening"]["probs"], n)
    subtype = rng.random(n) < 0.5
    stages = np.where(stage_groups == "IV", np.where(subtype, "IVA", "IVB"), np.where(subtype, "IIIB", "IIIC"))
    hist_groups = _categorical(rng, a["disease"]["histology"]["probs"], n)
    histology = np.where(hist_groups == "squamous", "squamous", priors["nonsquamous_label"]["value"])
    smoking_groups = _categorical(rng, a["demographics"]["smoking_status"]["probs"], n)
    former = rng.random(n) < priors["smoking_ever_split"]["former_fraction"]
    smoking = np.where(smoking_groups == "never", "never", np.where(former, "former", "current"))
    ecog = np.empty(n, dtype=int)
    for i, key in enumerate(band_keys):
        indices = np.flatnonzero(bands == i)
        draws = _categorical(rng, a["performance_status"]["ecog_given_age_band"]["table"][key], len(indices))
        ecog[indices] = [3 if x == "3_plus" else int(x) for x in draws]

    biom = a["biomarkers"]
    probabilities = []
    for key in ["egfr_sensitizing", "alk_rearrangement", "ros1_rearrangement"]:
        table = biom[key]["table"]
        probabilities.append(np.array([table[h][s] for h, s in zip(hist_groups, smoking_groups, strict=True)]))
    egfr_p, alk_p, ros1_p = probabilities
    adj = biom["egfr_sensitizing"]["race_adjustment"]
    egfr_p = np.where(race == "asian", np.minimum(_odds_adjust(egfr_p, adj["or_asian"]), adj["cap"]), egfr_p)
    alk_p = _odds_adjust(alk_p, np.where(race == "hispanic", 2.0, 1.0))
    age_prior = priors["driver_age"]
    age_ratio = age_prior["alk_ros1_odds_ratio_per_decade"] ** ((age - age_prior["reference_age"]) / 10)
    alk_p, ros1_p = _odds_adjust(alk_p, age_ratio), _odds_adjust(ros1_p, age_ratio)
    probs = np.stack([egfr_p, alk_p, ros1_p], axis=1)
    # Defensive normalization preserves mutually exclusive categories for extreme priors.
    probs /= np.maximum(1, probs.sum(axis=1))[:, None]
    driver_draw = rng.random(n)
    cumulative = probs.cumsum(axis=1)
    driver = np.sum(driver_draw[:, None] >= cumulative, axis=1)
    egfr = np.where(driver == 0, "positive", "negative")
    alk = np.where(driver == 1, "positive", "negative")
    ros1 = np.where(driver == 2, "positive", "negative")
    tps_probs = np.tile(list(biom["pdl1_tps_band"]["probs"].values()), (n, 1))
    tps_probs[driver != 3, 2] *= priors["pdl1_driver_positive"]["high_probability_multiplier"]
    tps_probs /= tps_probs.sum(axis=1)[:, None]
    tps_band = np.sum(rng.random(n)[:, None] >= tps_probs.cumsum(axis=1), axis=1)
    tps = np.where(tps_band == 0, rng.uniform(0, 1, n), np.where(tps_band == 1, rng.uniform(1, 50, n), rng.uniform(50, 100, n)))

    brain = a["brain_metastases"]
    brain_p = np.array([brain["any_brain_mets_given_histology"]["table"][h] for h in hist_groups])
    brain_p = _odds_adjust(brain_p, brain["age_effect"]["or_per_10_years"] ** ((age - 69) / 10))
    brain_any = rng.random(n) < brain_p
    brain_status = _categorical(rng, brain["status_given_any_brain_mets"]["probs"], n)
    brain_status = np.where(brain_status == "treated_and_stable", "treated_stable", brain_status)
    brain_status = np.where(brain_any, brain_status, "none")

    line_probs = a["prior_therapy"]["prior_lines_at_screening_for_untargeted_pool"]["probs"].copy()
    if setting == Setting.first_line:
        line_probs = {"0": 1.0}
    elif setting == Setting.second_line_plus:
        line_probs.pop("0")
    line_draw = _categorical(rng, line_probs, n)
    lines = np.array([rng.integers(2, priors["prior_therapy"]["max_lines"] + 1) if x == "2_plus" else int(x) for x in line_draw])
    prior_platinum = (lines > 0) & (rng.random(n) < priors["prior_therapy"]["platinum_given_prior_line"])
    prior_io = (lines > 0) & (rng.random(n) < priors["prior_therapy"]["immunotherapy_given_prior_line"])

    organ = a["organ_function"]
    weight_cfg = organ["weight_kg_for_cockcroft_gault"]
    mean_weight = np.array([weight_cfg["mean_kg"][p["sex"]][band_keys[b]] for p, b in zip(personas, bands, strict=True)])
    sigma = weight_cfg["sigma_log"]
    weight = rng.lognormal(np.log(mean_weight) - sigma ** 2 / 2, sigma) * weight_cfg["weight_multiplier"]
    scr_cfg = organ["serum_creatinine_mg_dl"]
    scr_med = np.where(female, scr_cfg["median"]["female"], scr_cfg["median"]["male"])
    scr = np.maximum(scr_cfg["floor"], rng.lognormal(np.log(scr_med), scr_cfg["sigma_log"]))
    crcl = (140 - age) * weight / (72 * scr) * np.where(female, 0.85, 1.0)

    labs = {}
    lab_prior = priors["lab_values"]
    frailty = np.where(ecog >= 2, lab_prior["frailty_multiplier"], 1.0)
    frailty /= frailty.mean()
    for field, key in [("anc", "anc_lt_1_5"), ("platelets", "platelets_lt_100"), ("hemoglobin", "hemoglobin_lt_9"), ("liver", "ast_or_alt_gt_2_5x_uln"), ("bilirubin", "bilirubin_gt_1_5x_uln")]:
        cfg = a["labs_baseline_abnormality"]["p_fail"][key]
        p = np.where(lines > 0, cfg["later_line"], cfg["first_line"])
        failure = rng.random(n) < np.minimum(p * frailty, 1.0)
        normal = rng.uniform(*lab_prior["normal_ranges"][field], n)
        abnormal = rng.uniform(*lab_prior["abnormal_ranges"][field], n)
        labs[field] = np.where(failure, abnormal, normal)

    conditions = priors["additional_conditions"]
    flags = {key: rng.random(n) < conditions[key] for key in ["leptomeningeal", "measurable_disease", "autoimmune_disease", "interstitial_lung_disease", "active_infection", "cardiac_condition", "consent_capacity"]}
    pregnancy = female & (age < 50) & (rng.random(n) < conditions["pregnancy_given_female_under50"])
    life_cfg = priors["life_expectancy_months"]
    life = rng.lognormal(np.log(np.array(life_cfg["median_by_ecog"])[ecog]), life_cfg["sigma_log"])
    return [ClinicalTraits(
        stage=str(stages[i]), histology=str(histology[i]), ecog=int(ecog[i]),
        egfr_status=str(egfr[i]), alk_status=str(alk[i]), ros1_status=str(ros1[i]), pdl1_tps=float(tps[i]),
        brain_mets=str(brain_status[i]), prior_lines=int(lines[i]), prior_platinum=bool(prior_platinum[i]),
        prior_immunotherapy=bool(prior_io[i]), crcl_ml_min=float(crcl[i]), anc=float(labs["anc"][i]),
        platelets=float(labs["platelets"][i]), hemoglobin=float(labs["hemoglobin"][i]),
        ast_x_uln=float(labs["liver"][i]), alt_x_uln=float(labs["liver"][i]), bilirubin_x_uln=float(labs["bilirubin"][i]),
        life_expectancy_months=float(life[i]), pregnancy=bool(pregnancy[i]), smoking_status=str(smoking[i]),
        weight_kg=float(weight[i]), creatinine_mg_dl=float(scr[i]),
        **{k: bool(v[i]) for k, v in flags.items()},
    ) for i in range(n)]
