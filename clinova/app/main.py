"""Clinova Streamlit app (owner: T15, then T25 and T32). Talks to the API only through ``client.py``.

Run: ``python -m clinova.api`` in one terminal and ``python -m clinova.app`` in another.
"""

from __future__ import annotations

import streamlit as st

from clinova.app import fmt, panels, tabs, theme
from clinova.app.client import ApiClient, ApiError
from clinova.app.rules_table import FIELD_CHOICES, OPERATOR_CHOICES, df_to_rules, rules_to_df
from clinova.app.theme import esc
from clinova.common.config import get_settings
from clinova.common.fixtures import FIXTURES_DIR
from clinova.contracts import ParsedProtocol, RunStatus

STAGE_LABELS = {
    "queued": "Queued",
    "parse": "Reading the criteria",
    "cohort": "Building 1,000 virtual patients",
    "screen": "Screening virtual patients against each criterion",
    "agents": "Simulating what eligible patients decide",
    "score": "Scoring risk",
    "flags": "Finding the criteria that matter most",
    "optimize": "Searching for suggested fixes",
    "done": "Done",
    "failed": "Failed",
}

st.set_page_config(page_title="Clinova", page_icon=":material/biotech:", layout="wide", initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)


@st.cache_resource
def get_client() -> ApiClient:
    return ApiClient()


client = get_client()
ss = st.session_state
DEMO_NCT = get_settings().DEMO_NCT_ID or ""


def reset_results() -> None:
    for k in ("status", "decisions", "accepts", "backtest", "accept_secs"):
        ss.pop(k, None)


def h2(n: int | str, title: str, sub: str = "", anchor: str = "") -> None:
    a = f' id="{anchor}"' if anchor else ""
    st.markdown(
        f'<div class="cl-h2"{a}><span class="n">{esc(n)}</span>{esc(title)}</div>'
        + (f'<div class="cl-h2-sub">{esc(sub)}</div>' if sub else ""),
        unsafe_allow_html=True,
    )


def trial_bar(protocol: ParsedProtocol, meta: dict, source: object) -> None:
    """Title, id and the 'as originally registered on <date>' chip (NCT) or 'pasted draft' chip."""
    if meta.get("nct_id") and meta.get("registered_on"):
        chip = f'<span class="cl-asof">Protocol as originally registered on {esc(fmt.nice_date(meta["registered_on"]))}</span>'
    elif meta.get("nct_id"):
        chip = '<span class="cl-asof">Protocol as originally registered</span>'
    else:
        chip = '<span class="cl-asof">Pasted draft</span>'
    st.markdown(
        f'<div class="cl-trial"><div><span class="t">{esc(protocol.title or protocol.trial_id)}</span>'
        f'<span class="id">{esc(protocol.trial_id)}</span> {theme.fixture_tag(source)}</div>{chip}</div>',
        unsafe_allow_html=True,
    )


def base_rates() -> dict[str, float]:
    """Positive rate per target among held-out test trials, only from real (non-fixture) metrics."""
    if "base_rates" not in ss:
        try:
            v = client.validation()
            ss["base_rates"] = (
                {} if theme.is_fixture(v.source) else {t.target.value: t.n_pos / t.n_test for t in v.metrics.targets if t.n_test}
            )
        except ApiError:
            ss["base_rates"] = {}
    return ss["base_rates"]


# --------------------------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------------------------

st.markdown(
    '<div class="cl-top"><span class="cl-logo">Clin<span>ova</span></span>'
    '<span class="cl-sub">Test-fly a lung cancer trial protocol on virtual patients before enrolling real ones.</span></div>',
    unsafe_allow_html=True,
)
try:
    health = client.health()
except ApiError as exc:
    st.markdown(
        theme.empty("The Clinova API is not reachable.", f"{exc} Start it with: python -m clinova.api, then reload this page."),
        unsafe_allow_html=True,
    )
    st.markdown(theme.FOOTER, unsafe_allow_html=True)
    st.stop()

tab_main, tab_val, tab_replay, tab_pulse, tab_audit = st.tabs(
    ["Protocol review", "Validation", "Replay", "Community Pulse", "Audit log"]
)

# --------------------------------------------------------------------------------------------------------
# Protocol review
# --------------------------------------------------------------------------------------------------------

