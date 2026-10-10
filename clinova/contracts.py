"""Clinova frozen contracts (contracts-v1).

Every task imports its shared data shapes from here. These models ARE the spec: read the
Field descriptions before writing code against them. Only the integrator (T00, G1-G4) edits
this file; propose changes in ``docs/contract_changes/<ID>.md``.

Conventions
-----------
* Enums are ``StrEnum``: they serialize to plain lower-case strings (``"ecog"``, ``"le"``).
* Dates are ISO dates (``date``); timestamps are timezone-aware ``datetime`` in UTC.
* Probabilities and fractions are floats in [0, 1].
* Models forbid unknown keys, so a typo in a fixture or an API payload fails loudly.
* Leakage rule: model features come only from a trial's FIRST registered version.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACTS_VERSION = "contracts-v1"


class Contract(BaseModel):
    """Base for every contract model: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Shared vocabularies
# ---------------------------------------------------------------------------

RuleValue = bool | int | float | str | list[bool | int | float | str] | None
"""A rule threshold or category. Numbers are in the field's canonical unit (see FIELD_SPECS);
categories are vocabulary strings; ``in``/``not_in`` take a list; ``present``/``absent`` take None."""


class Phase(StrEnum):
    """Trial phase. CT.gov ``["PHASE2"]`` -> phase2, ``["PHASE3"]`` -> phase3,
    ``["PHASE2", "PHASE3"]`` -> phase2_3. Amendment cost: phase2 uses the Phase II figure,
    phase3 and phase2_3 the Phase III figure (``config.amendment_cost``)."""

    phase2 = "phase2"
    phase3 = "phase3"
    phase2_3 = "phase2_3"


class OverallStatus(StrEnum):
    """ClinicalTrials.gov API v2 ``overallStatus`` values, verbatim."""

    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    UNKNOWN = "UNKNOWN"
    WITHHELD = "WITHHELD"
    NO_LONGER_AVAILABLE = "NO_LONGER_AVAILABLE"
    AVAILABLE = "AVAILABLE"
    APPROVED_FOR_MARKETING = "APPROVED_FOR_MARKETING"
    TEMPORARILY_NOT_AVAILABLE = "TEMPORARILY_NOT_AVAILABLE"


ENDED_STATUSES = frozenset({OverallStatus.COMPLETED, OverallStatus.TERMINATED, OverallStatus.WITHDRAWN})
"""Statuses that count as 'ended' for labels and the train/test split; anything else is pending."""


class SponsorClass(StrEnum):
    """CT.gov ``leadSponsor.class`` values, verbatim."""

    NIH = "NIH"
    FED = "FED"
    OTHER_GOV = "OTHER_GOV"
    INDIV = "INDIV"
    INDUSTRY = "INDUSTRY"
    NETWORK = "NETWORK"
    AMBIG = "AMBIG"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class StopCategory(StrEnum):
    """Why-stopped class assigned by Gemini (T11). ``accrual`` covers slow/poor/low enrollment."""

    accrual = "accrual"
    safety = "safety"
    efficacy_futility = "efficacy_futility"
    business_funding = "business_funding"
    other = "other"
    unknown = "unknown"


class Split(StrEnum):
    """train: start_date < 2018-01-01; test: on or after; pending: not ended (no labels)."""

    train = "train"
    test = "test"
    pending = "pending"


class Target(StrEnum):
    """Prediction targets, one per label."""

    accrual_fail = "accrual_fail"
    shortfall = "shortfall"
    amend = "amend"


class Setting(StrEnum):
    """Line-of-therapy setting the protocol enrolls."""

    first_line = "first_line"
    second_line_plus = "second_line_plus"
    any = "any"


class ClinicalField(StrEnum):
    """Controlled vocabulary of what an eligibility rule can test. Each value is also a column
    name in the cohort dataframe (see ``COHORT_COLUMNS``) and a field of ``ClinicalTraits``
    (except ``age`` and ``sex``, which live on ``Persona``, and ``other``). Units and value types
    are in ``FIELD_SPECS``."""

    age = "age"
    sex = "sex"
    ecog = "ecog"
    stage = "stage"
    histology = "histology"
    egfr_status = "egfr_status"
    alk_status = "alk_status"
    ros1_status = "ros1_status"
    pdl1_tps = "pdl1_tps"
    brain_mets = "brain_mets"
    leptomeningeal = "leptomeningeal"
    prior_lines = "prior_lines"
    prior_platinum = "prior_platinum"
    prior_immunotherapy = "prior_immunotherapy"
    crcl_ml_min = "crcl_ml_min"
    anc = "anc"
    platelets = "platelets"
    hemoglobin = "hemoglobin"
    ast_x_uln = "ast_x_uln"
    alt_x_uln = "alt_x_uln"
    bilirubin_x_uln = "bilirubin_x_uln"
    life_expectancy_months = "life_expectancy_months"
    measurable_disease = "measurable_disease"
    autoimmune_disease = "autoimmune_disease"
    interstitial_lung_disease = "interstitial_lung_disease"
    active_infection = "active_infection"
    cardiac_condition = "cardiac_condition"
    pregnancy = "pregnancy"
    consent_capacity = "consent_capacity"
    other = "other"


class Operator(StrEnum):
    """Comparison applied as ``patient[field] <operator> value``.

    lt/le/gt/ge: numeric fields, and ordinal fields using the order in FIELD_SPECS (e.g. stage).
    eq/ne: any field. in/not_in: value is a list. present/absent: boolean fields, value None
    (``present`` = the condition is true for the patient). ``eq true``/``eq false`` on a boolean
    field is accepted and means the same as present/absent, but parsers should emit present/absent.
    """

    lt = "lt"
    le = "le"
    gt = "gt"
    ge = "ge"
    eq = "eq"
    ne = "ne"
    in_ = "in"
    not_in = "not_in"
    present = "present"
    absent = "absent"


class BrainMets(StrEnum):
    """Brain metastasis status, ordered none < treated_stable < untreated."""

    none = "none"
    treated_stable = "treated_stable"
    untreated = "untreated"


