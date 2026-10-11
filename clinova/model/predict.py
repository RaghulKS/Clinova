"""Scoring and criterion flags (owner: T22). See ARCHITECTURE.md 5.6 and 7.

No Gemini calls: features come from the parsed rules, first-version JSON, stored embeddings,
a cached virtual cohort and (optionally) given simulation runs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from clinova.contracts import (
    FIELD_SPECS,
    CriteriaRule,
    CriterionFlag,
    Driver,
    Edit,
    Operator,
    ParsedProtocol,
    RiskScore,
    SimRunSummary,
    Target,
)
from clinova.model import features as F
from clinova.model.train import TARGETS, draws, load_bundle, predict_p

_NCT = re.compile(r"^NCT\d{8}$")


# ---------------------------------------------------------------------------------------------
# Context and features for one protocol
# ---------------------------------------------------------------------------------------------


def _context(protocol_or_trial: ParsedProtocol | str) -> tuple[ParsedProtocol | None, pd.DataFrame, dict, pd.DataFrame]:
    """(protocol, one-row trials frame, embeddings, sim features) for an NCT id or a ParsedProtocol."""
    pid = protocol_or_trial if isinstance(protocol_or_trial, str) else protocol_or_trial.trial_id
    protocol = None if isinstance(protocol_or_trial, str) else protocol_or_trial
    trials, emb, sim = pd.DataFrame({"nct_id": [pid], "first_version": [{}]}), {}, pd.DataFrame(columns=F.SIM_COLS)
    if _NCT.match(pid):
        found = F.load_trials([pid], splits=None)
        if len(found):
            trials = found
        elif protocol is None:
            raise LookupError(f"{pid} is not in the trials table")
        emb = F.load_embeddings([pid])
        sim = F.load_sim_features([pid])
        if protocol is None:
            protocol = F.load_protocols([pid]).get(pid)  # None: never parsed, rules features stay NaN
    return protocol, trials, emb, sim


def _frame(bundle: Mapping[str, Any], trials: pd.DataFrame, variants: Mapping[str, ParsedProtocol | None],
           emb: Mapping[str, np.ndarray], sim: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Raw and model features for several variants of one trial (same first-version JSON)."""
    base = trials.iloc[0]
    rows = pd.DataFrame({"nct_id": list(variants), "first_version": [base["first_version"]] * len(variants)})
    vec = next(iter(emb.values()), None)
    vemb = {k: vec for k in variants} if vec is not None else {}
    vsim = pd.DataFrame([sim.iloc[0]] * len(variants), index=list(variants)) if len(sim) else None
    raw = F.build_features(rows, {k: v for k, v in variants.items() if v is not None}, vsim)
    return raw, F.apply_ref(raw, vemb, bundle["ref"]).reindex(columns=bundle["feature_names"])


# ---------------------------------------------------------------------------------------------
# Explanations and confidence
# ---------------------------------------------------------------------------------------------

_EXPLAINERS: dict[int, Any] = {}


def shap_values(model, X: pd.DataFrame) -> np.ndarray:
    """SHAP TreeExplainer contributions (log-odds of the positive class), shape (rows, features)."""
    import shap

    ex = _EXPLAINERS.setdefault(id(model), shap.TreeExplainer(model))
    sv = ex.shap_values(X.astype(float))
    sv = sv[1] if isinstance(sv, list) else sv
    return sv[..., 1] if sv.ndim == 3 else sv


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}".rstrip("0").rstrip(".")


def describe(feature: str, raw: Mapping[str, Any], ref: Mapping[str, Any]) -> tuple[str, float | None]:
    """Plain-words label and display value of one feature for one trial."""
    if feature.startswith("strict_"):
        base = feature[len("strict_"):]
        v, med = raw.get(base), ref["medians"].get(base)
        if v is None or pd.isna(v):
            return f"{F.FEATURE_NAMES[base]}: no parsed rule", None
        side = "same as" if med is None or v == med else (
            "stricter than" if F.STRICT_SIGN[base] * (v - med) > 0 else "looser than")
        return f"{F.FEATURE_NAMES[base]} {_fmt(v)}, {side} the median training trial ({_fmt(med) if med is not None else 'n/a'})", float(v)
    if feature.startswith("emb_pc"):
        return F.FEATURE_NAMES[feature], None
    v = raw.get(feature)
    name = F.FEATURE_NAMES.get(feature, feature.replace("_", " "))
    if v is None or pd.isna(v):
        return f"{name}: not stated", None
    if feature == "log_planned_enrollment":
        return f"Planned enrollment {math.expm1(v):,.0f} patients", float(v)
    if feature in ("screen_eligible_frac", "screen_max_excl_frac", "us_share", "frac_unparseable") or feature.startswith("sim_"):
        return f"{name}: {v:.0%}", float(v)
    return f"{name}: {_fmt(float(v))}", float(v)


