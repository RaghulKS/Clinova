"""Regenerate tests/fixtures/personas.json: 200 synthetic advanced-NSCLC personas (fictional people).

Marginals are rough published-style figures for a second-line advanced NSCLC population, hard-coded
here for a test fixture only (the real cohort sampler is clinova/cohort with sourced assumptions).

    uv run --frozen python tests/fixtures/make_personas.py
"""

import json
from pathlib import Path

import numpy as np
from pydantic import TypeAdapter

from clinova.contracts import Persona

OUT = Path(__file__).parent / "personas.json"
N = 200

JOBS = ["teacher", "truck driver", "nurse", "accountant", "construction worker", "retail manager", "farmer",
        "electrician", "office administrator", "restaurant cook", "software developer", "hairdresser",
        "warehouse supervisor", "real estate agent", "mechanic", "social worker", "bus driver",
        "small business owner", "postal worker", "machinist"]
CARES_FOR = {"younger": ["a teenage son", "two young children", "an elderly mother", "a frail father",
                         "a disabled brother"],
             "older": ["a spouse with dementia", "a partner recovering from a stroke", "two grandchildren after school",
                       "a disabled adult daughter", "a frail older sister"]}
OUTLOOK = {"low": ["is wary of experimental drugs", "worries about side effects more than most",
                   "prefers to stick with what the oncologist knows"],
           "medium": ["wants to understand the trade-offs before deciding", "asks a lot of questions at appointments",
                      "would try something new if the doctor recommends it"],
           "high": ["wants to try anything that might buy more time", "is keen to be part of research",
                    "says there is little left to lose"]}


def pick(rng, options, probs):
    probs = np.asarray(probs, dtype=float)
    return options[rng.choice(len(options), p=probs / probs.sum())]


def lognormal(rng, median, sigma):
    return float(np.exp(rng.normal(np.log(median), sigma)))


def clinical(rng, age, sex, race):
    asian = race == "asian"
    smoking = pick(rng, ["never", "former", "current"], [45, 35, 20] if asian else [13, 55, 32])
    never = smoking == "never"
    histology = pick(rng, ["adenocarcinoma", "squamous", "large_cell", "nsclc_nos", "other"],
                     [82, 6, 2, 7, 3] if never else [56, 29, 3, 9, 3])
    squamous = histology == "squamous"

    def biomarker(p_pos, p_unknown, blocked):
        if rng.random() < p_unknown:
            return "unknown"
        return "positive" if not blocked and rng.random() < p_pos else "negative"

    p_egfr = 0.02 if squamous else min(0.6, (0.45 if never else 0.09) * (1.8 if asian else 1.0))
    egfr = biomarker(p_egfr, 0.4 if squamous else 0.03, False)
    alk = biomarker(0.005 if squamous else (0.16 if never else 0.03), 0.4 if squamous else 0.04, egfr == "positive")
    ros1 = biomarker(0.04 if never else 0.004, 0.15, "positive" in (egfr, alk))
    driver = "positive" in (egfr, alk, ros1)

    u = rng.random()
    pdl1 = round(rng.uniform(50, 100)) if u < 0.30 else round(rng.uniform(1, 49)) if u < 0.65 else 0
    ecog = int(pick(rng, [0, 1, 2, 3], [30, 50, 15, 5]))
    stage = pick(rng, ["IIIA", "IIIB", "IIIC", "IVA", "IVB"], [1, 7, 5, 37, 50])
    brain = pick(rng, ["none", "treated_stable", "untreated"], [75, 15, 10])
    lines = int(pick(rng, [0, 1, 2, 3], [3, 64, 27, 6]))
    prior_platinum = lines > 0 and rng.random() < (0.6 if driver and lines == 1 else 0.97)
    prior_io = lines > 0 and rng.random() < (0.5 if driver else 0.96)

    liver = rng.random() < 0.18  # liver metastases drive the AST/ALT/bilirubin tails
    weight = float(np.clip(rng.normal(79 if sex == "male" else 66, 15 if sex == "male" else 14) - 2 * ecog, 40, 140))
    creat = lognormal(rng, 0.9 if sex == "male" else 0.72, 0.25)
    crcl = (140 - age) * weight / (72 * creat) * (0.85 if sex == "female" else 1.0)  # Cockcroft-Gault
    ast = lognormal(rng, 1.1 if liver else 0.65, 0.55 if liver else 0.4)
    return {
        "stage": stage,
        "histology": histology,
        "ecog": ecog,
        "egfr_status": egfr,
        "alk_status": alk,
        "ros1_status": ros1,
        "pdl1_tps": float(pdl1),
        "brain_mets": brain,
        "leptomeningeal": bool(rng.random() < 0.02),
        "prior_lines": lines,
        "prior_platinum": bool(prior_platinum),
        "prior_immunotherapy": bool(prior_io),
        "crcl_ml_min": round(float(np.clip(crcl, 5, 250)), 1),
        "anc": round(lognormal(rng, 4.4, 0.55), 2),
        "platelets": float(round(lognormal(rng, 240, 0.45))),
        "hemoglobin": round(float(np.clip(rng.normal(12.2 - 0.5 * ecog, 1.5), 6, 17)), 1),
        "ast_x_uln": round(ast, 2),
        "alt_x_uln": round(ast * lognormal(rng, 1.0, 0.25), 2),
        "bilirubin_x_uln": round(lognormal(rng, 0.75 if liver else 0.5, 0.5 if liver else 0.38), 2),
        "life_expectancy_months": round(lognormal(rng, {0: 14, 1: 10, 2: 5, 3: 2.5}[ecog], 0.45), 1),
        "measurable_disease": bool(rng.random() < 0.94),
        "autoimmune_disease": bool(rng.random() < 0.04),
        "interstitial_lung_disease": bool(rng.random() < 0.03),
        "active_infection": bool(rng.random() < 0.03),
        "cardiac_condition": bool(rng.random() < (0.12 if age >= 70 else 0.05)),
        "pregnancy": bool(sex == "female" and age < 50 and rng.random() < 0.1),
        "consent_capacity": bool(rng.random() >= 0.02),
        "smoking_status": smoking,
        "weight_kg": round(weight, 1),
        "creatinine_mg_dl": round(creat, 2),
    }