STAGES = ("I", "II", "IIIA", "IIIB", "IIIC", "IVA", "IVB")
"""Ordered AJCC 8th-edition stage vocabulary. Parsers expand 'III' to IIIA-IIIC and 'IV' to
IVA-IVB using ``in``; 'stage IIIB or higher' is ``stage ge "IIIB"``."""

HISTOLOGIES = ("adenocarcinoma", "squamous", "large_cell", "nsclc_nos", "other")
"""Histology vocabulary. 'Non-squamous' = ``histology not_in ["squamous"]``."""

BIOMARKER_STATUSES = ("positive", "negative", "unknown")
"""Values for egfr_status (sensitizing EGFR mutation), alk_status, ros1_status (rearrangement)."""

RACE_ETHNICITIES = (
    "white",
    "black",
    "hispanic",
    "asian",
    "native_american",
    "pacific_islander",
    "multiracial",
    "other",
)
"""Persona race/ethnicity vocabulary (hispanic of any race is coded 'hispanic')."""


class FieldSpec(Contract):
    """Type, canonical unit and plausible range of one ClinicalField."""

    kind: Literal["numeric", "ordinal", "categorical", "boolean", "free"] = Field(
        description="numeric: compare floats; ordinal: compare by position in `values`; "
        "categorical: eq/ne/in/not_in on `values`; boolean: present/absent; free: unparseable bucket."
    )
    unit: str | None = Field(None, description="Canonical unit every rule value must be converted to.")
    values: tuple[str, ...] | None = Field(None, description="Allowed values (ordered for ordinal fields).")
    min: float | None = Field(None, description="Lowest plausible value (sanity check).")
    max: float | None = Field(None, description="Highest plausible value (sanity check).")


FIELD_SPECS: dict[ClinicalField, FieldSpec] = {
    ClinicalField.age: FieldSpec(kind="numeric", unit="years", min=0, max=120),
    ClinicalField.sex: FieldSpec(kind="categorical", values=("female", "male")),
    ClinicalField.ecog: FieldSpec(kind="numeric", unit="score", min=0, max=4),
    ClinicalField.stage: FieldSpec(kind="ordinal", values=STAGES),
    ClinicalField.histology: FieldSpec(kind="categorical", values=HISTOLOGIES),
    ClinicalField.egfr_status: FieldSpec(kind="categorical", values=BIOMARKER_STATUSES),
    ClinicalField.alk_status: FieldSpec(kind="categorical", values=BIOMARKER_STATUSES),
    ClinicalField.ros1_status: FieldSpec(kind="categorical", values=BIOMARKER_STATUSES),
    ClinicalField.pdl1_tps: FieldSpec(kind="numeric", unit="%", min=0, max=100),
    ClinicalField.brain_mets: FieldSpec(kind="ordinal", values=tuple(b.value for b in BrainMets)),
    ClinicalField.leptomeningeal: FieldSpec(kind="boolean"),
    ClinicalField.prior_lines: FieldSpec(kind="numeric", unit="lines", min=0, max=10),
    ClinicalField.prior_platinum: FieldSpec(kind="boolean"),
    ClinicalField.prior_immunotherapy: FieldSpec(kind="boolean"),
    ClinicalField.crcl_ml_min: FieldSpec(kind="numeric", unit="mL/min", min=5, max=250),
    ClinicalField.anc: FieldSpec(kind="numeric", unit="x10^9/L", min=0, max=50),
    ClinicalField.platelets: FieldSpec(kind="numeric", unit="x10^9/L", min=0, max=1500),
    ClinicalField.hemoglobin: FieldSpec(kind="numeric", unit="g/dL", min=3, max=22),
    ClinicalField.ast_x_uln: FieldSpec(kind="numeric", unit="xULN", min=0, max=50),
    ClinicalField.alt_x_uln: FieldSpec(kind="numeric", unit="xULN", min=0, max=50),
    ClinicalField.bilirubin_x_uln: FieldSpec(kind="numeric", unit="xULN", min=0, max=50),
    ClinicalField.life_expectancy_months: FieldSpec(kind="numeric", unit="months", min=0, max=240),
    ClinicalField.measurable_disease: FieldSpec(kind="boolean"),
    ClinicalField.autoimmune_disease: FieldSpec(kind="boolean"),
    ClinicalField.interstitial_lung_disease: FieldSpec(kind="boolean"),
    ClinicalField.active_infection: FieldSpec(kind="boolean"),
    ClinicalField.cardiac_condition: FieldSpec(kind="boolean"),
    ClinicalField.pregnancy: FieldSpec(kind="boolean"),
    ClinicalField.consent_capacity: FieldSpec(kind="boolean"),
    ClinicalField.other: FieldSpec(kind="free"),
}
"""One entry per ClinicalField. Unit conversions the parser must apply: CrCl in mL/min (eGFR
quoted in mL/min/1.73m2 is accepted as CrCl); ANC and platelets in 10^9/L (= 10^3/uL; 1500/mm3
-> 1.5); hemoglobin in g/dL (90 g/L -> 9.0); AST/ALT/bilirubin as multiples of ULN; PD-L1 TPS in
percent; ECOG as the score (Karnofsky 70 -> ECOG 1, KPS 60 -> 2)."""

LOCKED_BY_DEFAULT = frozenset({ClinicalField.pregnancy, ClinicalField.consent_capacity})
"""Fields whose rules the parser marks locked=True and the optimizer never edits."""

PROCEDURE_VOCAB = (
    "physical_exam",
    "vitals",
    "blood_draw",
    "urine_test",
    "ecg",
    "echocardiogram",
    "ct_scan",
    "mri_brain",
    "pet_ct",
    "bone_scan",
    "biopsy",
    "bronchoscopy",
    "infusion",
    "injection",
    "oral_drug_dispense",
    "pk_sampling",
    "pulmonary_function_test",
    "questionnaire",
    "adverse_event_review",
    "phone_call",
    "other",
)
"""Recommended Visit.procedures tags. Invasive: biopsy, bronchoscopy (venipuncture is not)."""