with tab_main:
    has_protocol = "protocol" in ss
    has_results = "status" in ss
    with st.expander("1. Your protocol", expanded=not has_protocol):
        mode = st.radio(
            "Start from",
            ["Load a registered trial", "Paste a protocol"],
            horizontal=True,
            label_visibility="collapsed",
            key="input_mode",
        )
        if mode == "Paste a protocol":
            if st.button("Use the example protocol", key="example"):
                ss["elig"] = (FIXTURES_DIR / "protocol_2L_eligibility.txt").read_text(encoding="utf-8")
                ss["sched"] = (FIXTURES_DIR / "protocol_2L_schedule.txt").read_text(encoding="utf-8")
            c1, c2 = st.columns(2)
            elig = c1.text_area("Eligibility criteria", key="elig", height=210, placeholder="Paste inclusion and exclusion criteria...")
            sched = c2.text_area("Schedule of visits (optional)", key="sched", height=210, placeholder="Paste the schedule of assessments...")
            go_parse = st.button("Parse protocol", type="primary", key="parse_text", disabled=not elig.strip())
            parse_args = {"eligibility_text": elig, "schedule_text": sched or None}
        else:
            c1, _ = st.columns([1, 2])
            nct = c1.text_input("NCT ID", key="nct", placeholder=DEMO_NCT or "NCT01234567")
            st.caption("Clinova loads the protocol as it was first registered, before any amendment, and later reveals what happened.")
            go_parse = st.button("Load protocol", type="primary", key="parse_nct", disabled=not nct.strip())
            parse_args = {"nct_id": nct.strip().upper()}

    if go_parse:
        reset_results()
        try:
            with st.spinner("Reading the protocol..."):
                resp = client.parse(**parse_args)
            ss["protocol"] = resp.protocol.model_dump(mode="json")
            ss["parse_meta"] = {
                "source": resp.source.value,
                "note": resp.note,
                "nct_id": parse_args.get("nct_id"),
                "registered_on": str(resp.registered_on) if resp.registered_on else None,
            }
            ss["rules_df"] = rules_to_df(resp.protocol.rules)
            ss["rules_version"] = ss.get("rules_version", 0) + 1
            st.rerun()  # collapse the input box now that a protocol is loaded
        except ApiError as exc:
            st.error(f"Could not load the protocol. {exc}")

    protocol: ParsedProtocol | None = ParsedProtocol.model_validate(ss["protocol"]) if "protocol" in ss else None
    meta = ss.get("parse_meta", {})

    if protocol is None:
        st.markdown(
            theme.empty(
                "No protocol loaded yet.",
                "Enter a registered NCT ID"
                + (f" (for example the demo trial {DEMO_NCT})" if DEMO_NCT else "")
                + ", or paste eligibility criteria and a visit schedule above.",
            ),
            unsafe_allow_html=True,
        )
    else:
        trial_bar(protocol, meta, meta.get("source"))
        with st.expander("2. Review the parsed criteria", expanded=not has_results):
            if meta.get("note") and meta.get("source") == "fixture":
                st.markdown(f'<div class="cl-warn">{esc(meta["note"])}</div>', unsafe_allow_html=True)
            n_bad = len(protocol.unparseable)
            st.markdown(
                f'<div class="cl-note" style="margin:0.3rem 0">{len(protocol.rules)} criteria. Edit any value, or lock a criterion so no fix touches it. '
                + (
                    f"{n_bad} could not be parsed and are highlighted: Clinova does not guess, and skips them when screening."
                    if n_bad
                    else "Every criterion was parsed."
                )
                + "</div>",
                unsafe_allow_html=True,
            )
            base_df = ss["rules_df"]

            def _highlight(row):
                bad = row["Status"] != "Parsed"
                return [f"background-color: {theme.AMBER_BG}; color: {theme.INK}" if bad else "" for _ in row]

            edited = st.data_editor(
                base_df.style.apply(_highlight, axis=1),
                key=f"rules_editor_{ss.get('rules_version', 0)}",
                hide_index=True,
                width="stretch",
                height=min(60 + 36 * len(base_df), 760),
                disabled=["ID", "Kind", "Source text", "Status"],
                column_config={
                    "Lock": st.column_config.CheckboxColumn("Lock", help="Locked criteria are never changed by a suggested fix.", width="small"),
                    "ID": st.column_config.TextColumn("ID", width="small"),
                    "Kind": st.column_config.TextColumn("Kind", width="small"),
                    "Field": st.column_config.SelectboxColumn("Field", options=FIELD_CHOICES, width="medium"),
                    "Operator": st.column_config.SelectboxColumn("Operator", options=OPERATOR_CHOICES, width="small"),
                    "Value": st.column_config.TextColumn("Value", width="small", help="For 'in' lists, separate values with commas."),
                    "Unit": st.column_config.TextColumn("Unit", width="small"),
                    "Source text": st.column_config.TextColumn("Source text", width="large"),
                    "Status": st.column_config.TextColumn("Status", width="small"),
                },
            )
            new_rules, problems = df_to_rules(edited, protocol.rules)
            for p in problems:
                st.warning(p)
            reviewed = ParsedProtocol.model_validate({**protocol.model_dump(mode="json"), "rules": [r.model_dump(mode="json") for r in new_rules]})
        with st.expander(f"Visit schedule ({len(protocol.visits)} visits)"):
            if not protocol.visits:
                st.markdown(theme.empty("No visit schedule.", "Paste a schedule of assessments to simulate visit burden."), unsafe_allow_html=True)
            else:
                if any(v.estimated for v in protocol.visits):
                    st.caption("Some visits are estimated from the trial design, not read from a schedule.")
                st.dataframe(
                    [
                        {
                            "Visit": v.visit_no,
                            "Name": v.name,
                            "Day": v.day,
                            "Procedures": ", ".join(v.procedures),
                            "Hours on site": v.est_hours,
                            "Travel": "yes" if v.requires_travel else "no",
                            "Invasive": "yes" if v.invasive else "no",
                            "Could be remote": "yes" if v.remote_ok else "no",
                            "Estimated": "yes" if v.estimated else "no",
                        }
                        for v in protocol.visits
                    ],
                    hide_index=True,
                    width="stretch",
                )

        run_col, hint_col = st.columns([1, 5])
        go_run = run_col.button("Run Clinova", type="primary", key="run", width="stretch")
        if not has_results:
            hint_col.markdown(
                '<div class="cl-note" style="padding-top:0.55rem">Screens 1,000 virtual patients, simulates their decisions, '
                "scores the risk and searches for fixes. A first run on a new protocol can take a minute.</div>",
                unsafe_allow_html=True,
            )
        if go_run:
            reset_results()
            try:
                created = client.start_run(reviewed, nct_id=None)
                bar = st.progress(0.0, text="Starting...")

                def on_update(s: RunStatus) -> None:
                    bar.progress(s.progress, text=STAGE_LABELS.get(s.stage, s.stage))

                final = client.wait_for_run(created.run_id, on_update=on_update)
                bar.empty()
                if final.stage == "failed":
                    st.error(f"The run did not finish: {final.message or 'unknown error'}. Try again, or check the API log.")
                else:
                    ss["status"] = final.model_dump(mode="json")
                    st.rerun()  # collapse the review box so results sit high on the page
            except ApiError as exc:
                st.error(f"The run could not start. {exc}")

    status = RunStatus.model_validate(ss["status"]) if "status" in ss else None
    if status is not None and status.result is not None:
        res = status.result
        if theme.is_fixture(status.source) and status.message:
            st.markdown(f'<div class="cl-warn">{esc(status.message)} Those parts show placeholder data, not results.</div>', unsafe_allow_html=True)
        h2(1, "Where enrollment stalls, and how likely trouble is", anchor="cl-results")
        col_f, col_r = st.columns([1.45, 1], gap="large")
        with col_f:
            panels.funnel_panel(status)
        with col_r:
            panels.risk_panel(status, base_rates())

        h2(2, "Where to look first", "The criteria whose change lowers the predicted risk most: where, why, what you could do, and what it is worth.", anchor="cl-flags")
        panels.flags_row(status)

        nct = meta.get("nct_id")
        step = 3
        if nct:
            h2(step, "What happened next", "The real trial's history, revealed after the prediction. Clinova only read the first registered version.", anchor="cl-reveal")
            step += 1
            if "backtest" not in ss:
                try:
                    with st.spinner("Loading the trial's registered history..."):
                        ss["backtest"] = client.backtest(nct).model_dump(mode="json")
                except ApiError as exc:
                    ss["backtest"] = {"error": str(exc)}
            bt = ss["backtest"]
            if "error" in bt:
                st.markdown(theme.empty("The trial history could not be loaded.", bt["error"]), unsafe_allow_html=True)
            else:
                pointed = [r for r in [panels.stall_rule(res)[0], *[f.rule_id for f in res.flags[:3]]] if r]
                panels.reveal(bt, pointed)

        h2(step, "Suggested fixes", "Three trade-offs for you to weigh. Accepting one records your decision and re-checks the protocol in seconds.", anchor="cl-fixes")
        panels.fixes_row(status, client)

        with st.expander("More funnel detail: each criterion on its own, and dropout after enrolling"):
            panels.funnel_details(status)

with tab_val:
    tabs.validation_tab(client)
with tab_replay:
    tabs.replay_tab(client)
with tab_pulse:
    tabs.pulse_tab(client, meta.get("nct_id") or DEMO_NCT or "NCT99999901")
with tab_audit:
    tabs.audit_tab(client)

st.markdown(
    theme.FOOTER
    + f'<div class="cl-note" style="margin-top:0.2rem">Engine mode: {esc(health.get("mode", "?"))}. Database '
    + ("connected." if health.get("db") else "not connected: placeholder fixtures are used where needed, and labelled.")
    + "</div>",
    unsafe_allow_html=True,
)
