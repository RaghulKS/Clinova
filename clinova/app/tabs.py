"""The four tabs behind the results screen: Validation, Replay, Community Pulse, Audit log."""

from __future__ import annotations

import streamlit as st

from clinova.app import charts, theme
from clinova.app.client import ApiClient, ApiError
from clinova.app.theme import esc
from clinova.contracts import ReplayResult, ValidationMetrics

TARGET_NAMES = {"accrual_fail": "accrual failure", "shortfall": "enrollment shortfall", "amend": "eligibility amendment"}


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
        )
    return out


def validation_tab(client: ApiClient) -> None:
    try:
        resp = client.validation()
    except ApiError as exc:
        st.error(str(exc))
        return
    m = resp.metrics
    st.markdown(
        f'<div class="cl-panel-title">Validation on held-out trials {theme.fixture_tag(resp.source)}</div>'
        f'<div class="cl-panel-sub">Trials that started before {m.split_date} train the model; trials on or after that date test it. '
        f"{m.n_train} training and {m.n_test} test trials. Model version {esc(m.model_version)}.</div>",
        unsafe_allow_html=True,
    )
    if resp.source.value == "fixture":
        st.markdown(
            '<div class="cl-warn">These numbers are illustrative placeholders that ship with the app so the layout can be reviewed. '
            "They are not results.</div>",
            unsafe_allow_html=True,
        )
    left, right = st.columns(2)
    with left:
        st.markdown('<div class="cl-section">Discrimination: Clinova vs baseline</div>', unsafe_allow_html=True)
        st.plotly_chart(charts.auc_compare(m), width="stretch", key="val_auc", config={"displayModeBar": False})
        for s in validation_sentences(m):
            st.markdown(f'<div class="cl-note" style="margin-bottom:0.35rem">{esc(s)}</div>', unsafe_allow_html=True)
        rows = [
            {
                "Target": TARGET_NAMES.get(t.target.value, t.target.value),
                "Clinova AUC": f"{t.auc.value:.2f} ({t.auc.ci_low:.2f} to {t.auc.ci_high:.2f})",
                "Baseline AUC": f"{t.baseline_auc.value:.2f} ({t.baseline_auc.ci_low:.2f} to {t.baseline_auc.ci_high:.2f})",
                "Clinova Brier": f"{t.brier:.3f}",
                "Baseline Brier": f"{t.baseline_brier:.3f}",
            }
            for t in m.targets
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
    with right:
        st.markdown('<div class="cl-section">Calibration: predicted vs observed (accrual failure)</div>', unsafe_allow_html=True)
        if m.targets:
            st.plotly_chart(charts.reliability(m, 0), width="stretch", key="val_rel", config={"displayModeBar": False})
        st.markdown(
            '<div class="cl-note">Points on the dashed line mean that when Clinova says 30%, about 30% of such trials had the problem.</div>',
            unsafe_allow_html=True,
        )
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="cl-section">Do the top flags point at the rule sponsors later widened?</div>', unsafe_allow_html=True)
        fig = charts.hit_rate(m)
        if fig is None:
            st.info("Hit rate has not been computed yet.")
        else:
            st.plotly_chart(fig, width="stretch", key="val_hit", config={"displayModeBar": False})
            h = m.hit_rate
            assert h is not None
            st.markdown(
                f'<div class="cl-note">Across {h.n_trials} test trials with a widened, parseable rule, '
                f"Clinova's top three flags hit it {h.hit_rate:.0%} of the time, against {h.marginal_baseline:.0%} for the three "
                f"largest-exclusion rules and {h.random_baseline:.0%} for three random rules.</div>",
                unsafe_allow_html=True,
            )
    with c2:
        st.markdown('<div class="cl-section">Does the simulation help?</div>', unsafe_allow_html=True)
        a = m.ablation
        if a is None:
            st.info("The simulation ablation has not been computed yet.")
        else:
            st.markdown(
                f'<div class="cl-card"><div class="cl-big">{a.diff.value:+.3f}</div>'
                f'<div class="cl-range">AUC difference, with minus without simulation features '
                f"(95% interval {a.diff.ci_low:+.3f} to {a.diff.ci_high:+.3f})</div>"
                f'<div class="cl-note" style="margin-top:0.4rem">{a.n_trials} simulated test trials: '
                f"{a.auc_with:.2f} with simulation, {a.auc_without:.2f} without.</div></div>",
                unsafe_allow_html=True,
            )
        if m.notes:
            st.markdown('<div class="cl-section">Caveats</div>', unsafe_allow_html=True)
            for n in m.notes:
                st.markdown(f'<div class="cl-note" style="margin-bottom:0.3rem">- {esc(n)}</div>', unsafe_allow_html=True)


def replay_tab(client: ApiClient) -> None:
    try:
        resp = client.replay()
    except ApiError as exc:
        st.error(str(exc))
        return
    r: ReplayResult = resp.replay
    st.markdown(
        f'<div class="cl-panel-title">Time-machine replay {theme.fixture_tag(resp.source)}</div>'
        f'<div class="cl-panel-sub">Month by month from {esc(r.months[0].month)} to {esc(r.months[-1].month)}, scoring trials whose outcome arrives within '
        f"{r.horizon_months} months. One model is retrained every month as outcomes arrive; the other stays frozen at {r.freeze_date}.</div>",
        unsafe_allow_html=True,
    )
    if resp.source.value == "fixture":
        st.markdown('<div class="cl-warn">Illustrative placeholder curves, not results.</div>', unsafe_allow_html=True)
    measure = st.radio("Measure", ["brier", "auc"], format_func=lambda x: "Brier score" if x == "brier" else "ROC AUC", horizontal=True, key="replay_measure")
    st.plotly_chart(charts.replay(r, measure), width="stretch", key="replay_chart", config={"displayModeBar": False})
    st.markdown(f'<div class="cl-callout">{esc(r.summary)}</div>', unsafe_allow_html=True)
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
        '<div class="cl-panel-sub">A narrative of how a simulated community might perceive the burden of this trial. It is a discussion aid, not evidence.</div>',
        unsafe_allow_html=True,
    )
    nct = st.text_input("Trial id", value=default_nct, key="pulse_nct")
    if not nct.strip():
        return
    try:
        resp = client.pulse(nct.strip())
    except ApiError as exc:
        st.error(str(exc))
        return
    if resp.status == "available" and resp.report_md:
        st.markdown(resp.report_md)
    else:
        st.markdown(
            '<div class="cl-card"><h4>Not run</h4>'
            f'<div class="cl-note">{esc(resp.note or "The community pulse has not been run for this trial.")}</div></div>',
            unsafe_allow_html=True,
        )