class BurdenTag(StrEnum):
    """Why a persona declines or drops out."""

    travel = "travel"
    time_off_work = "time_off_work"
    caregiving = "caregiving"
    procedure_fear = "procedure_fear"
    side_effects = "side_effects"
    cost = "cost"
    distrust = "distrust"
    too_unwell = "too_unwell"
    other = "other"


class Source(StrEnum):
    """Where an API payload came from."""

    fixture = "fixture"
    live = "live"
    cache = "cache"


class Interval(Contract):
    """A closed range, e.g. a bootstrap 95% interval."""

    low: float = Field(description="Lower bound.")
    high: float = Field(description="Upper bound (>= low).")

    @model_validator(mode="after")
    def _ordered(self) -> Interval:
        if self.low > self.high:
            raise ValueError(f"Interval low {self.low} > high {self.high}")
        return self


# ---------------------------------------------------------------------------
# Trials and version history (T11)
# ---------------------------------------------------------------------------


class TrialRecord(Contract):
    """One ClinicalTrials.gov trial = one row of table ``trials``.

    LEAKAGE RULE: every design/operations field below (phase, enrollment target, sites, arms,
    eligibility_text_first, ages...) is taken from the FIRST registered version. Fields marked
    LABEL-SIDE (status, why_stopped, stop_category, actual_enrollment, completion dates,
    eligibility_text_current, current, labels) describe what happened later and must never be
    used as model features.
    """

    nct_id: str = Field(pattern=r"^NCT\d{8}$", description="ClinicalTrials.gov id, e.g. NCT01234567.")
    title: str = Field(description="Brief title from the first version.")
    phase: Phase = Field(description="Phase from the first version.")
    conditions: list[str] = Field(default_factory=list, description="Condition terms (first version).")
    start_date: date | None = Field(None, description="Study start date (current record, actual if available); drives the split.")
    first_posted: date | None = Field(None, description="Date first posted on CT.gov.")
    first_version_date: date | None = Field(None, description="Date of version 0 in the history (== first_posted when history works).")
    primary_completion_date: date | None = Field(None, description="LABEL-SIDE: current primary completion date.")
    completion_date: date | None = Field(None, description="LABEL-SIDE: current study completion/termination date; outcome observed_at.")
    status: OverallStatus = Field(description="LABEL-SIDE: current overall status.")
    why_stopped: str | None = Field(None, description="LABEL-SIDE: current why-stopped free text.")
    stop_category: StopCategory | None = Field(None, description="LABEL-SIDE: Gemini class of why_stopped; None unless status is TERMINATED/SUSPENDED/WITHDRAWN.")
    planned_enrollment: int | None = Field(None, ge=0, description="Anticipated enrollment in the FIRST version (shortfall denominator).")
    actual_enrollment: int | None = Field(None, ge=0, description="LABEL-SIDE: current enrollment when its type is ACTUAL, else None.")
    sponsor_class: SponsorClass | None = Field(None, description="Lead sponsor class (first version).")
    lead_sponsor: str | None = Field(None, description="Lead sponsor name (first version).")
    n_sites: int | None = Field(None, ge=0, description="Number of locations in the first version (often 0 or few; never the current count).")
    n_countries: int | None = Field(None, ge=0, description="Distinct location countries in the first version.")
    n_arms: int | None = Field(None, ge=0, description="Number of arms (first version).")
    allocation: str | None = Field(None, description="CT.gov designInfo.allocation, e.g. RANDOMIZED, NON_RANDOMIZED, NA (first version).")
    masking: str | None = Field(None, description="CT.gov maskingInfo.masking, e.g. NONE, DOUBLE (first version).")
    intervention_types: list[str] = Field(default_factory=list, description="CT.gov intervention types, e.g. DRUG, BIOLOGICAL, RADIATION (first version).")
    min_age_years: float | None = Field(None, description="Minimum age in years (first version).")
    max_age_years: float | None = Field(None, description="Maximum age in years (first version).")
    eligibility_text_first: str = Field(description="Eligibility criteria text of the FIRST registered version (parser input for features).")
    eligibility_text_current: str | None = Field(None, description="LABEL-SIDE: current eligibility text (for the amend label and hit-rate diff).")
    first_version: dict = Field(default_factory=dict, description="Raw CT.gov protocolSection as of version 0 (jsonb).")
    current: dict = Field(default_factory=dict, description="LABEL-SIDE: raw current CT.gov protocolSection (jsonb).")
    label_accrual_fail: bool | None = Field(None, description="TERMINATED and stop_category == accrual. None if pending.")
    label_shortfall: bool | None = Field(None, description="actual_enrollment < 0.8 * planned_enrollment for ended trials with an ACTUAL count. None if unknown.")
    label_amend: bool | None = Field(None, description="Eligibility changed after start_date and the change was classified widened or mixed. None if pending.")
    split: Split = Field(description="train / test by start_date vs 2018-01-01; pending if not ended.")
    history_source: Literal["versions", "earliest_vs_current"] = Field(
        "versions", description="'versions' when the CT.gov version history was read; 'earliest_vs_current' when the documented fallback was used."
    )


class VersionField(StrEnum):
    """What changed between two consecutive registered versions."""

    eligibility = "eligibility"
    enrollment_count = "enrollment_count"
    enrollment_type = "enrollment_type"
    primary_completion_date = "primary_completion_date"
    overall_status = "overall_status"
    why_stopped = "why_stopped"
    location_count = "location_count"
    other = "other"


class VersionEvent(Contract):
    """One field change between version_no-1 and version_no = one row of hypertable ``trial_versions``."""

    nct_id: str = Field(pattern=r"^NCT\d{8}$", description="Trial id.")
    version_no: int = Field(ge=0, description="Version where the new value first appears (0 = first registered version).")
    version_date: date = Field(description="Date of that version (hypertable time column).")
    field: VersionField = Field(description="Which field changed.")
    old_value: str | None = Field(None, description="Value before (stringified; JSON for structured values).")
    new_value: str | None = Field(None, description="Value after (stringified; JSON for structured values).")
    change_class: Literal["widened", "narrowed", "mixed", "administrative"] | None = Field(
        None, description="Only for field == eligibility: Gemini's class of the change (widened/mixed count toward label_amend)."
    )


