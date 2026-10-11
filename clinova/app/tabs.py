"""The four tabs behind the results screen: Validation, Replay, Community Pulse, Audit log.

Every sentence is built from the numbers in the API response; nothing is typed in by hand."""

from __future__ import annotations

import streamlit as st

from clinova.app import charts, theme
from clinova.app.client import ApiClient, ApiError
from clinova.app.fmt import nice_date, pct
from clinova.app.theme import esc
from clinova.contracts import ReplayResult, TargetMetrics, ValidationMetrics

TARGET_NAMES = {"accrual_fail": "accrual failure", "shortfall": "enrollment shortfall", "amend": "eligibility amendment"}


def _error(what: str, exc: Exception) -> None:
    st.markdown(theme.empty(f"{what} could not be loaded.", str(exc)), unsafe_allow_html=True)


def validation_sentences(m: ValidationMetrics) -> list[str]:
    """One plain sentence per target, built only from the numbers in the metrics file."""
    out = []
    for t in m.targets:
        name = TARGET_NAMES.get(t.target.value, t.target.value)
        delta = t.auc.value - t.baseline_auc.value
        out.append(
            f"For {name}, on {t.n_test} test trials ({t.n_pos} positive), Clinova's AUC is {t.auc.value:.2f} "
            f"(95% interval {t.auc.ci_low:.2f} to {t.auc.ci_high:.2f}) against {t.baseline_auc.value:.2f} "
            f"for the baseline, a difference of {delta:+.2f}."
            + (
                f" Not validated: its Brier score ({t.brier:.3f}) is no better than the baseline's ({t.baseline_brier:.3f})."
                if t.brier >= t.baseline_brier
                else ""
            )
        )
    return out


def reliability_sentence(t: TargetMetrics) -> str:
    """Plain words for a reliability plot: the trial-weighted average gap between predicted and observed."""
    bins = [b for b in t.reliability if b.mean_pred is not None and b.frac_pos is not None and b.n > 0]
    if not bins:
        return "No reliability bins were computed for this target."
    n = sum(b.n for b in bins)
    gap = sum(abs(b.mean_pred - b.frac_pos) * b.n for b in bins) / n  # type: ignore[operator]
    return (
        f"Across {n} test trials in {len(bins)} bins, predicted and observed rates differ by "
        f"{gap * 100:.1f} percentage points on average (closer to the dashed line is better). "
        f"Brier score {t.brier:.3f} against {t.baseline_brier:.3f} for the baseline (lower is better)."
    )


def replay_sentence(r: ReplayResult, measure: str) -> str:
    """How often the retrained model beat the frozen one, counted from the monthly numbers."""
    pairs = [
        (getattr(m.retrained, measure), getattr(m.frozen, measure))
        for m in r.months
        if getattr(m.retrained, measure) is not None and getattr(m.frozen, measure) is not None
    ]
    if not pairs:
        return f"No month has a {measure.upper() if measure == 'auc' else 'Brier'} score for both models."
    better = sum(1 for a, b in pairs if (a < b if measure == "brier" else a > b))
    name = "Brier score" if measure == "brier" else "ROC AUC"
    return f"The retrained model had the better {name} in {better} of {len(pairs)} months where both could be scored."