def audit_tab(client: ApiClient) -> None:
    st.markdown(
        '<div class="cl-panel-title">Audit log</div>'
        '<div class="cl-panel-sub">Every decision recorded about a suggestion, and every risk score Clinova has logged.</div>',
        unsafe_allow_html=True,
    )
    st.button("Refresh", key="audit_refresh")
    try:
        resp = client.audit()
    except ApiError as exc:
        st.error(str(exc))
        return
    if resp.source.value == "fixture":
        st.markdown(
            '<div class="cl-note">Some entries are held in memory only because the ledger is not connected yet. They disappear when the API restarts.</div>',
            unsafe_allow_html=True,
        )
    st.markdown('<div class="cl-section">Decisions</div>', unsafe_allow_html=True)
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
                for f in resp.feedback
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.markdown('<div class="cl-note">No decisions yet. Accept or reject a fix on the Protocol review tab.</div>', unsafe_allow_html=True)
    st.markdown('<div class="cl-section">Logged predictions</div>', unsafe_allow_html=True)
    if resp.predictions:
        st.dataframe(
            [
                {
                    "When (UTC)": p.predicted_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Trial": p.trial_id,
                    "Target": p.target.value,
                    "p": round(p.p, 3),
                    "Range": f"{p.ci_low:.2f} to {p.ci_high:.2f}" if p.ci_low is not None and p.ci_high is not None else "",
                    "Context": p.context,
                    "Model": p.model_version,
                }
                for p in resp.predictions
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.markdown('<div class="cl-note">No predictions logged yet. Run a protocol first.</div>', unsafe_allow_html=True)