# ---------------------------------------------------------------------------
# Parsed protocol (T12)
# ---------------------------------------------------------------------------


class CriteriaRule(Contract):
    """One atomic, code-checkable eligibility condition = one row of ``criteria_rules``.

    Semantics: for an INCLUSION rule a patient must satisfy ``patient[field] <operator> value``;
    for an EXCLUSION rule a patient who satisfies it is excluded. Example: inclusion
    ``ecog le 1``; exclusion ``brain_mets eq "untreated"``; exclusion ``autoimmune_disease present``.
    A patient with a missing value for the field passes the rule (the screen never guesses).
    """

    rule_id: str = Field(description="Stable id within the protocol: 'I<n>' for inclusion, 'E<n>' for exclusion, numbered from 1 in order of appearance after splitting into atomic conditions.")
    kind: Literal["inclusion", "exclusion"] = Field(description="Inclusion (must satisfy) or exclusion (excluded if satisfied).")
    field: ClinicalField = Field(description="What the rule tests; 'other' when it maps to nothing in the vocabulary.")
    operator: Operator = Field(description="Comparison operator; see Operator.")
    value: RuleValue = Field(None, description="Threshold or category in the field's canonical unit (FIELD_SPECS); list for in/not_in; None for present/absent.")
    unit: str | None = Field(None, description="Canonical unit of `value` (FIELD_SPECS[field].unit), None for categorical/boolean.")
    raw_text: str = Field(description="The exact source text fragment this rule came from.")
    parse_status: Literal["ok", "unparseable"] = Field("ok", description="'unparseable' = flagged, never guessed; the screen skips it. field == other implies unparseable.")
    locked: bool = Field(False, description="Never edited by the optimizer. True by default for pregnancy and consent_capacity; authors may lock more.")
    note: str | None = Field(None, description="Why it is unparseable, or the sanity-check warning that fired.")

    @model_validator(mode="after")
    def _other_is_unparseable(self) -> CriteriaRule:
        if self.field == ClinicalField.other and self.parse_status != "unparseable":
            raise ValueError(f"rule {self.rule_id}: field 'other' must have parse_status 'unparseable'")
        return self


class Visit(Contract):
    """One scheduled study visit, enumerated explicitly (cycles are expanded, capped at the
    first 12 months of treatment)."""

    visit_no: int = Field(ge=1, description="1-based, chronological.")
    name: str = Field(description="Human label, e.g. 'Screening', 'Cycle 1 Day 1', 'Week 9 imaging'.")
    day: int = Field(description="Study day relative to first dose / randomization (day 1); screening is negative.")
    procedures: list[str] = Field(default_factory=list, description="Procedure tags, preferably from PROCEDURE_VOCAB.")
    est_hours: float = Field(ge=0, description="Estimated on-site time in hours, including waiting and infusion.")
    requires_travel: bool = Field(True, description="Patient must attend the site in person.")
    invasive: bool = Field(False, description="Includes an invasive procedure (biopsy, bronchoscopy); venipuncture does not count.")
    remote_ok: bool = Field(False, description="Could plausibly be done remotely (telehealth, local lab, home nursing).")
    estimated: bool = Field(False, description="True when built by parse.visits.estimate_visits from design fields, not read from schedule text; the UI labels it.")


class ParsedProtocol(Contract):
    """A protocol as code: rules plus visits. Input to the screen, the agents, the model and the optimizer."""

    trial_id: str = Field(description="NCT id for registered trials, 'draft-<uuid>' for pasted drafts, 'FIXTURE-...' for fixtures.")
    version_no: int = Field(0, ge=0, description="CT.gov version parsed (0 = first registered). Drafts use 0.")
    title: str | None = Field(None, description="Trial title, for the persona trial offer.")
    summary: str | None = Field(None, description="One-paragraph plain-language purpose (what is being tested), for the persona trial offer.")
    phase: Phase | None = Field(None, description="Phase if known (drafts may omit).")
    setting: Setting = Field(Setting.any, description="Line of therapy inferred from the criteria (first_line / second_line_plus / any).")
    randomized: bool | None = Field(None, description="Randomized assignment, if known.")
    arms: list[str] = Field(default_factory=list, description="Arm labels in plain words, e.g. ['Drug X + docetaxel', 'Docetaxel'].")
    rules: list[CriteriaRule] = Field(default_factory=list, description="Atomic eligibility rules, inclusion then exclusion.")
    visits: list[Visit] = Field(default_factory=list, description="Explicit visit schedule (may be estimated).")
    unparseable: list[str] = Field(default_factory=list, description="rule_ids with parse_status 'unparseable'; recomputed from `rules` on validation.")

    @model_validator(mode="after")
    def _index(self) -> ParsedProtocol:
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate rule_id in {self.trial_id}")
        self.unparseable = [r.rule_id for r in self.rules if r.parse_status == "unparseable"]
        return self


class RuleChange(Contract):
    """One per-field difference between two parsed versions (parse.diff.diff_rules), used for
    the hit-rate metric and the backtest reveal."""

    field: ClinicalField = Field(description="Field that changed.")
    change: Literal["relaxed", "tightened", "removed", "added"] = Field(description="relaxed = more patients pass; removed = rule dropped (also a relaxation for exclusion and inclusion rules).")
    old_rule_id: str | None = Field(None, description="rule_id in the old version, if any.")
    new_rule_id: str | None = Field(None, description="rule_id in the new version, if any.")
    old: str | None = Field(None, description="Old condition in words, e.g. 'crcl_ml_min ge 60'.")
    new: str | None = Field(None, description="New condition in words.")


# ---------------------------------------------------------------------------
# Virtual cohort (T13)
# ---------------------------------------------------------------------------