def confidence(nct_id: str, emb: Mapping[str, np.ndarray], ref: Mapping[str, Any]) -> str:
    """'low' above the training 90th percentile of mean distance to the 10 nearest training trials,
    'high' at or below the median, else 'medium'. No embedding (e.g. a pasted draft): 'low'."""
    if not emb or ref.get("train_emb") is None:
        return "low"
    d = F.knn_distance(next(iter(emb.values())), ref, exclude=nct_id)
    return "high" if d <= ref["knn_p50"] else "medium" if d <= ref["knn_p90"] else "low"


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------


def score(
    protocol_or_trial: ParsedProtocol | str,
    *,
    sim: Sequence[SimRunSummary] | None = None,
    model_version: str | None = None,
    bundle: Mapping[str, Any] | None = None,
) -> list[RiskScore]:
    """Calibrated RiskScores for accrual_fail, shortfall and amend (in that order) for a parsed
    protocol or an NCT id (its first registered version). Ranges combine bootstrap models and
    the spread across ``sim`` seeds; confidence comes from embedding distance to the 10 nearest
    training trials. Callers log results with ``loop.ledger.record_prediction``.

    A target the bundle could not train (too few labels, see ``bundle["skipped"]``) gets an explicit
    placeholder: p 0.5, range 0-1, confidence low, one driver saying there is no model. Raises
    NotImplementedError when no bundle has been trained, so the API falls back to its fixtures."""
    bundle = bundle or _bundle(model_version)
    protocol, trials, emb, sim_df = _context(protocol_or_trial)
    tid = trials.iloc[0]["nct_id"]
    seeds = [F.sim_row(s) for s in sim] if sim else []
    if seeds:
        sim_df = pd.DataFrame([pd.DataFrame(seeds).mean()], index=[tid])
    raw, X = _frame(bundle, trials, {tid: protocol}, emb, sim_df)
    Xs = X
    if seeds and bundle["use_sim"]:  # one row per simulation seed for the range
        Xs = pd.concat([_frame(bundle, trials, {tid: protocol}, emb, pd.DataFrame([s], index=[tid]))[1] for s in seeds])
    conf = confidence(tid, emb, bundle["ref"])
    out = []
    for target in TARGETS:
        if target not in bundle["models"]:
            why = bundle["skipped"].get(target, "not trained")
            out.append(RiskScore(target=Target(target), p=0.5, ci_low=0.0, ci_high=1.0, confidence="low",
                                 model_version=bundle["version"], top_drivers=[Driver(
                                     feature="no_model", contribution=0.0,
                                     label=f"No {target} model yet ({why}): this 50% is a placeholder, not a prediction.")]))
            continue
        p = float(predict_p(bundle, target, X)[0])
        lo, hi = np.percentile(draws(bundle, target, Xs).ravel(), [2.5, 97.5])
        contrib = shap_values(bundle["models"][target], X)[0]
        drivers = []
        for j in np.argsort(-np.abs(contrib))[:4]:
            if contrib[j] == 0:
                break
            label, value = describe(X.columns[j], raw.iloc[0].to_dict(), bundle["ref"])
            drivers.append(Driver(feature=X.columns[j], label=label, value=value, contribution=float(contrib[j])))
        out.append(RiskScore(target=Target(target), p=p, ci_low=float(min(lo, p)), ci_high=float(max(hi, p)),
                             confidence=conf, top_drivers=drivers, model_version=bundle["version"]))
    return out


def _bundle(model_version: str | None) -> Mapping[str, Any]:
    try:
        return load_bundle(model_version)
    except FileNotFoundError as e:  # no trained model: callers (the API) fall back to fixtures
        raise NotImplementedError(str(e)) from e


_ORDERED = (Operator.lt, Operator.le, Operator.gt, Operator.ge)


