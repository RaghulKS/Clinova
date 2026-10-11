"""The results screen: Funnel, Risk, flags, Fixes and the backtest reveal. Every number shown comes from
the API response; wording frames everything as a suggestion to the protocol author."""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field

import streamlit as st

from clinova.api.fallbacks import FIXTURE_NCT_ID
from clinova.api.schemas import AcceptResponse, FeedbackIn, ValidationResponse
from clinova.app import charts, fmt, theme
from clinova.app import reveal as rv
from clinova.app.client import ApiClient, ApiError
from clinova.app.fmt import money, pct, signed_pct, signed_pts
from clinova.app.rules_table import OPERATOR_SYMBOL, rule_in_words, value_to_text
from clinova.app.theme import esc
from clinova.contracts import (
    BacktestResult,
    CriterionFlag,
    Edit,
    Fix,
    ParsedProtocol,
    RiskScore,
    RunResult,
    RunStatus,
)

__all__ = ["money", "signed_pct", "signed_pts", "pct"]  # re-exported for callers and tests

TARGET_TITLES = {
    "accrual_fail": ("Accrual failure", "Chance the trial stops early for slow enrollment"),
    "shortfall": ("Enrollment shortfall", "Chance it enrolls under 80% of its target"),
    "amend": ("Eligibility amendment", "Chance the sponsor later has to widen the criteria"),
}
FIX_TITLES = {
    "fastest": "Fastest enrollment",
    "lowest_dropout": "Fewest dropouts",
    "balanced": "Balanced",
    "other": "Other option",
}
CONFIDENCE_TEXT = {
    "high": "High confidence",
    "medium": "Medium confidence",
    "low": "Low confidence: unlike most trials the model has seen",
}


@dataclass
class Reference:
    """What the held-out evaluation says about each target (only from real, non-fixture metrics).

    ``status``: ``validated`` (test AUC interval above 0.5 and Brier better than the baseline's),
    ``not_validated`` (evaluated, but not both), ``no_model`` (no test metrics for the target).
    Empty when the metrics are fixture placeholders: the app then shows scores without these labels."""

    status: dict[str, str] = field(default_factory=dict)
    test_p: dict[str, list[float]] = field(default_factory=dict)
    n_test: dict[str, int] = field(default_factory=dict)

    def percentile(self, target: str, p: float) -> float | None:
        xs = self.test_p.get(target)
        if not xs:
            return None
        return bisect.bisect_left(xs, p) / len(xs)  # share of test trials scored strictly lower

    def no_model(self, target: str) -> bool:
        return self.status.get(target) == "no_model"


def build_reference(resp: ValidationResponse | None) -> Reference:
    if resp is None or theme.is_fixture(resp.source):
        return Reference()
    m = resp.metrics
    status: dict[str, str] = {}
    for t in m.targets:
        ok = t.auc.ci_low > 0.5 and t.brier < t.baseline_brier
        status[t.target.value] = "validated" if ok else "not_validated"
    for target in TARGET_TITLES:
        status.setdefault(target, "no_model")
    return Reference(status=status, test_p=dict(resp.test_p), n_test={t.target.value: t.n_test for t in m.targets})


def edit_in_words(edit: Edit, protocol: ParsedProtocol) -> str:
    """Plain words for one edit, e.g. 'Relax I12 (creatinine clearance >= 60 mL/min) to 45'."""
    rules = {r.rule_id: r for r in protocol.rules}
    visits = {str(v.visit_no): v for v in protocol.visits}
    if edit.kind in ("relax_rule", "drop_rule"):
        rule = rules.get(edit.target_id)
        what = f"{edit.target_id} ({rule_in_words(rule)})" if rule else edit.target_id
        if edit.kind == "drop_rule":
            return f"Drop {what}"
        val = value_to_text(edit.new_value)
        unit = f" {rule.unit}" if rule is not None and rule.unit and not isinstance(edit.new_value, list) else ""
        to = f"{OPERATOR_SYMBOL[edit.new_operator.value]} {val}{unit}" if edit.new_operator else f"{val}{unit}"
        return f"Relax {what} to {to}"
    visit = visits.get(edit.target_id)
    name = f"visit {edit.target_id} ({visit.name})" if visit else f"visit {edit.target_id}"
    return {
        "cut_visit": f"Remove {name}",
        "merge_visits": f"Merge {name} with the next visit",
        "make_remote": f"Allow {name} to be done remotely",
    }[edit.kind]