class ClinicalTraits(Contract):
    """Clinical state of one virtual patient. Field names equal ClinicalField values so the
    cohort dataframe can be screened directly. Sampled from ``cohort/assumptions.yaml``."""

    stage: str = Field(description="One of STAGES.")
    histology: str = Field(description="One of HISTOLOGIES.")
    ecog: int = Field(ge=0, le=4, description="ECOG performance status.")
    egfr_status: str = Field(description="One of BIOMARKER_STATUSES (sensitizing EGFR mutation).")
    alk_status: str = Field(description="One of BIOMARKER_STATUSES.")
    ros1_status: str = Field(description="One of BIOMARKER_STATUSES.")
    pdl1_tps: float = Field(ge=0, le=100, description="PD-L1 tumor proportion score, percent.")
    brain_mets: BrainMets = Field(description="none / treated_stable / untreated.")
    leptomeningeal: bool = Field(False, description="Leptomeningeal disease.")
    prior_lines: int = Field(ge=0, description="Prior systemic lines for advanced disease.")
    prior_platinum: bool = Field(description="Received platinum-based chemotherapy.")
    prior_immunotherapy: bool = Field(description="Received PD-1/PD-L1/CTLA-4 inhibitor.")
    crcl_ml_min: float = Field(gt=0, description="Creatinine clearance (Cockcroft-Gault), mL/min.")
    anc: float = Field(ge=0, description="Absolute neutrophil count, 10^9/L.")
    platelets: float = Field(ge=0, description="Platelets, 10^9/L.")
    hemoglobin: float = Field(gt=0, description="Hemoglobin, g/dL.")
    ast_x_uln: float = Field(ge=0, description="AST as multiple of ULN.")
    alt_x_uln: float = Field(ge=0, description="ALT as multiple of ULN.")
    bilirubin_x_uln: float = Field(ge=0, description="Total bilirubin as multiple of ULN.")
    life_expectancy_months: float = Field(ge=0, description="Clinician-estimated life expectancy, months.")
    measurable_disease: bool = Field(description="RECIST 1.1 measurable disease.")
    autoimmune_disease: bool = Field(False, description="Active autoimmune disease needing systemic treatment in the past 2 years.")
    interstitial_lung_disease: bool = Field(False, description="History of ILD or pneumonitis needing steroids.")
    active_infection: bool = Field(False, description="Active infection (incl. uncontrolled HIV/HBV/HCV).")
    cardiac_condition: bool = Field(False, description="Clinically significant cardiac disease (recent MI, NYHA III-IV, uncontrolled arrhythmia).")
    pregnancy: bool = Field(False, description="Pregnant or breastfeeding.")
    consent_capacity: bool = Field(True, description="Able to give informed consent.")
    smoking_status: Literal["never", "former", "current"] = Field(description="Smoking history (drives EGFR/ALK correlation).")
    weight_kg: float = Field(gt=0, description="Body weight, kg (Cockcroft-Gault input).")
    creatinine_mg_dl: float = Field(gt=0, description="Serum creatinine, mg/dL (Cockcroft-Gault input).")


class Persona(Contract):
    """One virtual patient: life context (from the persona dataset, reweighted to NSCLC
    demographics) plus sampled ClinicalTraits. Synthetic; never real patient data."""

    persona_id: str = Field(description="Stable id, deterministic per (source, seed, index), e.g. 'p-42-00017'.")
    age: int = Field(ge=18, le=100, description="Age in years.")
    sex: Literal["female", "male"] = Field(description="Sex.")
    race_ethnicity: str = Field(description="One of RACE_ETHNICITIES.")
    occupation: str | None = Field(None, description="Occupation or 'retired'.")
    employed: bool | None = Field(None, description="Currently working (time off work is a burden).")
    caregiver: bool = Field(False, description="Primary caregiver for a child, partner or parent.")
    travel_access: Literal["easy", "moderate", "hard"] = Field("moderate", description="How hard it is to reach a trial site (car, transit, distance).")
    distance_km: float | None = Field(None, ge=0, description="One-way distance to the nearest trial site, km.")
    urbanicity: Literal["urban", "suburban", "rural"] | None = Field(None, description="Where they live.")
    income_band: Literal["low", "middle", "high"] = Field("middle", description="Household income band.")
    risk_tolerance: Literal["low", "medium", "high"] = Field("medium", description="Attitude to medical risk and uncertainty.")
    health_literacy: Literal["low", "adequate", "high"] = Field("adequate", description="Health literacy.")
    narrative: str | None = Field(None, description="Short background in the persona's own terms (from the dataset), shown to the agent.")
    source: str = Field("synthetic", description="Persona source, e.g. 'matraix', 'census_synthetic', 'fixture'.")
    clinical: ClinicalTraits = Field(description="Sampled clinical traits.")


PERSONA_COLUMNS = ("persona_id", "age", "sex", "race_ethnicity", "employed", "caregiver", "travel_access",
                   "distance_km", "urbanicity", "income_band", "risk_tolerance", "health_literacy")
COHORT_COLUMNS = PERSONA_COLUMNS + tuple(ClinicalTraits.model_fields)
"""Columns of the cohort dataframe (``cohort.generate.cohort_frame``): one row per persona,
persona fields then every ClinicalTraits field, so ``df[rule.field]`` works for every field but 'other'."""


# ---------------------------------------------------------------------------
# Screening (T13)
# ---------------------------------------------------------------------------


class ScreenResult(Contract):
    """Screening outcome for one persona."""

    persona_id: str = Field(description="Persona screened.")
    eligible: bool = Field(description="Passes every applied rule.")
    failed_rules: list[str] = Field(default_factory=list, description="rule_ids the persona fails (all of them, not just the first).")
    first_failed_rule: str | None = Field(None, description="First failing rule in protocol order (the funnel stage that drops them).")


class FunnelStage(Contract):
    """One step of the sequential funnel: rules are applied in protocol order."""

    rule_id: str | None = Field(None, description="Rule applied at this stage; None for the initial 'screened' stage.")
    label: str = Field(description="Plain words, e.g. 'Screened' or 'ECOG 0-1 (I3)'.")
    remaining: int = Field(ge=0, description="Personas still eligible after this stage.")
    excluded: int = Field(ge=0, description="Personas dropped at this stage (failed this rule and passed all earlier ones).")