def relax_edit(rule: CriteriaRule, ref: Mapping[str, Any]) -> Edit | None:
    """One step looser: the next threshold other training trials used (numeric) or the next
    vocabulary step (ordinal). None for rules that can only be removed."""
    spec = FIELD_SPECS[rule.field]
    if rule.operator not in _ORDERED or rule.value is None or isinstance(rule.value, (list, bool)):
        return None
    min_requirement = (rule.kind == "inclusion") == (rule.operator in (Operator.ge, Operator.gt))
    up = not min_requirement  # raising the threshold loosens a maximum (or an exclusion floor)
    if spec.kind == "ordinal":
        if rule.value not in spec.values:
            return None
        j = spec.values.index(rule.value) + (1 if up else -1)
        return Edit(kind="relax_rule", target_id=rule.rule_id, new_value=spec.values[j]) if 0 <= j < len(spec.values) else None
    if spec.kind != "numeric" or not isinstance(rule.value, (int, float)):
        return None
    v, lo, hi = float(rule.value), spec.min if spec.min is not None else -np.inf, spec.max if spec.max is not None else np.inf
    steps = [x for x in ref["ladders"].get(rule.field.value, []) if lo <= x <= hi and (x > v if up else x < v)]
    if not steps:
        return None
    new = min(steps) if up else max(steps)
    return Edit(kind="relax_rule", target_id=rule.rule_id, new_value=int(new) if float(new).is_integer() else new)


def flag_criteria(protocol: ParsedProtocol, *, k: int = 3, bundle: Mapping[str, Any] | None = None,
                  model_version: str | None = None) -> list[CriterionFlag]:
    """Rank rules by the drop in predicted accrual-failure probability when each is relaxed one
    step or removed (re-screen cached cohort, rebuild features, re-score). Returns the top ``k``
    flags (rank 1 first), each saying where, why and what to do. Locked and unparseable rules are skipped.

    Probabilities are ``train.predict_p`` (mean of the calibrated main and bootstrap models); ties
    (common with a small model) are broken by the rule's marginal exclusion on the virtual cohort."""
    from clinova.simulate.screen import _label, apply_edits, screen

    bundle = bundle or _bundle(model_version)
    target = Target.accrual_fail.value
    if target not in bundle["models"]:
        return []
    _, trials, emb, sim = _context(protocol)
    rules = F.screenable(protocol.rules)
    ok = {r.rule_id for r in rules}
    cands = [r for r in protocol.rules if r.rule_id in ok and r.parse_status == "ok" and not r.locked]
    if not cands:
        return []
    _, funnel = screen(rules, F.screen_cohort())
    marg = {m.rule_id: m for m in funnel.marginal}
    variants: dict[str, ParsedProtocol] = {"orig": protocol}
    meta = {}
    for r in cands:
        for e in (relax_edit(r, bundle["ref"]), Edit(kind="drop_rule", target_id=r.rule_id)):
            if e is not None:
                key = f"{r.rule_id}:{e.kind}"
                variants[key] = protocol.model_copy(update={"rules": apply_edits(protocol.rules, [e])})
                meta[key] = (r, e)
    _, X = _frame(bundle, trials, variants, emb, sim)
    p = predict_p(bundle, target, X)
    p0 = float(p[0])
    best: dict[str, tuple[float, Edit, float]] = {}
    for key, p1 in zip(list(variants)[1:], p[1:], strict=True):
        r, e = meta[key]
        delta = p0 - float(p1)
        if r.rule_id not in best or delta > best[r.rule_id][0] + 1e-12:  # relax is tried first and wins ties
            best[r.rule_id] = (delta, e, float(p1))
    by_id = {r.rule_id: r for r in cands}
    order = sorted(best, key=lambda rid: (-round(best[rid][0], 6), -(marg[rid].excluded_n if rid in marg else 0)))
    flags = []
    for rank, rid in enumerate(order[:k], 1):
        r, (delta, e, p1) = by_id[rid], best[rid]
        m = marg.get(rid)
        excl = (f"On its own it excludes {m.excluded_frac:.0%} of virtual NSCLC patients; dropping it would make "
                f"{m.excluded_only_n} of {funnel.n_screened} newly eligible. ") if m else ""
        change = "relaxing" if e.kind == "relax_rule" else "removing"
        risk = (f"The model predicts {change} it lowers accrual-failure risk from {p0:.1%} to {p1:.1%}." if delta > 0 else
                f"The model predicts no drop in accrual-failure risk from {change} it; ranked by its exclusion share.")
        if e.kind == "relax_rule":
            new = apply_edits([r], [e])[0]
            todo = f"Consider {_label(new).rsplit(' (', 1)[0]} instead, a cutoff other NSCLC trials used, if safety allows."
        else:
            todo = "Consider removing this criterion, or state in the protocol why it is needed."
        flags.append(CriterionFlag(
            rule_id=rid, field=r.field, rank=rank,
            where=f"{r.kind.capitalize()} {rid}: {r.raw_text.strip()[:160]}",
            why=excl + risk, what_to_do=todo, delta_p=float(delta), suggested_edit=e,
        ))
    return flags
