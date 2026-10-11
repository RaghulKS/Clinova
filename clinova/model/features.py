"""Model features (owner: T22). First-version data only. See ARCHITECTURE.md 5.6.

Leakage rule, enforced by construction: from a ``trials`` row ``build_features`` reads only
``nct_id`` and ``first_version`` (the raw version-0 protocolSection). Everything else comes from
the first-version parsed rules, the embedding of the first-version eligibility text and
simulation runs of the first version. Start year is never a feature.

Two layers:
- ``build_features(trials, protocols, sim_features)`` gives raw, unfitted features
  (thresholds in canonical units, counts, design, operations, screen, simulation).
- ``fit_ref`` learns what needs the training split (per-field medians, embedding PCA, relax
  ladders, kNN distances for the confidence badge); ``build_features(..., ref=ref)`` then swaps raw
  thresholds for ``strict_*`` (positive = stricter than the training median) and adds ``emb_pc*``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from clinova.contracts import (
    FIELD_SPECS,
    ClinicalField,
    CriteriaRule,
    ParsedProtocol,
    SimRunSummary,
)

SPLIT_DATE = date(2018, 1, 1)
TUNE_DATE = date(2015, 1, 1)
LABELS = {"accrual_fail": "label_accrual_fail", "shortfall": "label_shortfall", "amend": "label_amend"}
N_PCS = 16
SCREEN_N, SCREEN_SEED = 2000, 42

# (field, bound): "max" = highest allowed value (lower is stricter), "min" = lowest allowed (higher is stricter).
BOUNDS: tuple[tuple[str, str], ...] = (
    ("ecog", "max"), ("crcl_ml_min", "min"), ("anc", "min"), ("platelets", "min"), ("hemoglobin", "min"),
    ("ast_x_uln", "max"), ("alt_x_uln", "max"), ("bilirubin_x_uln", "max"), ("life_expectancy_months", "min"),
    ("prior_lines", "max"), ("prior_lines", "min"), ("pdl1_tps", "min"), ("brain_mets", "max"), ("stage", "min"),
    ("age", "min"), ("age", "max"), ("histology", "n"),
)
THRESHOLDS = [f"{f}_{b}" for f, b in BOUNDS]
STRICT_SIGN = {f"{f}_{b}": (1.0 if b == "min" else -1.0) for f, b in BOUNDS}  # "n" (allowed count): fewer = stricter
COMORBIDITY = ("autoimmune_disease", "interstitial_lung_disease", "active_infection", "cardiac_condition", "leptomeningeal")
SIM_COLS = ["sim_eligible_frac", "sim_enroll_rate", "sim_completion_rate", "sim_dropout_final"]
MASKING = {"NONE": 0, "SINGLE": 1, "DOUBLE": 2, "TRIPLE": 3, "QUADRUPLE": 4}
PHASE_NUM = {"phase2": 0.0, "phase2_3": 1.0, "phase3": 2.0}

# Plain words for every feature (SHAP drivers, plots).
FEATURE_NAMES = {
    "n_rules": "Number of atomic eligibility rules", "n_inclusion": "Number of inclusion rules",
    "n_exclusion": "Number of exclusion rules", "n_unparseable": "Rules too complex to encode",
    "frac_unparseable": "Share of rules too complex to encode", "n_fields": "Distinct clinical fields restricted",
    "n_comorbidity_excl": "Comorbidity exclusions (autoimmune, ILD, infection, cardiac, leptomeningeal)",
    "biomarker_required": "Driver mutation required (EGFR/ALK/ROS1 positive)",
    "prior_platinum_required": "Prior platinum chemotherapy required", "prior_io_excluded": "Prior PD-(L)1 therapy excluded",
    "text_len": "Length of the eligibility text (characters)",
    "visit_count": "Visits in the first 12 months", "visit_hours": "On-site hours in the first 12 months",
    "visit_invasive": "Invasive-procedure visits", "visit_travel": "Visits needing travel",
    "phase_num": "Phase (2, 2/3, 3)", "n_arms": "Number of arms", "randomized": "Randomized",
    "masking_level": "Masking (0 open label to 4 quadruple)", "iv_drug": "Drug intervention",
    "iv_biological": "Biological intervention", "iv_radiation": "Radiation intervention", "iv_other": "Procedure, device or other intervention",
    "log_planned_enrollment": "Planned enrollment (log)", "n_sites": "Sites at first registration",
    "n_countries": "Countries at first registration", "us_share": "Share of first-registration sites in the US",
    "sponsor_industry": "Industry sponsor", "sponsor_gov": "Government sponsor (NIH, FED, other gov)",
    "screen_eligible_frac": "Share of virtual NSCLC patients passing every rule",
    "screen_max_excl_frac": "Largest single-rule exclusion on the virtual cohort",
    "sim_eligible_frac": "Simulated eligible fraction", "sim_enroll_rate": "Simulated enrollment rate",
    "sim_completion_rate": "Simulated completion rate", "sim_dropout_final": "Simulated 12-month dropout",
}
_FIELD_WORDS = {
    "ecog": "ECOG performance status", "crcl_ml_min": "Creatinine clearance", "anc": "Neutrophil count",
    "platelets": "Platelet count", "hemoglobin": "Hemoglobin", "ast_x_uln": "AST", "alt_x_uln": "ALT",
    "bilirubin_x_uln": "Bilirubin", "life_expectancy_months": "Life expectancy", "prior_lines": "Prior therapy lines",
    "pdl1_tps": "PD-L1 TPS", "brain_mets": "Brain metastases allowed (0 none, 1 treated, 2 untreated)",
    "stage": "Stage (index I..IVB)", "age": "Age", "histology": "Histologies allowed",
}
for _f, _b in BOUNDS:
    _w = _FIELD_WORDS[_f] + {"max": " upper limit", "min": " lower limit", "n": " (count)"}[_b]
    FEATURE_NAMES[f"{_f}_{_b}"] = _w
    FEATURE_NAMES[f"strict_{_f}_{_b}"] = _w + ", strictness vs training median"
for _i in range(1, N_PCS + 1):
    FEATURE_NAMES[f"emb_pc{_i}"] = f"Eligibility-text theme {_i} (embedding PCA)"


# ---------------------------------------------------------------------------------------------
# Per-trial raw features
# ---------------------------------------------------------------------------------------------


def _allowed(field: str, rules: Sequence[CriteriaRule]) -> tuple[list, list] | None:
    """(grid, values passing every parseable rule on ``field``) or None without such rules."""
    from clinova.parse.diff import _grid, _pass_set  # T12's per-field pass-set logic, reused

    rs = [r for r in rules if r.field == field and r.parse_status == "ok"]
    if not rs:
        return None
    f = ClinicalField(field)
    try:
        grid = _grid(f, rs)
        return grid, sorted(_pass_set(f, rs, grid), key=grid.index)
    except (ValueError, TypeError, KeyError):  # out-of-vocabulary value: treat as no usable rule
        return None


def _bound(field: str, bound: str, rules: Sequence[CriteriaRule]) -> float:
    a = _allowed(field, rules)
    if a is None:
        return np.nan
    grid, ok = a
    if bound == "n":
        return float(len(ok))
    if not ok:
        return np.nan
    spec = FIELD_SPECS[ClinicalField(field)]
    if spec.kind == "ordinal":
        idx = [spec.values.index(v) for v in ok]
        lo, hi = min(idx), max(idx)
        return float(hi if hi < len(spec.values) - 1 else np.nan) if bound == "max" else float(lo if lo > 0 else np.nan)
    lo_lim, hi_lim = spec.min if spec.min is not None else -np.inf, spec.max if spec.max is not None else np.inf
    num = [float(g) for g in grid if lo_lim <= g <= hi_lim]  # only plausible values count
    ok = [float(g) for g in ok if lo_lim <= g <= hi_lim]
    if not ok or not num:
        return np.nan
    if bound == "max":
        return max(ok) if max(ok) < max(num) else np.nan  # passing the outermost grid point = unbounded
    return min(ok) if min(ok) > min(num) else np.nan


def rule_features(rules: Sequence[CriteriaRule] | None) -> dict[str, float]:
    """Counts and per-field bounds from first-version rules (NaN when the trial was never parsed)."""
    if rules is None:
        return dict.fromkeys(["n_rules", "n_inclusion", "n_exclusion", "n_unparseable", "frac_unparseable", "n_fields",
                              "n_comorbidity_excl", "biomarker_required", "prior_platinum_required",
                              "prior_io_excluded", *[t for t in THRESHOLDS if not t.startswith("age_")]], np.nan)
    ok = [r for r in rules if r.parse_status == "ok"]
    out = {
        "n_rules": float(len(rules)),
        "n_inclusion": float(sum(r.kind == "inclusion" for r in rules)),
        "n_exclusion": float(sum(r.kind == "exclusion" for r in rules)),
        "n_unparseable": float(len(rules) - len(ok)),
        "frac_unparseable": (len(rules) - len(ok)) / len(rules) if rules else np.nan,
        "n_fields": float(len({r.field for r in ok})),
        "n_comorbidity_excl": float(len({r.field.value for r in ok} & set(COMORBIDITY))),
    }
    bio = 0
    for f in ("egfr_status", "alk_status", "ros1_status"):
        a = _allowed(f, rules)
        bio += a is not None and a[1] == ["positive"]
    out["biomarker_required"] = float(bio)
    plat, io = _allowed("prior_platinum", rules), _allowed("prior_immunotherapy", rules)
    out["prior_platinum_required"] = float(plat is not None and plat[1] == [True])
    out["prior_io_excluded"] = float(io is not None and io[1] == [False])
    for f, b in BOUNDS:
        if f != "age":  # age limits come from the registered design fields
            out[f"{f}_{b}"] = _bound(f, b, rules)
    return out


def _first_record(nct_id: str, fv: Mapping[str, Any]):
    """TrialRecord rebuilt from the first-version JSON alone (T11's own mapping), or None."""
    from clinova.ingest.pull import map_trial

    if not fv:
        return None
    try:
        return map_trial(dict(fv), dict(fv), history_source="versions")
    except (KeyError, ValueError, TypeError):
        return None


def design_features(nct_id: str, fv: Mapping[str, Any]) -> tuple[dict[str, float], Any]:
    """Design, operations and visit-burden features from the first-version protocolSection."""
    from clinova.parse.visits import estimate_visits

    keys = ["text_len", "phase_num", "n_arms", "randomized", "masking_level", "iv_drug", "iv_biological", "iv_radiation",
            "iv_other", "log_planned_enrollment", "n_sites", "n_countries", "us_share", "sponsor_industry", "sponsor_gov",
            "age_min", "age_max"]
    out = dict.fromkeys(keys, np.nan)
    t = _first_record(nct_id, fv)
    if t is None:
        return out, None
    types = set(t.intervention_types)
    locs = (fv.get("contactsLocationsModule") or {}).get("locations")
    out.update(
        text_len=float(len(t.eligibility_text_first)) if t.eligibility_text_first else np.nan,
        phase_num=PHASE_NUM[t.phase.value],
        n_arms=float(t.n_arms) if t.n_arms is not None else np.nan,
        randomized={"RANDOMIZED": 1.0, "NON_RANDOMIZED": 0.0, "NA": 0.0}.get(t.allocation or "", np.nan),
        masking_level=float(MASKING[t.masking]) if t.masking in MASKING else np.nan,
        iv_drug=float("DRUG" in types), iv_biological=float("BIOLOGICAL" in types),
        iv_radiation=float("RADIATION" in types),
        iv_other=float(bool(types - {"DRUG", "BIOLOGICAL", "RADIATION"})),
        log_planned_enrollment=float(np.log1p(t.planned_enrollment)) if t.planned_enrollment is not None else np.nan,
        n_sites=float(t.n_sites) if t.n_sites is not None else np.nan,
        n_countries=float(t.n_countries) if t.n_countries is not None else np.nan,
        us_share=(sum(x.get("country") == "United States" for x in locs) / len(locs)) if locs else np.nan,
        sponsor_industry=float(t.sponsor_class == "INDUSTRY") if t.sponsor_class else np.nan,
        sponsor_gov=float(t.sponsor_class in ("NIH", "FED", "OTHER_GOV")) if t.sponsor_class else np.nan,
        age_min=t.min_age_years if t.min_age_years is not None else np.nan,
        age_max=t.max_age_years if t.max_age_years is not None else np.nan,
    )
    if not types:  # no interventions registered: unknown, not "none"
        for k in ("iv_drug", "iv_biological", "iv_radiation", "iv_other"):
            out[k] = np.nan
    return out, estimate_visits(t)


def visit_features(visits) -> dict[str, float]:
    if not visits:
        return dict.fromkeys(["visit_count", "visit_hours", "visit_invasive", "visit_travel"], np.nan)
    return {
        "visit_count": float(len(visits)),
        "visit_hours": float(sum(v.est_hours for v in visits)),
        "visit_invasive": float(sum(v.invasive for v in visits)),
        "visit_travel": float(sum(v.requires_travel for v in visits)),
    }


@lru_cache(maxsize=1)
def screen_cohort() -> pd.DataFrame:
    """The fixed virtual cohort every protocol is screened against (cached on disk; ~14 s to build)."""
    from clinova.cohort.generate import generate_cohort_df
    from clinova.common.config import get_settings

    path = get_settings().DATA_DIR / "model" / f"cohort_any_{SCREEN_N}_{SCREEN_SEED}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    df = generate_cohort_df(SCREEN_N, SCREEN_SEED)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def screenable(rules: Sequence[CriteriaRule]) -> list[CriteriaRule]:
    """Rules the screen accepts (malformed parses are dropped instead of failing the trial)."""
    from clinova.simulate.screen import _validate_rule

    out = []
    for r in rules:
        try:
            _validate_rule(r)
            out.append(r)
        except ValueError:
            pass
    return out


def screen_features(rules: Sequence[CriteriaRule] | None, cohort: pd.DataFrame | None = None) -> dict[str, float]:
    from clinova.simulate.screen import screen

    if rules is None:
        return {"screen_eligible_frac": np.nan, "screen_max_excl_frac": np.nan}
    _, funnel = screen(screenable(rules), screen_cohort() if cohort is None else cohort)
    return {
        "screen_eligible_frac": funnel.eligible_frac,
        "screen_max_excl_frac": funnel.marginal[0].excluded_frac if funnel.marginal else 0.0,
    }


def sim_row(s: SimRunSummary | Mapping[str, Any]) -> dict[str, float]:
    """Simulation features of one run (a SimRunSummary or a ``sim_runs`` row)."""
    g = s.get if isinstance(s, Mapping) else lambda k: getattr(s, k)
    curve = g("dropout_curve") or []
    if isinstance(curve, str):
        curve = json.loads(curve)
    return {"sim_eligible_frac": float(g("eligible_frac")), "sim_enroll_rate": float(g("enroll_rate")),
            "sim_completion_rate": float(g("completion_rate")), "sim_dropout_final": float(curve[-1]) if curve else 0.0}


def build_features(
    trials: pd.DataFrame,
    protocols: Mapping[str, ParsedProtocol] | None = None,
    sim_features: pd.DataFrame | None = None,
    *,
    embeddings: Mapping[str, np.ndarray] | None = None,
    ref: Mapping[str, Any] | None = None,
    cohort: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per trial, indexed by trial id.

    ``trials`` has ``trials`` table columns but only ``nct_id`` and ``first_version`` are read;
    ``protocols`` maps trial id -> first-version ParsedProtocol; ``sim_features`` is indexed by
    trial id with ``SIM_COLS`` (``load_sim_features``; NaN when absent). With ``ref`` (from
    ``fit_ref``) raw thresholds become ``strict_*`` and ``emb_pc*`` are added from ``embeddings``.
    Excludes start year and anything observed after the first version.
    """
    protocols = protocols or {}
    rows = {}
    for nct_id, fv in zip(trials["nct_id"], trials["first_version"], strict=True):
        fv = json.loads(fv) if isinstance(fv, str) else (fv or {})
        p = protocols.get(nct_id)
        design, est_visits = design_features(nct_id, fv)
        row = rule_features(p.rules if p else None) | design
        row |= visit_features(p.visits if p and p.visits else est_visits)
        row |= screen_features(p.rules if p else None, cohort)
        rows[nct_id] = row
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "nct_id"
    sim = sim_features if sim_features is not None else pd.DataFrame(columns=SIM_COLS)
    df = df.join(sim.reindex(columns=SIM_COLS), how="left")
    return apply_ref(df, embeddings, ref) if ref is not None else df


# ---------------------------------------------------------------------------------------------
# Training-split reference: medians, PCA, ladders, kNN distances
# ---------------------------------------------------------------------------------------------


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def knn_distance(vec: np.ndarray, ref: Mapping[str, Any], exclude: str | None = None) -> float:
    """Mean cosine distance from ``vec`` to its k nearest training trials (k = 10 or fewer)."""
    mat, ids = ref["train_emb"], ref["train_emb_ids"]
    d = 1.0 - mat @ _unit(vec)
    if exclude is not None:
        d = d[np.asarray(ids) != exclude]
    k = min(10, len(d))
    return float(np.sort(d)[:k].mean()) if k else float("nan")


def fit_ref(raw_train: pd.DataFrame, embeddings: Mapping[str, np.ndarray] | None = None,
            protocols: Mapping[str, ParsedProtocol] | None = None) -> dict[str, Any]:
    """Medians, embedding PCA, kNN distances and relax ladders from the training rows only: protocols
    whose id is not in ``raw_train`` are ignored, so held-out protocols never shape the edit policy."""
    from sklearn.decomposition import PCA

    medians = {c: float(raw_train[c].median()) for c in THRESHOLDS if c in raw_train and raw_train[c].notna().any()}
    ref: dict[str, Any] = {"medians": medians, "pca": None, "train_emb": None, "train_emb_ids": [],
                           "knn_p50": None, "knn_p90": None, "ladders": {}, "ladder_ids": []}
    ids = [i for i in raw_train.index if embeddings and i in embeddings]
    if len(ids) >= 3:
        mat = _unit(np.stack([embeddings[i] for i in ids]))
        ref["pca"] = PCA(n_components=min(N_PCS, len(ids) - 1), random_state=0).fit(mat)
        ref["train_emb"], ref["train_emb_ids"] = mat, ids
        dists = [knn_distance(mat[j], ref, exclude=i) for j, i in enumerate(ids)]
        ref["knn_p50"], ref["knn_p90"] = float(np.percentile(dists, 50)), float(np.percentile(dists, 90))
    # Relax ladders: numeric thresholds other training NSCLC trials actually used in their first
    # version, plus integer steps for ECOG and prior lines.
    vals: dict[str, set] = {}
    ref["ladder_ids"] = sorted(i for i in (protocols or {}) if i in raw_train.index)
    for p in (protocols[i] for i in ref["ladder_ids"]):
        for r in p.rules:
            if r.parse_status == "ok" and FIELD_SPECS[r.field].kind == "numeric":
                for v in r.value if isinstance(r.value, list) else [r.value]:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        vals.setdefault(r.field.value, set()).add(float(v))
    for f, spec in FIELD_SPECS.items():
        if spec.kind == "numeric" and spec.max is not None and spec.max - spec.min <= 10:
            vals.setdefault(f.value, set()).update(float(x) for x in range(int(spec.min), int(spec.max) + 1))
    ref["ladders"] = {f: sorted(v) for f, v in vals.items()}
    return ref


def apply_ref(raw: pd.DataFrame, embeddings: Mapping[str, np.ndarray] | None, ref: Mapping[str, Any]) -> pd.DataFrame:
    """Raw thresholds -> ``strict_*`` (sign so positive = stricter than the training median); add ``emb_pc*``."""
    df = raw.drop(columns=[c for c in THRESHOLDS if c in raw])
    for c in THRESHOLDS:
        med = ref["medians"].get(c)
        df[f"strict_{c}"] = STRICT_SIGN[c] * (raw[c] - med) if med is not None and c in raw else np.nan
    pcs = np.full((len(raw), N_PCS), np.nan)
    if ref.get("pca") is not None and embeddings:
        for j, nid in enumerate(raw.index):
            if nid in embeddings:
                z = ref["pca"].transform(_unit(embeddings[nid])[None, :])[0]
                pcs[j, : len(z)] = z
    for i in range(N_PCS):
        df[f"emb_pc{i + 1}"] = pcs[:, i]
    return df


# ---------------------------------------------------------------------------------------------
# Database loaders (read-only)
# ---------------------------------------------------------------------------------------------


def load_trials(nct_ids: Sequence[str] | None = None, splits: Sequence[str] | None = ("train", "test")) -> pd.DataFrame:
    """``trials`` rows: id, first_version, split, start_date and labels (labels are never features)."""
    from clinova.common.db import get_conn

    sql = ("SELECT nct_id, first_version, split, start_date, label_accrual_fail, label_shortfall, label_amend "
           "FROM trials WHERE (%s::text[] IS NULL OR nct_id = ANY(%s)) AND (%s::text[] IS NULL OR split = ANY(%s)) "
           "ORDER BY nct_id")
    ids, sp = (list(nct_ids) if nct_ids is not None else None), (list(splits) if splits is not None else None)
    with get_conn() as c:
        cur = c.execute(sql, (ids, ids, sp, sp))
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def load_protocols(nct_ids: Sequence[str] | None = None, version_no: int = 0) -> dict[str, ParsedProtocol]:
    """Parsed rules from ``criteria_rules`` (no Gemini). Visits are left empty (estimated later)."""
    from clinova.common.db import get_conn

    ids = list(nct_ids) if nct_ids is not None else None
    with get_conn() as c:
        rows = c.execute(
            "SELECT nct_id, rule_id, kind, field, operator, value, unit, raw_text, parse_status, locked, note "
            "FROM criteria_rules WHERE version_no = %s AND (%s::text[] IS NULL OR nct_id = ANY(%s))",
            (version_no, ids, ids)).fetchall()
    by: dict[str, list[CriteriaRule]] = {}
    for nid, rid, kind, field, op, value, unit, raw, status, locked, note in rows:
        try:
            r = CriteriaRule(rule_id=rid, kind=kind, field=field, operator=op, value=value, unit=unit, raw_text=raw or "",
                             parse_status=status, locked=bool(locked), note=note)
        except ValueError:
            continue
        by.setdefault(nid, []).append(r)
    key = lambda r: (r.kind != "inclusion", int("".join(ch for ch in r.rule_id if ch.isdigit()) or 0), r.rule_id)  # noqa: E731
    return {nid: ParsedProtocol(trial_id=nid, version_no=version_no, rules=sorted(rs, key=key)) for nid, rs in by.items()}


def load_embeddings(nct_ids: Sequence[str] | None = None) -> dict[str, np.ndarray]:
    from clinova.common.db import get_conn

    ids = list(nct_ids) if nct_ids is not None else None
    with get_conn() as c:
        rows = c.execute("SELECT nct_id, embedding FROM trial_embeddings WHERE (%s::text[] IS NULL OR nct_id = ANY(%s))",
                         (ids, ids)).fetchall()
    return {nid: np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, dtype=np.float32) for nid, v in rows}


def load_sim_features(nct_ids: Sequence[str] | None = None) -> pd.DataFrame:
    """Mean simulation features per trial over its first-version ``sim_runs`` seeds.

    Read straight from ``sim_runs`` with ``sim_row`` so training and ``score(sim=...)`` compute the
    exact same features (T24's ``sim_features`` view is one row per simulated trial of the same runs).
    """
    from clinova.common.db import get_conn

    ids = list(nct_ids) if nct_ids is not None else None
    with get_conn() as c:
        rows = c.execute(
            "SELECT trial_id, eligible_frac, enroll_rate, completion_rate, dropout_curve FROM sim_runs "
            "WHERE version_no = 0 AND trial_id LIKE 'NCT%%' AND (%s::text[] IS NULL OR trial_id = ANY(%s))",
            (ids, ids)).fetchall()
    if not rows:
        return pd.DataFrame(columns=SIM_COLS)
    recs = [{"nct_id": r[0]} | sim_row(dict(eligible_frac=r[1], enroll_rate=r[2], completion_rate=r[3], dropout_curve=r[4]))
            for r in rows]
    return pd.DataFrame(recs).groupby("nct_id")[SIM_COLS].mean()


def load_dataset(splits: Sequence[str] = ("train", "test"), *, ref: Mapping[str, Any] | None = None,
                 ref_split: str = "train") -> tuple[pd.DataFrame, dict]:
    """Features for every trial in ``splits`` plus ``split``, ``start_date`` and label columns.
    ``ref`` (medians, PCA, ladders) is fitted on ``ref_split`` only unless given (e.g. a bundle's).
    No Gemini calls."""
    trials = load_trials(splits=splits)
    ids = trials["nct_id"].tolist()
    protocols, emb, sim = load_protocols(ids), load_embeddings(ids), load_sim_features(ids)
    raw = build_features(trials, protocols, sim)
    if ref is None:
        train_ids = trials.loc[trials["split"] == ref_split, "nct_id"]
        ref = fit_ref(raw.loc[raw.index.intersection(train_ids)], emb, protocols)
    df = apply_ref(raw, emb, ref).join(trials.set_index("nct_id")[["split", "start_date", *LABELS.values()]])
    return df, ref