class RuleExclusion(Contract):
    """Per-rule marginal exclusion, independent of rule order."""

    rule_id: str = Field(description="Rule.")
    field: ClinicalField = Field(description="Its field.")
    label: str = Field(description="Plain words for the rule.")
    excluded_n: int = Field(ge=0, description="Personas failing this rule regardless of other rules ('marginal exclusion'; the hit-rate baseline ranks by this).")
    excluded_frac: float = Field(ge=0, le=1, description="excluded_n / n_screened.")
    excluded_only_n: int = Field(ge=0, description="Personas failing ONLY this rule: dropping the rule would make them eligible.")


class Funnel(Contract):
    """Screening funnel for one protocol on one cohort."""

    trial_id: str = Field(description="Protocol screened.")
    n_screened: int = Field(ge=0, description="Cohort size.")
    n_eligible: int = Field(ge=0, description="Personas passing every applied rule.")
    eligible_frac: float = Field(ge=0, le=1, description="n_eligible / n_screened.")
    stages: list[FunnelStage] = Field(description="Sequential funnel; first stage is 'Screened' with rule_id None.")
    marginal: list[RuleExclusion] = Field(default_factory=list, description="Per-rule marginal exclusions, sorted by excluded_n descending.")
    skipped_rules: list[str] = Field(default_factory=list, description="Unparseable rule_ids that were not applied.")


# ---------------------------------------------------------------------------
# Persona agents and simulation runs (T14, T24)
# ---------------------------------------------------------------------------


class AgentDecision(Contract):
    """What one eligible persona agent decided after reading the trial offer."""

    persona_id: str = Field(description="Persona that decided.")
    enroll: bool = Field(description="Would enroll.")
    dropout_visit: int | None = Field(None, ge=1, description="First visit_no they would NOT attend (they attended every earlier visit); None = completes (always None when enroll is False).")
    reason: str = Field(description="One or two sentences in the persona's voice.")
    burden_tags: list[BurdenTag] = Field(default_factory=list, description="Burdens behind declining or dropping out (may be empty for completers).")

    @model_validator(mode="after")
    def _no_dropout_without_enroll(self) -> AgentDecision:
        if not self.enroll and self.dropout_visit is not None:
            raise ValueError(f"persona {self.persona_id}: dropout_visit set but enroll is False")
        return self


class ReasonShare(Contract):
    """Share of decliners/dropouts citing a burden tag."""

    tag: BurdenTag = Field(description="Burden tag.")
    share: float = Field(ge=0, le=1, description="Fraction of personas who declined or dropped out that cited it.")


class SimRunSummary(Contract):
    """One simulation run (one protocol, one cohort seed) = one row of hypertable ``sim_runs``."""

    run_id: str = Field(description="Unique id (uuid4 hex) for this (trial, seed) run.")
    trial_id: str = Field(description="Protocol simulated.")
    version_no: int = Field(0, ge=0, description="Protocol version simulated.")
    seed: int = Field(description="Cohort and agent seed.")
    model_version: str = Field(description="LLM model id + agent prompt version, e.g. '<model>/agents-v1'.")
    n_personas: int = Field(ge=0, description="Cohort size screened.")
    n_eligible: int = Field(ge=0, description="Eligible personas.")
    eligible_frac: float = Field(ge=0, le=1, description="n_eligible / n_personas.")
    n_agents: int = Field(ge=0, description="Eligible personas actually run as agents (<= max_agents).")
    enroll_rate: float = Field(ge=0, le=1, description="Enrolled / n_agents.")
    completion_rate: float = Field(ge=0, le=1, description="Enrolled who complete every visit / enrolled.")
    dropout_curve: list[float] = Field(default_factory=list, description="Cumulative fraction of enrolled who have dropped out by each visit: curve[i] = #(dropout_visit <= i+1) / enrolled; non-decreasing; length = number of visits.")
    median_dropout_visit: int | None = Field(None, description="Median visit_no of dropout among dropouts; None if nobody dropped out.")
    top_reasons: list[ReasonShare] = Field(default_factory=list, description="Burden tags by share, descending (top 5).")
    funnel: Funnel | None = Field(None, description="Screening funnel for this seed.")
    run_at: datetime = Field(description="When the run happened (UTC; hypertable time column).")


# ---------------------------------------------------------------------------
# Model outputs (T22)
# ---------------------------------------------------------------------------


class Driver(Contract):
    """One SHAP driver of a risk score, in plain words."""

    feature: str = Field(description="Feature column name, e.g. 'strict_crcl_ml_min'.")
    label: str = Field(description="Plain words, e.g. 'Creatinine clearance cutoff stricter than 80% of trials'.")
    value: float | str | None = Field(None, description="The feature's value for this trial.")
    contribution: float = Field(description="SHAP contribution in log-odds; positive raises risk.")


class RiskScore(Contract):
    """Calibrated probability for one target with its range and confidence."""

    target: Target = Field(description="accrual_fail / shortfall / amend.")
    p: float = Field(ge=0, le=1, description="Calibrated probability (isotonic).")
    ci_low: float = Field(ge=0, le=1, description="Lower end of the range (bootstrap models x simulation seeds).")
    ci_high: float = Field(ge=0, le=1, description="Upper end of the range.")
    confidence: Literal["high", "medium", "low"] = Field(description="'low' when embedding distance to the 10 nearest training trials is above the training 90th percentile.")
    top_drivers: list[Driver] = Field(default_factory=list, description="Top SHAP drivers, largest |contribution| first (3-5).")
    model_version: str = Field("unversioned", description="Model bundle version, e.g. 'v1.0'.")

    @model_validator(mode="after")
    def _range(self) -> RiskScore:
        if self.ci_low > self.ci_high:
            raise ValueError("ci_low > ci_high")
        return self


class Edit(Contract):
    """One protocol edit the optimizer may propose."""

    kind: Literal["relax_rule", "drop_rule", "cut_visit", "merge_visits", "make_remote"] = Field(
        description="relax_rule: change a rule's threshold/operator one step; drop_rule: remove it; cut_visit: remove a visit; "
        "merge_visits: merge visit target_id with the next visit; make_remote: set the visit to remote (no travel)."
    )
    target_id: str = Field(description="rule_id for rule edits; str(visit_no) for visit edits.")
    new_value: RuleValue = Field(None, description="relax_rule only: the new threshold/category in the canonical unit.")
    new_operator: Operator | None = Field(None, description="relax_rule only, optional: new operator (e.g. brain_mets 'ne none' -> 'eq untreated').")