def validation_tab(client: ApiClient) -> None:
    try:
        resp = client.validation()
    except ApiError as exc:
        _error("Validation results", exc)
        return
    m = resp.metrics
    st.markdown(
        f'<div class="cl-panel-title">Validation on held-out trials {theme.fixture_tag(resp.source)}</div>'
        f'<div class="cl-panel-sub">Trials that started before {esc(nice_date(m.split_date))} train the model; trials on or after that date test it. '
        f"{m.n_train:,} training and {m.n_test:,} test trials. Model {esc(m.model_version)}, computed {esc(nice_date(m.generated_at.date()))}.</div>",
        unsafe_allow_html=True,
    )
    if theme.is_fixture(resp.source):
        st.markdown(
            '<div class="cl-warn">These numbers are illustrative placeholders that ship with the app so the layout can be reviewed. '
            "They are not results.</div>",
            unsafe_allow_html=True,
        )
    if not m.targets:
        st.markdown(theme.empty("No targets were evaluated."), unsafe_allow_html=True)
        return
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown('<div class="cl-section">Discrimination: Clinova vs baseline</div>', unsafe_allow_html=True)
        st.plotly_chart(charts.auc_compare(m), width="stretch", key="val_auc", config={"displayModeBar": False})
        for s in validation_sentences(m):
            st.markdown(f'<div class="cl-note" style="margin-bottom:0.35rem">{esc(s)}</div>', unsafe_allow_html=True)
    with right:
        names = [t.target.value for t in m.targets]
        st.markdown('<div class="cl-section">Calibration: predicted vs observed</div>', unsafe_allow_html=True)
        pick = st.radio(
            "Target",
            names,
            format_func=lambda v: TARGET_NAMES.get(v, v).capitalize(),
            horizontal=True,
            key="val_rel_target",
            label_visibility="collapsed",
        )
        i = names.index(pick)
        st.plotly_chart(charts.reliability(m, i), width="stretch", key="val_rel", config={"displayModeBar": False})
        st.markdown(f'<div class="cl-note">{esc(reliability_sentence(m.targets[i]))}</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown('<div class="cl-section">Do the top flags point at the rule sponsors later widened?</div>', unsafe_allow_html=True)
        fig = charts.hit_rate(m)
        if fig is None:
            st.markdown(theme.empty("Hit rate has not been computed yet."), unsafe_allow_html=True)
        else:
            st.plotly_chart(fig, width="stretch", key="val_hit", config={"displayModeBar": False})
            h = m.hit_rate
            assert h is not None
            st.markdown(
                f'<div class="cl-note">Across {h.n_trials} test trials with a widened, parseable rule, '
                f"Clinova's top three flags hit it {pct(h.hit_rate)} of the time, against {pct(h.marginal_baseline)} for the three "
                f"largest-exclusion rules and {pct(h.random_baseline)} for three random rules.</div>",
                unsafe_allow_html=True,
            )
    with c2:
        st.markdown('<div class="cl-section">Does the simulation help?</div>', unsafe_allow_html=True)
        a = m.ablation
        if a is None:
            st.markdown(theme.empty("The simulation ablation has not been computed yet."), unsafe_allow_html=True)
        else:
            helps = "raises" if a.diff.ci_low > 0 else ("lowers" if a.diff.ci_high < 0 else "does not clearly change")
            st.markdown(
                f'<div class="cl-card"><div class="cl-big">{a.diff.value:+.3f}</div>'
                f'<div class="cl-range">AUC difference, with minus without simulation features '
                f"(95% interval {a.diff.ci_low:+.3f} to {a.diff.ci_high:+.3f})</div>"
                f'<div class="cl-note" style="margin-top:0.4rem">On {a.n_trials} simulated test trials, adding the simulation features '
                f"{helps} the AUC for {esc(TARGET_NAMES.get(a.target.value, a.target.value))}: "
                f"{a.auc_with:.2f} with simulation, {a.auc_without:.2f} without.</div></div>",
                unsafe_allow_html=True,
            )
        if m.notes:
            st.markdown('<div class="cl-section">Caveats</div>', unsafe_allow_html=True)
            for n in m.notes:
                st.markdown(f'<div class="cl-note" style="margin-bottom:0.3rem">- {esc(n)}</div>', unsafe_allow_html=True)
    with st.expander("All numbers"):
        st.dataframe(
            [
                {
                    "Target": TARGET_NAMES.get(t.target.value, t.target.value),
                    "Test trials": t.n_test,
                    "Positive": t.n_pos,
                    "Clinova AUC": f"{t.auc.value:.2f} ({t.auc.ci_low:.2f} to {t.auc.ci_high:.2f})",
                    "Baseline AUC": f"{t.baseline_auc.value:.2f} ({t.baseline_auc.ci_low:.2f} to {t.baseline_auc.ci_high:.2f})",
                    "Clinova Brier": f"{t.brier:.3f}",
                    "Baseline Brier": f"{t.baseline_brier:.3f}",
                }
                for t in m.targets
            ],
            hide_index=True,
            width="stretch",
        )


def replay_tab(client: ApiClient) -> None:
    try:
        resp = client.replay()
    except ApiError as exc:
        _error("The replay", exc)
        return
    r: ReplayResult = resp.replay
    if not r.months:
        st.markdown(theme.empty("The replay has no months yet."), unsafe_allow_html=True)
        return
    st.markdown(
        f'<div class="cl-panel-title">Time-machine replay {theme.fixture_tag(resp.source)}</div>'
        f'<div class="cl-panel-sub">Month by month from {esc(r.months[0].month)} to {esc(r.months[-1].month)}, scoring trials whose outcome arrives within '
        f"{r.horizon_months} months. One model is retrained every month as outcomes arrive; the other stays frozen at {esc(nice_date(r.freeze_date))}.</div>",
        unsafe_allow_html=True,
    )
    if theme.is_fixture(resp.source):
        st.markdown('<div class="cl-warn">Illustrative placeholder curves, not results.</div>', unsafe_allow_html=True)
    measure = st.radio(
        "Measure", ["brier", "auc"], format_func=lambda x: "Brier score" if x == "brier" else "ROC AUC", horizontal=True, key="replay_measure"
    )
    st.plotly_chart(charts.replay(r, measure), width="stretch", key="replay_chart", config={"displayModeBar": False})
    st.markdown(f'<div class="cl-callout">{esc(r.summary)} {esc(replay_sentence(r, measure))}</div>', unsafe_allow_html=True)
    with st.expander("Monthly table"):
        rows = [
            {
                "Month": m.month,
                "Scored": m.n_scored,
                "Positives": m.n_pos,
                "New outcomes": m.n_new_outcomes,
                "Retrained Brier": m.retrained.brier,
                "Frozen Brier": m.frozen.brier,
                "Retrained AUC": m.retrained.auc,
                "Frozen AUC": m.frozen.auc,
            }
            for m in r.months
        ]
        st.dataframe(rows, hide_index=True, width="stretch")


def pulse_tab(client: ApiClient, default_nct: str) -> None:
    st.markdown(
        '<div class="cl-panel-title">Community pulse</div>'
        '<div class="cl-panel-sub">A narrative of how a simulated community might perceive the burden of this trial. A discussion aid, not evidence.</div>',
        unsafe_allow_html=True,
    )
    c1, _ = st.columns([1, 2])
    nct = c1.text_input("Trial id", value=default_nct, key="pulse_nct")
    if not nct.strip():
        st.markdown(theme.empty("Enter a trial id to see its community pulse."), unsafe_allow_html=True)
        return
    try:
        resp = client.pulse(nct.strip())
    except ApiError as exc:
        _error("The community pulse", exc)
        return
    if resp.status == "available" and resp.report_md:
        with st.container(border=True):
            st.markdown(resp.report_md)
    else:
        st.markdown(
            theme.empty(
                f"Not run for {resp.nct_id}.",
                (resp.note or "The community pulse has not been run for this trial.")
                + " It is optional and does not feed the risk scores.",
            ),
            unsafe_allow_html=True,
        )


def audit_tab(client: ApiClient) -> None:
    st.markdown(
        '<div class="cl-panel-title">Audit log</div>'
        '<div class="cl-panel-sub">Every decision recorded about a suggestion, and every risk score Clinova has logged, newest first.</div>',
        unsafe_allow_html=True,
    )
    st.button("Refresh", key="audit_refresh")
    try:
        resp = client.audit()
    except ApiError as exc:
        _error("The audit log", exc)
        return
    if theme.is_fixture(resp.source):
        st.markdown(
            '<div class="cl-warn">Some entries are held in memory only because the ledger is not connected. They disappear when the API restarts.</div>',
            unsafe_allow_html=True,
        )
    st.markdown(f'<div class="cl-section">Decisions ({len(resp.feedback)})</div>', unsafe_allow_html=True)
    if resp.feedback:
        st.dataframe(
            [
                {
                    "When (UTC)": f.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Who": f.user_name,
                    "Decision": f.decision,
                    "About": f"{f.target_type} {f.target_id}",
                    "Trial": f.trial_id or "",
                    "Comment": f.comment or "",
                }
                for f in resp.feedback[:500]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.markdown(theme.empty("No decisions yet.", "Accept or reject a fix on the Protocol review tab."), unsafe_allow_html=True)
    st.markdown(f'<div class="cl-section">Logged predictions ({len(resp.predictions)})</div>', unsafe_allow_html=True)
    if resp.predictions:
        st.dataframe(
            [
                {
                    "When (UTC)": p.predicted_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Trial": p.trial_id,
                    "Target": TARGET_NAMES.get(p.target.value, p.target.value),
                    "Probability": pct(p.p),
                    "Range": f"{pct(p.ci_low)} to {pct(p.ci_high)}" if p.ci_low is not None and p.ci_high is not None else "",
                    "Context": p.context,
                    "Model": p.model_version,
                }
                for p in resp.predictions[:500]
            ],
            hide_index=True,
            width="stretch",
        )
        if len(resp.predictions) > 500:
            st.markdown(f'<div class="cl-note">Showing the newest 500 of {len(resp.predictions):,}.</div>', unsafe_allow_html=True)
    else:
        st.markdown(theme.empty("No predictions logged yet.", "Run a protocol first."), unsafe_allow_html=True)
