"""Verdict panel for the Streamlit dashboard. Renders banner + KPIs + notes."""
import streamlit as st


def render_verdict(verdict: dict) -> None:
    label = "PASS" if verdict["pass"] else "WATCH" if verdict["watch"] else "SHELF"
    color = {"PASS": "#22c55e", "WATCH": "#f59e0b", "SHELF": "#ef4444"}[label]
    st.markdown(
        f"<div style='background:{color};padding:8px;color:white;font-weight:700;"
        f"border-radius:6px'>{label}</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("DSR",       f"{verdict['dsr']:.2f}")
    c2.metric("FWER",      f"{verdict['fwer']:.2f}")
    c3.metric("K_eff",     f"{verdict['k_effective']}")
    c4.metric("Calmar",    f"{verdict['calmar_overall']:.2f}")
    if verdict["regime_kill"]:
        st.warning("Regime kill fired — negative Sharpe in a cell ≥ 20% sample")
    if verdict.get("notes"):
        with st.expander("Notes"):
            st.json(verdict["notes"])
    # dump values as markdown so AppTest can assert labels
    st.markdown(f"verdict={label} dsr={verdict['dsr']:.2f} fwer={verdict['fwer']:.2f}")