class CriterionFlag(Contract):
    """A rule ranked by how much relaxing or removing it lowers predicted accrual-failure risk."""

    rule_id: str = Field(description="Flagged rule.")
    field: ClinicalField = Field(description="Its field.")
    rank: int = Field(ge=1, description="1 = biggest risk reduction.")
    where: str = Field(description="Where in the protocol, e.g. 'Inclusion I6: creatinine clearance >= 60 mL/min'.")
    why: str = Field(description="Why it matters, with numbers, e.g. 'excludes 18% of otherwise-eligible patients; 4 in 10 comparable trials later widened it'.")
    what_to_do: str = Field(description="Suggested change for the author, e.g. 'Consider CrCl >= 45 mL/min, as used by NCT0...'.")
    delta_p: float = Field(description="p_accrual_fail(original) - p_accrual_fail(edited); positive = risk drops.")
    suggested_edit: Edit | None = Field(None, description="The edit behind delta_p.")


class Precedent(Contract):
    """A comparable trial that already used the looser rule."""

    nct_id: str = Field(pattern=r"^NCT\d{8}$", description="Trial id.")
    title: str = Field(description="Brief title.")
    distance: float | None = Field(None, ge=0, description="Cosine distance of eligibility embeddings (smaller = more similar).")
    detail: str = Field(description="What it did and how it ended, e.g. 'allowed CrCl >= 45 mL/min; COMPLETED with 98% of target'.")


class FixRanges(Contract):
    """Ranges of a fix's effects across cohort seeds and bootstrap models."""

    delta_enroll: Interval = Field(description="Range of delta_enroll.")
    delta_dropout: Interval = Field(description="Range of delta_dropout.")
    delta_diversity: Interval = Field(description="Range of delta_diversity.")
    delta_p_amend: Interval | None = Field(None, description="Range of delta_p_amend.")


class Fix(Contract):
    """One trade-off fix (1-3 edits) = one row of ``fixes``."""

    fix_id: str = Field(description="Unique id.")
    trial_id: str = Field(description="Protocol it applies to.")
    label: Literal["fastest", "lowest_dropout", "balanced", "other"] = Field(description="Which representative pick this is.")
    summary: str = Field(description="One sentence for the author, framed as a suggestion.")
    edits: list[Edit] = Field(min_length=1, max_length=3, description="Edits applied together.")
    delta_enroll: float = Field(description="Relative change in enrollment speed (eligible_frac x enroll_rate); 0.25 = 25% faster.")
    delta_dropout: float = Field(description="Absolute change in predicted dropout risk (fraction of enrolled); negative = fewer dropouts.")
    delta_diversity: float = Field(description="Absolute change in the diversity index of the eligible cohort (0-1); positive = closer to NSCLC population.")
    delta_p_amend: float = Field(0.0, description="Change in predicted amendment probability; negative = fewer amendments.")
    ranges: FixRanges = Field(description="Ranges across 5 cohort seeds and bootstrap models.")
    dollar_value: float = Field(ge=0, description="Expected value in USD of avoided amendments: max(0, -delta_p_amend) x phase median amendment cost (config). Labelled 'expected value'.")
    precedents: list[Precedent] = Field(default_factory=list, description="Comparable trials that used the looser rule and completed.")


# ---------------------------------------------------------------------------
# Learning loop (T21)
# ---------------------------------------------------------------------------


class Prediction(Contract):
    """One logged prediction = one row of hypertable ``predictions``."""

    prediction_id: str = Field(description="Unique id (uuid4 hex).")
    trial_id: str = Field(description="Trial or draft scored.")
    model_version: str = Field(description="Model bundle version.")
    target: Target = Field(description="Target predicted.")
    p: float = Field(ge=0, le=1, description="Calibrated probability.")
    ci_low: float | None = Field(None, ge=0, le=1, description="Range low.")
    ci_high: float | None = Field(None, ge=0, le=1, description="Range high.")
    context: Literal["live", "backtest", "replay", "validation", "fixture"] = Field("live", description="Why it was made; the replay writes 'replay'.")
    predicted_at: datetime = Field(description="When (UTC). The replay sets it to the simulated month.")


class Feedback(Contract):
    """One human decision or rating = one row of hypertable ``feedback`` (audit trail)."""

    feedback_id: str = Field(description="Unique id (uuid4 hex).")
    user_name: str = Field("anonymous", description="Who decided (free text; no auth in v1).")
    target_type: Literal["fix", "flag", "rule", "persona", "prediction", "run"] = Field(description="What the feedback is about.")
    target_id: str = Field(description="fix_id, rule_id, persona_id, prediction_id or run_id.")
    trial_id: str | None = Field(None, description="Protocol context.")
    decision: Literal["accept", "reject", "rate", "comment"] = Field(description="accept/reject a fix or flag; rate a persona's realism; free comment.")
    rating: int | None = Field(None, ge=1, le=5, description="1-5 persona-realism rating when decision == rate.")
    comment: str | None = Field(None, description="Optional comment.")
    created_at: datetime = Field(description="When (UTC).")


class RunResult(Contract):
    """Everything the results screen shows for one run."""

    protocol: ParsedProtocol = Field(description="Protocol that was run.")
    funnel: Funnel | None = Field(None, description="Screening funnel (first seed).")
    sim: list[SimRunSummary] = Field(default_factory=list, description="One summary per seed.")
    risks: list[RiskScore] = Field(default_factory=list, description="One per target.")
    flags: list[CriterionFlag] = Field(default_factory=list, description="Top criterion flags (3).")
    fixes: list[Fix] = Field(default_factory=list, description="Three trade-off fixes.")


RunStage = Literal["queued", "parse", "cohort", "screen", "agents", "score", "flags", "optimize", "done", "failed"]


