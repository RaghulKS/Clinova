"""Deterministic synthetic NSCLC cohort with licensed persona life context."""
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from clinova.cohort.personas import sample_personas
from clinova.cohort.traits import sample_traits
from clinova.contracts import COHORT_COLUMNS, PERSONA_COLUMNS, Persona, Setting


def generate_cohort(n: int, seed: int, setting: Setting = Setting.any) -> list[Persona]:
    """Draw life context and conditional traits; uses the census fallback if no data exist.

    Persona source is recorded on every object. Downloading never occurs implicitly.
    """
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("n must be a nonnegative integer")
    setting = Setting(setting)
    contexts = sample_personas(n, seed)
    traits = sample_traits(contexts, seed + 1, setting)
    return [Persona(**p, clinical=t) for p, t in zip(contexts, traits, strict=True)]


def cohort_frame(personas: Sequence[Persona]) -> pd.DataFrame:
    """Flatten validated personas in input order, with exactly COHORT_COLUMNS."""
    rows = []
    for persona in personas:
        row = {key: getattr(persona, key) for key in PERSONA_COLUMNS}
        row.update(persona.clinical.model_dump(mode="json"))
        rows.append(row)
    return pd.DataFrame(rows, columns=COHORT_COLUMNS)


def generate_cohort_df(n: int, seed: int, setting: Setting = Setting.any) -> pd.DataFrame:
    """Generate a dataframe sharing the validated object path and deterministic seed."""
    return cohort_frame(generate_cohort(n, seed, setting))