def persona(rng, i, sex, race):
    age = int(np.clip(round(rng.normal(68, 9)), 40, 88))
    job = pick(rng, JOBS, [1] * len(JOBS))
    if age >= 66 or rng.random() < 0.15:
        occupation, employed = "retired", False
    elif rng.random() < 0.3:
        occupation, employed = pick(rng, ["on disability", "homemaker", "on medical leave"], [2, 1, 2]), False
    else:
        occupation, employed = job, True
    urbanicity = pick(rng, ["urban", "suburban", "rural"], [30, 45, 25])
    distance = round(lognormal(rng, {"urban": 12, "suburban": 25, "rural": 80}[urbanicity], 0.5), 1)
    income = pick(rng, ["low", "middle", "high"], [35, 45, 20])
    if distance > 60 or (income == "low" and rng.random() < 0.4):
        travel = "hard"
    elif distance < 20 and income != "low":
        travel = "easy"
    else:
        travel = "moderate"
    caregiver = bool(rng.random() < 0.2)
    risk = pick(rng, ["low", "medium", "high"], [30, 50, 20])
    literacy = pick(rng, ["low", "adequate", "high"], [25, 55, 20])

    who = f"retired {job}" if occupation == "retired" else job if employed else f"former {job} now {occupation}"
    cares_for = CARES_FOR["older" if age >= 65 else "younger"]
    care = f", cares for {pick(rng, cares_for, [1] * len(cares_for))}" if caregiver else ""
    ride = "" if travel != "hard" else " (a long drive each way)" if distance > 60 else ", depends on others for rides"
    narrative = (f"{age}-year-old {who} living in {'an' if urbanicity == 'urban' else 'a'} {urbanicity} area {round(distance)} km from the cancer center"
                 f"{ride}{care}; {pick(rng, OUTLOOK[risk], [1, 1, 1])}.")
    return {
        "persona_id": f"fx-{i:04d}",
        "age": age,
        "sex": sex,
        "race_ethnicity": race,
        "occupation": occupation,
        "employed": employed,
        "caregiver": caregiver,
        "travel_access": travel,
        "distance_km": distance,
        "urbanicity": urbanicity,
        "income_band": income,
        "risk_tolerance": risk,
        "health_literacy": literacy,
        "narrative": narrative,
        "source": "fixture",
        "clinical": clinical(rng, age, sex, race),
    }


def main():
    rng = np.random.default_rng(42)
    # exact quotas for sex (52% male) and race (75/11/6/6/2), shuffled; everything else is sampled
    sexes = rng.permutation(["male"] * 104 + ["female"] * 96)
    races = rng.permutation(["white"] * 150 + ["black"] * 22 + ["hispanic"] * 12 + ["asian"] * 12
                            + ["native_american", "multiracial", "multiracial", "other"])
    adapter = TypeAdapter(list[Persona])
    personas = adapter.validate_python([persona(rng, i, str(sexes[i - 1]), str(races[i - 1])) for i in range(1, N + 1)])
    OUT.write_text(json.dumps(adapter.dump_python(personas, mode="json"), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(personas)} personas to {OUT}")


if __name__ == "__main__":
    main()