class RunStatus(Contract):
    """Progress and result of POST /runs, returned by GET /runs/{run_id}."""

    run_id: str = Field(description="Run id.")
    trial_id: str = Field(description="Protocol being run.")
    stage: RunStage = Field(description="Current stage; 'done' or 'failed' when finished.")
    stages_done: list[RunStage] = Field(default_factory=list, description="Stages completed so far, in order.")
    progress: float = Field(0.0, ge=0, le=1, description="Overall progress 0-1.")
    message: str | None = Field(None, description="Human-readable status or error message.")
    started_at: datetime = Field(description="When the run started (UTC).")
    updated_at: datetime = Field(description="Last update (UTC).")
    source: Source = Field(Source.live, description="fixture / live / cache.")
    result: RunResult | None = Field(None, description="Filled when stage == 'done'.")


class BacktestResult(Contract):
    """GET /trials/{nct_id}/backtest: the first-version run plus the history reveal."""

    trial: TrialRecord = Field(description="The trial (first-version fields + outcome).")
    run: RunResult | None = Field(None, description="Clinova run on the first registered version.")
    events: list[VersionEvent] = Field(default_factory=list, description="Version-history events for the reveal timeline.")
    changes: list[RuleChange] = Field(default_factory=list, description="Rule-level eligibility changes first -> last version.")
    source: Source = Field(Source.live, description="fixture / live / cache.")


# ---------------------------------------------------------------------------
# Validation and replay outputs (T22 -> docs/metrics.json, T21 -> docs/replay.json)
# ---------------------------------------------------------------------------


class MetricCI(Contract):
    """A metric with its bootstrap 95% interval."""

    value: float = Field(description="Point estimate.")
    ci_low: float = Field(description="2.5th percentile.")
    ci_high: float = Field(description="97.5th percentile.")


class ReliabilityBin(Contract):
    """One bin of a 10-bin reliability plot."""

    bin_low: float = Field(ge=0, le=1, description="Bin lower edge.")
    bin_high: float = Field(ge=0, le=1, description="Bin upper edge.")
    mean_pred: float | None = Field(None, ge=0, le=1, description="Mean predicted probability in the bin (None if empty).")
    frac_pos: float | None = Field(None, ge=0, le=1, description="Observed positive rate in the bin (None if empty).")
    n: int = Field(ge=0, description="Test trials in the bin.")


class TargetMetrics(Contract):
    """Test-split metrics for one target."""

    target: Target = Field(description="Target.")
    n_test: int = Field(ge=0, description="Labeled test trials.")
    n_pos: int = Field(ge=0, description="Positives among them.")
    auc: MetricCI = Field(description="LightGBM test ROC AUC.")
    baseline_auc: MetricCI = Field(description="Logistic baseline test ROC AUC.")
    brier: float = Field(ge=0, description="LightGBM Brier score after isotonic calibration.")
    baseline_brier: float = Field(ge=0, description="Baseline Brier score.")
    reliability: list[ReliabilityBin] = Field(default_factory=list, description="10 bins.")


class HitRate(Contract):
    """Share of test trials with label_amend whose widened field is in our top-3 flags."""

    n_trials: int = Field(ge=0, description="Test trials with label_amend and a parseable widened field.")
    hit_rate: float = Field(ge=0, le=1, description="Clinova top-3 flags.")
    random_baseline: float = Field(ge=0, le=1, description="Three random rules.")
    marginal_baseline: float = Field(ge=0, le=1, description="Three rules with the largest marginal exclusion.")


class Ablation(Contract):
    """With vs without simulation features on the simulated test subset."""

    target: Target = Field(Target.accrual_fail, description="Target ablated.")
    n_trials: int = Field(ge=0, description="Simulated test trials.")
    auc_with: float = Field(description="AUC with simulation features.")
    auc_without: float = Field(description="AUC without.")
    diff: MetricCI = Field(description="auc_with - auc_without with paired bootstrap CI.")


class ValidationMetrics(Contract):
    """Shape of docs/metrics.json."""

    model_version: str = Field(description="Model bundle version.")
    generated_at: datetime = Field(description="When the script ran (UTC).")
    split_date: date = Field(date(2018, 1, 1), description="Train/test cutoff on start_date.")
    n_train: int = Field(ge=0, description="Labeled training trials.")
    n_test: int = Field(ge=0, description="Labeled test trials.")
    targets: list[TargetMetrics] = Field(description="One entry per target.")
    hit_rate: HitRate | None = Field(None, description="Flag hit rate, if computed.")
    ablation: Ablation | None = Field(None, description="Simulation ablation, if computed.")
    notes: list[str] = Field(default_factory=list, description="Honest caveats written by the script.")


class ReplayArm(Contract):
    """Metrics of one model variant in one replay month."""

    auc: float | None = Field(None, description="ROC AUC (None when only one class was observed).")
    brier: float | None = Field(None, ge=0, description="Brier score (None when nothing was scored).")


class ReplayMonth(Contract):
    """One step of the time-machine replay."""

    month: str = Field(pattern=r"^\d{4}-\d{2}$", description="Simulated month, 'YYYY-MM'.")
    n_scored: int = Field(ge=0, description="Trials scored whose outcome arrives within the horizon.")
    n_pos: int = Field(ge=0, description="Positives among them.")
    n_new_outcomes: int = Field(ge=0, description="Outcomes added to training after this month.")
    retrained: ReplayArm = Field(description="Model retrained every month.")
    frozen: ReplayArm = Field(description="Model frozen at freeze_date.")


class ReplayResult(Contract):
    """Shape of docs/replay.json."""

    target: Target = Field(Target.accrual_fail, description="Target replayed.")
    freeze_date: date = Field(date(2019, 12, 31), description="Frozen model's training cutoff.")
    horizon_months: int = Field(6, ge=1, description="Score trials whose outcome arrives within this many months.")
    months: list[ReplayMonth] = Field(description="Monthly results, chronological.")
    summary: str = Field(description="One honest sentence on retrained vs frozen.")
    generated_at: datetime = Field(description="When the script ran (UTC).")
