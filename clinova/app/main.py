"""Clinova Streamlit app (owner: T15, then T25 and T32). Talks to the API only through ``client.py``.

Run: ``python -m clinova.api`` in one terminal and ``python -m clinova.app`` in another.
"""

from __future__ import annotations

import streamlit as st

from clinova.app import panels, tabs, theme
from clinova.app.client import ApiClient, ApiError
from clinova.app.rules_table import FIELD_CHOICES, OPERATOR_CHOICES, df_to_rules, rules_to_df
from clinova.app.theme import esc
from clinova.common.fixtures import FIXTURES_DIR
from clinova.contracts import ParsedProtocol, RunStatus

STAGE_LABELS = {
    "queued": "Queued",
    "parse": "Reading the criteria",
    "cohort": "Building virtual patients",
    "screen": "Screening patients against the criteria",
    "agents": "Simulating what patients decide",
    "score": "Scoring risk",
    "flags": "Finding the criteria that matter most",
    "optimize": "Searching for suggested fixes",
    "done": "Done",
    "failed": "Failed",
}

st.set_page_config(page_title="Clinova", layout="wide", initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)


@st.cache_resource
def get_client() -> ApiClient:
    return ApiClient()


client = get_client()
ss = st.session_state


def reset_results() -> None:
    for k in ("status", "decisions", "after_runs", "backtest"):
        ss.pop(k, None)


# --------------------------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------------------------

st.markdown("<h1>Clinova</h1>", unsafe_allow_html=True)
st.markdown(
    '<div class="cl-sub">Test-fly a lung cancer trial protocol on virtual patients before enrolling real ones. '
    "Clinova offers suggestions to the protocol author; it never approves or rejects a protocol.</div>",
    unsafe_allow_html=True,
)
try:
    client.health()
except ApiError as exc:
    st.error(f"{exc} Start it with: python -m clinova.api")
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
    input_box = st.expander("1. Your protocol", expanded=not has_protocol)
    input_box.__enter__()
    mode = st.radio(
        "Start from",
        ["Paste a protocol", "Load a registered trial"],
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
        sched = c2.text_area("Schedule of visits", key="sched", height=210, placeholder="Paste the schedule of assessments...")
        go_parse = st.button("Parse protocol", type="primary", key="parse_text", disabled=not elig.strip())
        parse_args = {"eligibility_text": elig, "schedule_text": sched or None}
    else:
        nct = st.text_input("NCT ID", key="nct", placeholder="NCT99999901")
        st.caption("Loads the protocol as it was first registered, before any amendment.")
        go_parse = st.button("Load protocol", type="primary", key="parse_nct", disabled=not nct.strip())
        parse_args = {"nct_id": nct.strip()}
    input_box.__exit__(None, None, None)

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
        except ApiError as exc:
            st.error(str(exc))

    protocol: ParsedProtocol | None = ParsedProtocol.model_validate(ss["protocol"]) if "protocol" in ss else None

    if protocol is not None:
        meta = ss.get("parse_meta", {})
        tag = '<span class="cl-tag">fixture data</span>' if meta.get("source") == "fixture" else ""
        review_box = st.expander("2. Review the parsed criteria", expanded=not has_results)
        review_box.__enter__()
        title = protocol.title or protocol.trial_id
        st.markdown(
            f"<b>{esc(title)}</b> &nbsp;<span class='cl-note'>{esc(protocol.trial_id)}</span> {tag}",
            unsafe_allow_html=True,
        )
        if meta.get("note"):
            st.markdown(f'<div class="cl-note">{esc(meta["note"])}</div>', unsafe_allow_html=True)
        n_bad = len(protocol.unparseable)
        st.markdown(
            f'<div class="cl-note" style="margin:0.3rem 0">{len(protocol.rules)} criteria. Edit any value, or lock a criterion so no fix touches it. '
            + (f"{n_bad} could not be parsed and are highlighted: Clinova will not guess, and skips them when screening." if n_bad else "Every criterion was parsed.")
            + "</div>",
            unsafe_allow_html=True,
        )
        base_df = ss["rules_df"]

        def _highlight(row):
            bad = row["Status"] != "Parsed"
            return ["background-color: #FFF4D6; color: #1D2733" if bad else "" for _ in row]

        edited = st.data_editor(
            base_df.style.apply(_highlight, axis=1),
            key=f"rules_editor_{ss.get('rules_version', 0)}",
            hide_index=True,
            width="stretch",
            height=60 + 36 * len(base_df),
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
        review_box.__exit__(None, None, None)
        with st.expander(f"Visit schedule ({len(protocol.visits)} visits)"):
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

        st.markdown('<div class="cl-section">3. Run</div>', unsafe_allow_html=True)
        if st.button("Run Clinova", type="primary", key="run"):
            reset_results()
            try:
                created = client.start_run(reviewed, nct_id=None)
                bar = st.progress(0.0, text="Starting...")

                def on_update(s: RunStatus) -> None:
                    bar.progress(s.progress, text=STAGE_LABELS.get(s.stage, s.stage))

                final = client.wait_for_run(created.run_id, on_update=on_update)
                bar.empty()
                if final.stage == "failed":
                    st.error(f"The run did not finish: {final.message}")
                else:
                    ss["status"] = final.model_dump(mode="json")
            except ApiError as exc:
                st.error(str(exc))

    status = RunStatus.model_validate(ss["status"]) if "status" in ss else None
    if status is not None and status.result is not None:
        st.markdown('<div class="cl-section">4. Results</div>', unsafe_allow_html=True)
        res = status.result
        st.markdown(
            f"<b>{esc(res.protocol.title or res.protocol.trial_id)}</b> &nbsp;{theme.fixture_tag(status.source)}"
            + (f"<div class='cl-note'>{esc(status.message)}</div>" if status.source.value == "fixture" else ""),
            unsafe_allow_html=True,
        )
        col_f, col_r, col_x = st.columns([1, 1, 1.15], gap="large")
        with col_f:
            panels.funnel_panel(status)
        with col_r:
            panels.risk_panel(status)
        with col_x:
            panels.fixes_panel(status, client)

        nct = (ss.get("parse_meta") or {}).get("nct_id")
        if nct:
            st.markdown('<div class="cl-section">What happened next (backtest reveal)</div>', unsafe_allow_html=True)
            if "backtest" not in ss:
                try:
                    ss["backtest"] = client.backtest(nct).model_dump(mode="json")
                except ApiError as exc:
                    ss["backtest"] = {"error": str(exc)}
            bt = ss["backtest"]
            if "error" in bt:
                st.info(bt["error"])
            else:
                panels.reveal(bt)

with tab_val:
    tabs.validation_tab(client)
with tab_replay:
    tabs.replay_tab(client)
with tab_pulse:
    tabs.pulse_tab(client, (ss.get("parse_meta") or {}).get("nct_id") or "NCT99999901")
with tab_audit:
    tabs.audit_tab(client)