def _rule_label(label: str) -> str:
    """'ECOG <= 1 (I6)' -> 'ECOG <= 1'."""
    return label.rsplit(" (", 1)[0] if label.endswith(")") and " (" in label else label


def stall_rule(res: RunResult) -> tuple[str | None, str]:
    """The criterion the funnel highlights: Clinova's top flag when there is one (the model's pick),
    else the rule that excludes the most virtual patients on its own. Returns (rule_id, why)."""
    f = res.funnel
    if f is None:
        return None, ""
    in_funnel = {s.rule_id for s in f.stages if s.rule_id} | {m.rule_id for m in f.marginal}
    for flag in sorted(res.flags, key=lambda x: x.rank):
        if flag.rule_id in in_funnel:
            return flag.rule_id, "flag"
    if f.marginal:
        return max(f.marginal, key=lambda m: m.excluded_n).rule_id, "marginal"
    return None, ""


def flag_dollars(flag: CriterionFlag, fixes: list[Fix]) -> tuple[float, Fix] | None:
    """Dollar figure for a flag: the expected value of avoided amendments of the suggested fix that
    changes the same rule (largest if several). None when no fix touches it."""
    matching = [fx for fx in fixes if any(e.target_id == flag.rule_id and e.kind in ("relax_rule", "drop_rule") for e in fx.edits)]
    if not matching:
        return None
    best = max(matching, key=lambda fx: fx.dollar_value)
    return best.dollar_value, best


# --------------------------------------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------------------------------------


def _kpi(k: str, v: str, s: str = "", hi: bool = False) -> str:
    return f'<div class="cl-kpi{" hi" if hi else ""}"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="s">{esc(s)}</div></div>'


def funnel_panel(status: RunStatus) -> None:
    res = status.result
    assert res is not None
    st.markdown(
        f'<div class="cl-panel-title">Funnel {theme.fixture_tag(status.source)}</div>'
        '<div class="cl-panel-sub">Virtual patients screened against each criterion in protocol order, and who would be lost.</div>',
        unsafe_allow_html=True,
    )
    f = res.funnel
    if f is None:
        st.markdown(theme.empty("No funnel for this run.", "The screening stage returned nothing."), unsafe_allow_html=True)
        return
    hi, why = stall_rule(res)
    _, steps = charts.funnel_steps(f, res.sim)
    kpis = [_kpi("Screened", f"{f.n_screened:,}", "virtual patients")]
    kpis.append(_kpi("Eligible", f"{f.n_eligible:,}", f"{pct(f.eligible_frac)} pass every criterion", hi=True))
    if len(steps) == 4:
        kpis.append(_kpi("Would enroll", f"{steps[2][1]:,}", "estimate"))
        kpis.append(_kpi("Would complete", f"{steps[3][1]:,}", "estimate"))
    st.markdown(f'<div class="cl-kpis">{"".join(kpis)}</div>', unsafe_allow_html=True)
    st.plotly_chart(charts.funnel_waterfall(f, res.sim, hi), width="stretch", key="funnel_waterfall", config={"displayModeBar": False})
    top = next((m for m in f.marginal if m.rule_id == hi), None)
    if top is not None:
        lead = "Clinova's top flag" if why == "flag" else "The funnel stalls here"
        n_txt = f"In this run's {f.n_screened:,} virtual patients, on its own"
        st.markdown(
            f'<div class="cl-callout"><b>{lead}: {esc(top.rule_id)}, {esc(_rule_label(top.label))}.</b> '
            f"{n_txt} it rules out {top.excluded_n:,} ({pct(top.excluded_frac)}); "
            f"{top.excluded_only_n:,} of them fail only this criterion. You may want to check whether it is needed as written.</div>",
            unsafe_allow_html=True,
        )
    if f.skipped_rules:
        st.markdown(
            f'<div class="cl-note">Not applied because they could not be parsed: {esc(", ".join(f.skipped_rules))}.</div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="cl-note">Would enroll and would complete apply the simulated persona decisions to all eligible virtual '
        "patients: an estimate, not a count.</div>",
        unsafe_allow_html=True,
    )


