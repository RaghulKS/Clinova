"""The three results panels: Funnel, Risk, Fixes. Wording frames everything as a suggestion to the author."""

from __future__ import annotations

import streamlit as st

from clinova.api.schemas import AcceptResponse, FeedbackIn
from clinova.app import charts, theme
from clinova.app.client import ApiClient, ApiError
from clinova.app.rules_table import rule_in_words
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

TARGET_TITLES = {
    "accrual_fail": ("Accrual failure", "Chance the trial stops early for slow enrollment"),
    "shortfall": ("Enrollment shortfall", "Chance it enrolls well under its target"),
    "amend": ("Eligibility amendment", "Chance the sponsor later has to change the criteria"),
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


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def signed_pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def signed_pts(x: float) -> str:
    return f"{x * 100:+.0f} pts"


def money(x: float) -> str:
    return f"${x:,.0f}"


def edit_in_words(edit: Edit, protocol: ParsedProtocol) -> str:
    """Plain words for one edit, e.g. 'Relax I12 (creatinine clearance >= 60 mL/min) to 45'."""
    rules = {r.rule_id: r for r in protocol.rules}
    visits = {str(v.visit_no): v for v in protocol.visits}
    if edit.kind in ("relax_rule", "drop_rule"):
        rule = rules.get(edit.target_id)
        what = f"{edit.target_id} ({rule_in_words(rule)})" if rule else edit.target_id
        if edit.kind == "drop_rule":
            return f"Drop {what}"
        to = f"{edit.new_operator.value} {edit.new_value}" if edit.new_operator else f"{edit.new_value}"
        return f"Relax {what} to {to}"
    visit = visits.get(edit.target_id)
    name = f"visit {edit.target_id} ({visit.name})" if visit else f"visit {edit.target_id}"
    return {
        "cut_visit": f"Remove {name}",
        "merge_visits": f"Merge {name} with the next visit",
        "make_remote": f"Allow {name} to be done remotely",
    }[edit.kind]


# --------------------------------------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------------------------------------


def funnel_panel(status: RunStatus) -> None:
    res = status.result
    assert res is not None
    st.markdown(
        f'<div class="cl-panel-title">Funnel {theme.fixture_tag(status.source)}</div>'
        '<div class="cl-panel-sub">Virtual patients screened against your criteria, and who would be lost.</div>',
        unsafe_allow_html=True,
    )
    f = res.funnel
    if f is None:
        st.info("No funnel for this run.")
        return
    fig, steps = charts.funnel_steps(f, res.sim)
    st.plotly_chart(fig, width="stretch", key="funnel_steps", config={"displayModeBar": False})
    top = f.marginal[0] if f.marginal else None
    if top is not None:
        st.markdown(
            f'<div class="cl-callout"><b>Biggest exclusion: {esc(top.rule_id)}, {esc(top.label.rsplit(" (", 1)[0])}.</b> '
            f"It rules out {top.excluded_n} of {f.n_screened} virtual patients ({pct(top.excluded_frac)}); "
            f"{top.excluded_only_n} of them fail only this criterion. "
            "You may want to check whether it is needed as written.</div>",
            unsafe_allow_html=True,
        )
    st.markdown('<div class="cl-section">Patients failing each criterion</div>', unsafe_allow_html=True)
    st.plotly_chart(charts.exclusions(f), width="stretch", key="funnel_excl", config={"displayModeBar": False})
    if f.skipped_rules:
        st.markdown(
            f'<div class="cl-note">Not applied (could not be parsed): {esc(", ".join(f.skipped_rules))}</div>',
            unsafe_allow_html=True,
        )
    if res.sim:
        sim = res.sim[0]
        st.markdown('<div class="cl-section">Dropout after enrolling</div>', unsafe_allow_html=True)
        st.plotly_chart(charts.dropout_curve(sim), width="stretch", key="funnel_dropout", config={"displayModeBar": False})
        reasons = ", ".join(f"{r.tag.value.replace('_', ' ')} ({pct(r.share)})" for r in sim.top_reasons[:3])
        if reasons:
            st.markdown(f'<div class="cl-note">Most cited burdens: {esc(reasons)}.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="cl-note">Enrolled and completing are the simulated rates applied to all eligible virtual patients, '
        "an estimate rather than a count.</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Funnel in protocol order"):
        rows = [
            {"Step": s.label, "Remaining": s.remaining, "Dropped here": s.excluded} for s in f.stages
        ]
        st.dataframe(rows, hide_index=True, width="stretch")


# --------------------------------------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------------------------------------


def _risk_card(r: RiskScore) -> str:
    title, sub = TARGET_TITLES.get(r.target.value, (r.target.value, ""))
    drivers = "".join(
        f'<div class="cl-driver">{esc(d.label)} <span class="dir">({"raises" if d.contribution > 0 else "lowers"} the estimate)</span></div>'
        for d in r.top_drivers[:4]
    )
    return (
        f'<div class="cl-card accent"><h4>{esc(title)}</h4>'
        f'<div class="cl-note" style="margin-bottom:0.4rem">{esc(sub)}</div>'
        f'<div class="cl-big">{pct(r.p)}</div>'
        f'<div class="cl-range">likely range {pct(r.ci_low)} to {pct(r.ci_high)}</div>'
        f'<div style="margin:0.45rem 0 0.5rem"><span class="cl-badge {esc(r.confidence)}">{esc(CONFIDENCE_TEXT[r.confidence])}</span></div>'
        f'<div class="cl-note" style="margin-bottom:0.15rem">What drives this estimate</div>{drivers}</div>'
    )


def _flag_html(f: CriterionFlag) -> str:
    return (
        f'<div class="cl-flag"><div class="where">{f.rank}. {esc(f.where)}</div>'
        f'<div class="lbl">Why it matters</div><div class="txt">{esc(f.why)}</div>'
        f'<div class="lbl">What you could do</div><div class="txt">{esc(f.what_to_do)}</div>'
        f'<div class="cl-note" style="margin-top:0.25rem">Estimated to lower accrual-failure risk by {abs(f.delta_p) * 100:.0f} points.</div></div>'
    )


def risk_panel(status: RunStatus) -> None:
    res = status.result
    assert res is not None
    st.markdown(
        f'<div class="cl-panel-title">Risk {theme.fixture_tag(status.source)}</div>'
        '<div class="cl-panel-sub">Calibrated probabilities with a range. These are estimates to inform your judgement, not a verdict.</div>',
        unsafe_allow_html=True,
    )
    if not res.risks:
        st.info("No risk scores for this run.")
    for r in res.risks:
        st.markdown(_risk_card(r), unsafe_allow_html=True)
    if res.flags:
        st.markdown('<div class="cl-section">Where to look first</div>', unsafe_allow_html=True)
        for flag in res.flags[:3]:
            st.markdown(_flag_html(flag), unsafe_allow_html=True)


# --------------------------------------------------------------------------------------------------------
# Fixes
# --------------------------------------------------------------------------------------------------------


def _rng(lo: float, hi: float, fmt) -> str:
    return f'<span class="r">range {fmt(lo)} to {fmt(hi)}</span>'


def _metric(label: str, value: str, rng: str = "") -> str:
    return f'<div class="cl-metric"><span>{esc(label)}</span><span class="v">{value}{rng}</span></div>'


def _fix_card(fix: Fix, protocol: ParsedProtocol, decision: str | None) -> str:
    rg = fix.ranges
    edits = "".join(f'<div class="cl-driver">{esc(edit_in_words(e, protocol))}</div>' for e in fix.edits)
    prec = "".join(
        f'<div class="cl-precedent">{esc(p.nct_id)}: {esc(p.detail)}</div>' for p in fix.precedents[:2]
    )
    badge = ""
    if decision:
        badge = f' <span class="cl-badge {"high" if decision == "accept" else "low"}">{"Accepted" if decision == "accept" else "Rejected"}</span>'
    dollars = (
        _metric("Expected value of avoided amendments", money(fix.dollar_value))
        if fix.dollar_value > 0
        else _metric("Expected value of avoided amendments", "none estimated")
    )
    return (
        f'<div class="cl-card accent"><h4>{esc(FIX_TITLES.get(fix.label, fix.label))}{badge}</h4>'
        f'<div style="font-size:0.9rem;margin-bottom:0.45rem">{esc(fix.summary)}</div>'
        f'<div class="cl-note" style="margin-bottom:0.15rem">Suggested edits</div>{edits}'
        '<div style="height:0.4rem"></div>'
        + _metric("Enrollment speed", signed_pct(fix.delta_enroll), _rng(rg.delta_enroll.low, rg.delta_enroll.high, signed_pct))
        + _metric("Dropout (share of enrolled)", signed_pts(fix.delta_dropout), _rng(rg.delta_dropout.low, rg.delta_dropout.high, signed_pts))
        + _metric("Diversity index", f"{fix.delta_diversity:+.2f}", _rng(rg.delta_diversity.low, rg.delta_diversity.high, lambda v: f"{v:+.2f}"))
        + _metric(
            "Amendment risk",
            signed_pts(fix.delta_p_amend),
            _rng(rg.delta_p_amend.low, rg.delta_p_amend.high, signed_pts) if rg.delta_p_amend else "",
        )
        + dollars
        + (f'<div class="cl-note" style="margin:0.4rem 0 0.1rem">Comparable trials</div>{prec}' if prec else "")
        + "</div>"
    )


def _decide(client: ApiClient, fix: Fix, decision: str, status: RunStatus) -> None:
    """Accept goes to /fixes/accept (logs the decision, re-runs fast, returns before and after);
    reject only logs the decision."""
    trial_id = status.result.protocol.trial_id if status.result else status.trial_id
    try:
        if decision == "accept":
            resp = client.accept_fix(status.run_id, fix.fix_id)
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
    return f"{v:.2f}" if kind == "index" else pct(v, 1)


def before_after_rows(resp: AcceptResponse) -> list[dict]:
    """Rows of the before and after table. The Basis column says whether the after number is a re-run
    or the optimizer's estimate."""
    basis = "re-run (surrogate, no new LLM calls)" if resp.basis == "quick_rerun" else "optimizer estimate"
    b, a = resp.before, resp.after
    spec = [
        ("Eligible virtual patients", b.eligible_frac, a.eligible_frac, "pct"),
        ("Enrollment rate of eligible", b.enroll_rate, a.enroll_rate, "pct"),
        ("Dropout (share of enrolled)", b.dropout_rate, a.dropout_rate, "pct"),
        ("Diversity index", b.diversity_index, a.diversity_index, "index"),
        ("Accrual-failure risk", b.accrual_fail_p, a.accrual_fail_p, "pct"),
        ("Enrollment-shortfall risk", b.shortfall_p, a.shortfall_p, "pct"),
        ("Amendment risk", b.amend_p, a.amend_p, "pct"),
    ]
    return [
        {"Measure": label, "Before": _fmt(bv, k), "After": _fmt(av, k), "Basis": basis}
        for label, bv, av, k in spec
        if bv is not None or av is not None
    ]


def fixes_panel(status: RunStatus, client: ApiClient) -> None:
    res: RunResult | None = status.result
    assert res is not None
    st.markdown(
        f'<div class="cl-panel-title">Fixes {theme.fixture_tag(status.source)}</div>'
        '<div class="cl-panel-sub">Three suggested trade-offs for you to consider. Accepting one records your decision and re-checks the protocol.</div>',
        unsafe_allow_html=True,
    )
    if not res.fixes:
        st.info("No fixes were suggested for this run.")
        return
    decisions: dict[str, str] = st.session_state.get("decisions", {})
    for fix in res.fixes:
        d = decisions.get(fix.fix_id)
        st.markdown(_fix_card(fix, res.protocol, d), unsafe_allow_html=True)
        c1, c2, _ = st.columns([1, 1, 2])
        c1.button("Accept", key=f"acc_{fix.fix_id}", type="primary", on_click=_decide, args=(client, fix, "accept", status), disabled=d is not None)
        c2.button("Reject", key=f"rej_{fix.fix_id}", on_click=_decide, args=(client, fix, "reject", status), disabled=d is not None)
    if err := st.session_state.pop("fix_error", None):
        st.error(err)
    accepts: dict = st.session_state.get("accepts", {})
    accepted = [f for f in res.fixes if f.fix_id in accepts]
    if accepted:
        fix = accepted[-1]
        resp = AcceptResponse.model_validate(accepts[fix.fix_id])
        st.markdown('<div class="cl-section">Before and after</div>', unsafe_allow_html=True)
        st.dataframe(before_after_rows(resp), hide_index=True, width="stretch")
        if resp.note:
            st.markdown(f'<div class="cl-note">{esc(resp.note)}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="cl-note">Your decision was recorded ({"database" if resp.feedback_source.value == "live" else "kept in memory only"}). '
            "Clinova suggests; the final protocol is yours to decide.</div>",
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------------------------------------
# Backtest reveal
# --------------------------------------------------------------------------------------------------------


def reveal(payload: dict) -> None:
    """Version-history reveal for a backtest: what the sponsor changed later, and what Clinova flagged."""
    bt = BacktestResult.model_validate(payload)
    t = bt.trial
    outcome = f"{t.status.value.replace('_', ' ').title()}" + (f": {t.why_stopped}" if t.why_stopped else "")
    st.markdown(
        f'<div class="cl-card"><h4>{esc(t.nct_id)}: {esc(t.title)} {theme.fixture_tag(bt.source)}</h4>'
        f'<div class="cl-note">Registered {esc(t.first_version_date)}. Outcome: {esc(outcome)}. '
        f"Planned enrollment {esc(t.planned_enrollment)}, actual {esc(t.actual_enrollment)}.</div></div>",
        unsafe_allow_html=True,
    )
    changed_fields = {c.field.value for c in bt.changes}
    flagged = [f for f in (bt.run.flags if bt.run else []) if f.field.value in changed_fields]
    if flagged:
        names = ", ".join(f"{f.rule_id} ({f.field.value.replace('_', ' ')})" for f in flagged)
        st.markdown(
            f'<div class="cl-callout">Clinova had flagged {esc(names)} on the original protocol. '
            "The sponsor later changed that same criterion, after the trial had already struggled.</div>",
            unsafe_allow_html=True,
        )
    left, right = st.columns([1.3, 1], gap="large")
    with left:
        events = ""
        for e in bt.events:
            key = "key" if e.field == "eligibility" else ""
            old = (e.old_value or "")[:140]
            new = (e.new_value or "")[:140]
            cls = f" ({e.change_class})" if e.change_class else ""
            events += (
                f'<div class="ev {key}"><div class="date">{esc(e.version_date)}, version {e.version_no}</div>'
                f"<b>{esc(e.field.replace('_', ' '))}{esc(cls)}</b><br>"
                f'<span class="cl-note">{esc(old)}</span><br>to {esc(new)}</div>'
            )
        st.markdown(f'<div class="cl-timeline">{events}</div>', unsafe_allow_html=True)
    with right:
        if bt.changes:
            st.markdown('<div class="cl-section">Criteria that changed, first to last version</div>', unsafe_allow_html=True)
            st.dataframe(
                [{"Field": c.field.value, "Change": c.change, "Was": c.old or "", "Became": c.new or ""} for c in bt.changes],
                hide_index=True,
                width="stretch",
            )