def funnel_details(status: RunStatus) -> None:
    """Secondary funnel views: each criterion on its own, dropout after enrolling, the funnel table."""
    res = status.result
    assert res is not None
    f = res.funnel
    if f is None:
        return
    hi, _ = stall_rule(res)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown('<div class="cl-section">Patients failing each criterion on its own</div>', unsafe_allow_html=True)
        st.plotly_chart(charts.exclusions(f, highlight=hi), width="stretch", key="funnel_excl", config={"displayModeBar": False})
    with c2:
        if res.sim:
            sim = res.sim[0]
            st.markdown('<div class="cl-section">Dropout after enrolling</div>', unsafe_allow_html=True)
            if sim.dropout_curve:
                st.plotly_chart(charts.dropout_curve(sim), width="stretch", key="funnel_dropout", config={"displayModeBar": False})
            reasons = ", ".join(f"{r.tag.value.replace('_', ' ')} ({pct(r.share)})" for r in sim.top_reasons[:3])
            if reasons:
                st.markdown(f'<div class="cl-note">Most cited burdens: {esc(reasons)}.</div>', unsafe_allow_html=True)
        else:
            st.markdown(theme.empty("No simulation summary for this run."), unsafe_allow_html=True)
    st.dataframe(
        [{"Step": s.label, "Remaining": s.remaining, "Dropped here": s.excluded} for s in f.stages],
        hide_index=True,
        width="stretch",
    )


# --------------------------------------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------------------------------------


def _drivers(r: RiskScore, n: int) -> str:
    return "".join(
        f'<div class="cl-driver">{esc(d.label)} <span class="dir">({"raises" if d.contribution > 0 else "lowers"} the estimate)</span></div>'
        for d in r.top_drivers[:n]
    )


def _risk_card(r: RiskScore, *, hero: bool, ref: Reference) -> str:
    title, sub = TARGET_TITLES.get(r.target.value, (r.target.value, ""))
    target = r.target.value
    pad = "" if hero else " tight"
    big = "cl-hero" if hero else "cl-mid"
    if ref.no_model(target):
        why = r.top_drivers[0].label if r.top_drivers else "There are too few labeled trials to train this model."
        return (
            f'<div class="cl-card{pad}" style="opacity:0.8"><h4>{esc(title)}<span class="cl-level low">No model yet</span></h4>'
            f'<div class="cl-note" style="margin-bottom:0.4rem">{esc(sub)}</div>'
            f'<div class="{big}" style="color:{theme.GREY_DARK}">n/a</div>'
            f'<div class="cl-note" style="margin-top:0.3rem">{esc(why)}</div></div>'
        )
    q = ref.percentile(target, r.p)
    if q is not None:
        level, cls = fmt.percentile_level(q)
        n = ref.n_test.get(target, len(ref.test_p.get(target, [])))
        context = f"Higher than {pct(q, 0)} of {n} held-out test trials"
    else:
        level, cls = fmt.risk_level(r.p)
        context = ""
    chip = f'<span class="cl-level {cls}">{level}</span>'
    if ref.status.get(target) == "not_validated":
        chip = '<span class="cl-level low">Not validated</span>'
        context = (context + ". " if context else "") + "On held-out trials this model did not beat the baseline; treat it as a rough signal"
    drivers = _drivers(r, 4 if hero else 2)
    return (
        f'<div class="cl-card accent{pad}"><h4>{esc(title)}{chip}</h4>'
        f'<div class="cl-note" style="margin-bottom:0.4rem">{esc(sub)}</div>'
        f'<div class="{big}">{pct(r.p)}</div>'
        f'<div class="cl-range">likely range {pct(r.ci_low)} to {pct(r.ci_high)}</div>'
        + (f'<div class="cl-note" style="margin-top:0.2rem">{esc(context)}.</div>' if context else "")
        + f'<div style="margin:0.45rem 0 0.45rem"><span class="cl-badge {esc(r.confidence)}">{esc(CONFIDENCE_TEXT[r.confidence])}</span></div>'
        + (f'<div class="cl-note" style="margin-bottom:0.1rem">What drives this estimate</div>{drivers}' if drivers else "")
        + "</div>"
    )


def risk_panel(status: RunStatus, ref: Reference | None = None) -> None:
    res = status.result
    assert res is not None
    ref = ref or Reference()
    st.markdown(
        f'<div class="cl-panel-title">Risk {theme.fixture_tag(status.source)}</div>'
        '<div class="cl-panel-sub">Calibrated probabilities with a range: estimates to inform your judgement, not a verdict.</div>',
        unsafe_allow_html=True,
    )
    if not res.risks:
        st.markdown(theme.empty("No risk scores for this run.", "The model did not return scores."), unsafe_allow_html=True)
        return
    order = {"accrual_fail": 0, "shortfall": 1, "amend": 2}
    risks = sorted(res.risks, key=lambda r: order.get(r.target.value, 9))
    st.markdown(_risk_card(risks[0], hero=True, ref=ref), unsafe_allow_html=True)
    rest = risks[1:]
    if rest:
        cols = st.columns(len(rest), gap="small")
        for col, r in zip(cols, rest, strict=True):
            col.markdown(_risk_card(r, hero=False, ref=ref), unsafe_allow_html=True)
    model_v = risks[0].model_version
    st.markdown(f'<div class="cl-note">Model version: {esc(model_v)}.</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------------------------------------
# Flags: where, why, what to do, with a dollar figure
# --------------------------------------------------------------------------------------------------------


NO_AMEND_MODEL = "not estimated: no amendment model yet"


def _flag_html(f: CriterionFlag, fixes: list[Fix], ref: Reference) -> str:
    dollars = flag_dollars(f, fixes)
    if ref.no_model("amend"):
        money_row = (
            '<div class="money"><span class="cl-note">Dollar value of avoided amendments: not estimated, because there is no '
            "amendment model yet (too few amended trials to learn from).</span></div>"
        )
    elif dollars is not None:
        value, fx = dollars
        money_row = (
            f'<div class="money"><span>Expected value of avoided amendments<br><span class="cl-note">'
            f'if changed as in the "{esc(FIX_TITLES.get(fx.label, fx.label))}" fix</span></span><b>{money(value)}</b></div>'
        )
    else:
        money_row = '<div class="money"><span class="cl-note">No dollar estimate: no suggested fix changes this criterion.</span></div>'
    return (
        f'<div class="cl-flag"><div class="rank">Flag {f.rank}</div><div class="where">{esc(f.where)}</div>'
        f'<div class="lbl">Why it matters</div><div class="txt">{esc(f.why)}</div>'
        f'<div class="lbl">What you could do</div><div class="txt">{esc(f.what_to_do)}</div>'
        + (
            f'<div class="cl-note" style="margin-top:0.35rem">Changing it is estimated to lower accrual-failure risk by '
            f"{esc(fmt.pts(abs(f.delta_p)))}.</div>"
            if abs(f.delta_p) >= 0.0005
            else ""
        )
        + f"{money_row}</div>"
    )


def flags_row(status: RunStatus, ref: Reference | None = None) -> None:
    res = status.result
    assert res is not None
    ref = ref or Reference()
    if res.flags and all(abs(f.delta_p) < 0.0005 for f in res.flags):
        sub = (
            "Ranked by how many virtual patients each criterion excludes on its own; the risk model predicts no change "
            "in accrual-failure risk from editing them. Where it is, why it matters, what you could do."
        )
    else:
        sub = "The criteria whose change lowers the predicted risk most: where it is, why it matters, what you could do, and what it is worth."
    st.markdown(f'<div class="cl-h2-sub">{esc(sub)}</div>', unsafe_allow_html=True)
    if not res.flags:
        st.markdown(
            theme.empty(
                "No criterion flags for this run.",
                "Flags appear once the risk model can rank criteria for this protocol.",
            ),
            unsafe_allow_html=True,
        )
        return
    flags = sorted(res.flags, key=lambda x: x.rank)[:3]
    cols = st.columns(3, gap="medium")
    for col, flag in zip(cols, flags, strict=False):
        col.markdown(_flag_html(flag, res.fixes, ref), unsafe_allow_html=True)


# --------------------------------------------------------------------------------------------------------
# Fixes
# --------------------------------------------------------------------------------------------------------


def _metric(label: str, value: str, rng: str = "", cls: str = "") -> str:
    r = f'<span class="r">{rng}</span>' if rng else ""
    return f'<div class="cl-metric {cls}"><span>{esc(label)}</span><span class="v">{value}{r}</span></div>'


def _rng(lo: float, hi: float, f) -> str:
    return f"range {f(lo)} to {f(hi)}"


def _fix_card(fix: Fix, protocol: ParsedProtocol, decision: str | None, ref: Reference | None = None) -> str:
    ref = ref or Reference()
    rg = fix.ranges
    if ref.no_model("amend"):
        amend_rows = _metric("Amendment risk", '<span class="cl-note">not estimated</span>', "no amendment model yet") + _metric(
            "Expected value of avoided amendments", '<span class="cl-note">not estimated</span>', "needs an amendment model"
        )
    else:
        amend_rows = _metric(
            "Amendment risk",
            signed_pts(fix.delta_p_amend),
            _rng(rg.delta_p_amend.low, rg.delta_p_amend.high, signed_pts) if rg.delta_p_amend else "",
        ) + _metric("Expected value of avoided amendments", money(fix.dollar_value), cls="money")
    edits = "".join(f'<div class="cl-driver">{esc(edit_in_words(e, protocol))}</div>' for e in fix.edits)
    prec = "".join(
        f'<div class="cl-precedent"><b>{esc(p.nct_id)}</b>: {esc(p.detail)}</div>' for p in fix.precedents[:2]
    )
    badge = ""
    if decision:
        badge = f' <span class="cl-badge {"high" if decision == "accept" else "low"}">{"Accepted" if decision == "accept" else "Rejected"}</span>'
    div_fmt = lambda v: f"{v:+.2f}"  # noqa: E731
    return (
        f'<div class="cl-card accent"><h4>{esc(FIX_TITLES.get(fix.label, fix.label))}{badge}</h4>'
        f'<div style="font-size:0.9rem;margin-bottom:0.45rem">{esc(fix.summary)}</div>'
        f'<div class="cl-note" style="margin-bottom:0.1rem">Suggested edits</div>{edits}'
        '<div style="height:0.45rem"></div>'
        + _metric("Enrollment speed", signed_pct(fix.delta_enroll), _rng(rg.delta_enroll.low, rg.delta_enroll.high, signed_pct))
        + _metric("Dropout (share of enrolled)", signed_pts(fix.delta_dropout), _rng(rg.delta_dropout.low, rg.delta_dropout.high, signed_pts))
        + _metric("Diversity index", div_fmt(fix.delta_diversity), _rng(rg.delta_diversity.low, rg.delta_diversity.high, div_fmt))
        + amend_rows
        + (f'<div class="cl-note" style="margin:0.45rem 0 0.1rem">Precedents: comparable trials that used the looser rule</div>{prec}' if prec else
           '<div class="cl-note" style="margin-top:0.45rem">No precedent trials found for these edits.</div>')
        + "</div>"
    )


def _decide(client: ApiClient, fix: Fix, decision: str, status: RunStatus) -> None:
    """Accept goes to /fixes/accept (logs the decision, re-runs fast, returns before and after);
    reject only logs the decision."""
    trial_id = status.result.protocol.trial_id if status.result else status.trial_id
    try:
        if decision == "accept":
            t0 = time.monotonic()
            resp = client.accept_fix(status.run_id, fix.fix_id)
            st.session_state["accept_secs"] = time.monotonic() - t0
            st.session_state.setdefault("accepts", {})[fix.fix_id] = resp.model_dump(mode="json")
            st.session_state["feedback_source"] = resp.feedback_source.value
        else:
            resp = client.feedback(FeedbackIn(target_type="fix", target_id=fix.fix_id, trial_id=trial_id, decision="reject"))
            st.session_state["feedback_source"] = resp.source.value
        st.session_state.setdefault("decisions", {})[fix.fix_id] = decision
    except ApiError as exc:
        st.session_state["fix_error"] = str(exc)


def _fmt(v: float | None, kind: str = "pct") -> str:
    if v is None:
        return "not estimated"
    return f"{v:.2f}" if kind == "index" else pct(v)


# higher is better for these measures; lower is better for the rest
_HIGHER_BETTER = {"eligible_frac", "enroll_rate", "enroll_speed", "diversity_index"}
_SPEC = [
    ("Eligible virtual patients", "eligible_frac", "pct"),
    ("Enrollment rate of eligible", "enroll_rate", "pct"),
    ("Would enroll, share of screened", "enroll_speed", "pct"),
    ("Dropout (share of enrolled)", "dropout_rate", "pct"),
    ("Diversity index", "diversity_index", "index"),
    ("Accrual-failure risk", "accrual_fail_p", "pct"),
    ("Enrollment-shortfall risk", "shortfall_p", "pct"),
    ("Amendment risk", "amend_p", "pct"),
]


def _change(bv: float | None, av: float | None, key: str, kind: str) -> tuple[str, str]:
    if bv is None or av is None:
        return "", ""
    d = av - bv
    text = f"{d:+.2f}" if kind == "index" else signed_pts(d)
    if abs(d) < (0.005 if kind == "index" else 0.0005):  # below the shown precision
        return "no change", ""
    better = (d > 0) == (key in _HIGHER_BETTER)
    return text, "better" if better else "worse"


def _spec(skip: set[str]) -> list[tuple[str, str, str]]:
    return [row for row in _SPEC if row[1] not in skip]


def _skip(ref: Reference | None) -> set[str]:
    """Measures with no model behind them (their 'before' would be a placeholder): not shown."""
    if ref is None:
        return set()
    return {f"{t}_p" for t in ("accrual_fail", "shortfall", "amend") if ref.no_model(t)}


def before_after_rows(resp: AcceptResponse, ref: Reference | None = None) -> list[dict]:
    """Rows of the before and after table. The Basis column says whether the after number is a re-run
    or the optimizer's estimate."""
    basis = "re-run (surrogate, no new LLM calls)" if resp.basis == "quick_rerun" else "optimizer estimate"
    b, a = resp.before, resp.after
    rows = []
    for label, key, kind in _spec(_skip(ref)):
        bv, av = getattr(b, key), getattr(a, key)
        if bv is None and av is None:
            continue
        change, _ = _change(bv, av, key, kind)
        rows.append({"Measure": label, "Before": _fmt(bv, kind), "After": _fmt(av, kind), "Change": change, "Basis": basis})
    return rows


def _before_after_html(resp: AcceptResponse, ref: Reference | None = None) -> str:
    b, a = resp.before, resp.after
    body = ""
    for label, key, kind in _spec(_skip(ref)):
        bv, av = getattr(b, key), getattr(a, key)
        if bv is None and av is None:
            continue
        change, cls = _change(bv, av, key, kind)
        body += (
            f"<tr><td>{esc(label)}</td><td class='num'>{esc(_fmt(bv, kind))}</td>"
            f"<td class='num after'>{esc(_fmt(av, kind))}</td><td class='num {cls}'>{esc(change)}</td></tr>"
        )
    return (
        "<table class='cl-ba'><thead><tr><th>Measure</th><th style='text-align:right'>Before</th>"
        "<th style='text-align:right'>After</th><th style='text-align:right'>Change</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def fixes_row(status: RunStatus, client: ApiClient, ref: Reference | None = None) -> None:
    res: RunResult | None = status.result
    assert res is not None
    ref = ref or Reference()
    if not res.fixes:
        st.markdown(
            theme.empty(
                "No fix suggestions for this protocol yet.",
                "The optimizer is not connected, so Clinova shows no fixes rather than placeholder ones."
                if theme.is_fixture(status.source)
                else "The optimizer found no edit that lowers the predicted risk within the allowed changes.",
            ),
            unsafe_allow_html=True,
        )
        return
    decisions: dict[str, str] = st.session_state.get("decisions", {})
    fixes = res.fixes[:3]
    cols = st.columns(len(fixes), gap="medium")
    for col, fix in zip(cols, fixes, strict=True):
        d = decisions.get(fix.fix_id)
        with col:
            st.markdown(_fix_card(fix, res.protocol, d, ref), unsafe_allow_html=True)
            c1, c2, _ = st.columns([1, 1, 2])
            c1.button("Accept", key=f"acc_{fix.fix_id}", type="primary", on_click=_decide, args=(client, fix, "accept", status), disabled=d is not None, width="stretch")
            c2.button("Reject", key=f"rej_{fix.fix_id}", on_click=_decide, args=(client, fix, "reject", status), disabled=d is not None, width="stretch")
    if err := st.session_state.pop("fix_error", None):
        st.error(err)
    accepts: dict = st.session_state.get("accepts", {})
    accepted = [f for f in fixes if f.fix_id in accepts]
    if accepted:
        fix = accepted[-1]
        resp = AcceptResponse.model_validate(accepts[fix.fix_id])
        secs = st.session_state.get("accept_secs")
        basis = (
            "Re-run with the edits applied: criteria re-screened, enrollment and dropout re-estimated by the fast surrogate, risks re-scored. No new LLM calls."
            if resp.basis == "quick_rerun"
            else "The fast re-run was not available, so the after column applies the optimizer's own estimates."
        )
        st.markdown(
            f'<div class="cl-section" id="cl-before-after">Before and after: {esc(FIX_TITLES.get(fix.label, fix.label))} '
            f"{theme.fixture_tag(resp.source)}</div>",
            unsafe_allow_html=True,
        )
        left, right = st.columns([1.6, 1], gap="large")
        with left:
            st.markdown(_before_after_html(resp, ref), unsafe_allow_html=True)
        with right:
            took = f" Took {secs:.1f} s." if secs is not None else ""
            st.markdown(f'<div class="cl-callout">{esc(basis)}{esc(took)}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="cl-note">Your decision was recorded ({"in the database" if resp.feedback_source.value == "live" else "in memory only"}) '
                "and appears in the Audit log. Clinova suggests; the final protocol is yours to decide.</div>",
                unsafe_allow_html=True,
            )


# --------------------------------------------------------------------------------------------------------
# Backtest reveal
# --------------------------------------------------------------------------------------------------------


def reveal(payload: dict, pointed_at: list[str], fixes: list[Fix] | None = None) -> None:
    """Version-history reveal for a backtest: when the sponsor widened the criteria, when the trial ended,
    and whether that was a rule Clinova pointed at on the original protocol."""
    bt = BacktestResult.model_validate(payload)
    t = bt.trial
    status_words = t.status.value.replace("_", " ").capitalize()
    outcome = status_words + (f' on {fmt.nice_date(t.completion_date)}' if t.completion_date and t.status.value in ("TERMINATED", "COMPLETED", "WITHDRAWN") else "")
    why = f': "{esc(t.why_stopped)}"' if t.why_stopped else ""
    enroll = rv.enrollment_line(bt)
    st.markdown(
        f'<div class="cl-trial"><div><span class="t">{esc(t.title)}</span><span class="id">{esc(t.nct_id)}</span> {theme.fixture_tag(bt.source) if t.nct_id == FIXTURE_NCT_ID else ""}'
        f'<div class="cl-note" style="margin-top:0.2rem">Outcome: <b>{esc(outcome)}</b>{why}'
        + (f" &middot; {esc(enroll)}" if enroll else "")
        + "</div></div></div>",
        unsafe_allow_html=True,
    )
    points = rv.milestones(bt)
    if points:
        st.plotly_chart(charts.timeline(points), width="stretch", key="reveal_timeline", config={"displayModeBar": False})
    matched = rv.flagged_and_widened(bt, pointed_at)
    widened = rv.widened_changes(bt)
    if matched:
        names = ", ".join(f"{c.old_rule_id or c.field.value} ({c.field.value.replace('_', ' ')}, {c.change})" for c in matched)
        st.markdown(
            f'<div class="cl-callout strong">The sponsor later widened a criterion Clinova pointed at on the original protocol: {esc(names)}.</div>',
            unsafe_allow_html=True,
        )
    elif widened:
        st.markdown(
            '<div class="cl-callout">The sponsor later widened the criteria, but not one Clinova pointed at first. '
            "Shown as it is.</div>",
            unsafe_allow_html=True,
        )
    if bt.changes and not rv.has_dated_widening(bt):
        st.markdown(
            '<div class="cl-note">The dated version history is not loaded for this trial, so the criteria changes below compare the '
            "first registered text with the current text; the date of the change is not shown.</div>",
            unsafe_allow_html=True,
        )
    if bt.changes:
        st.markdown('<div class="cl-section">Criteria that changed, first to last version</div>', unsafe_allow_html=True)
        rows = ""
        matched_keys = {(c.field, c.old_rule_id, c.new_rule_id) for c in matched}
        first_rules = {r.rule_id: r for r in bt.run.protocol.rules} if bt.run else {}
        fix_names: dict[str, list[str]] = {}
        for fx in fixes or []:
            for e in fx.edits:
                if e.kind in ("relax_rule", "drop_rule"):
                    fix_names.setdefault(e.target_id, []).append(FIX_TITLES.get(fx.label, fx.label))
        notes_col = bool(matched) or any(c.old_rule_id in fix_names for c in bt.changes)
        for c in bt.changes:
            hit = (c.field, c.old_rule_id, c.new_rule_id) in matched_keys
            notes = []
            if hit:
                notes.append("Flagged by Clinova")
            if c.change in rv.RELAXING and c.old_rule_id in fix_names:
                notes.append("Also changed by the " + ", ".join(f'"{n}"' for n in dict.fromkeys(fix_names[c.old_rule_id])) + " fix")
            style = f" style='background:{theme.ACCENT_SOFT};font-weight:650'" if hit else ""
            old_rule = first_rules.get(c.old_rule_id or "")
            was = f"{c.old_rule_id}: {old_rule.raw_text}" if old_rule is not None else rv.condition_words(c.old)
            later = rv.condition_words(c.new) if c.new else ("no longer in the criteria" if c.change == "removed" else "")
            rows += (
                f"<tr{style}><td>{esc(c.field.value.replace('_', ' '))}</td><td>{esc(c.change)}</td>"
                f"<td>{esc(was[:220])}</td><td>{esc(later)}</td>"
                + (f"<td>{esc('; '.join(notes))}</td>" if notes_col else "")
                + "</tr>"
            )
        st.markdown(
            "<table class='cl-ba'><thead><tr><th>Criterion</th><th>Change</th><th>As first registered</th><th>Later</th>"
            + ("<th>Clinova</th>" if notes_col else "")
            + f"</tr></thead><tbody>{rows}</tbody></table>",
            unsafe_allow_html=True,
        )
    elif not bt.events:
        st.markdown(theme.empty("No eligibility changes found between the first and the current version."), unsafe_allow_html=True)
