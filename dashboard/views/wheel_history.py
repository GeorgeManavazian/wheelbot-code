"""History tab: past wheel backtest runs, clear history, load a run's config
back into the Wheel form."""
import streamlit as st
from dashboard import labels
from dashboard.wheel_history import load_runs, clear_runs

# target_dte replaced dte_min/dte_max (2026-07-12 API contract). Old history
# rows lack it; the Wheel page's load path tolerates missing keys.
_CFG_KEYS = ["put_delta", "call_delta", "target_dte", "take_profit",
             "capital", "data_source", "start", "end"]


def render():
    st.title("Wheel — run history")
    runs = load_runs()
    if runs.empty:
        st.info("No runs yet. Run a wheel backtest and it'll show up here.")
        return

    st.dataframe(labels.humanize(runs), use_container_width=True)

    c1, c2 = st.columns(2)
    if c1.button("Clear history", key="clear_history"):
        clear_runs()
        st.rerun()

    idx = c2.selectbox(
        "Load a run's config", runs.index,
        format_func=lambda i: f"{runs.loc[i, 'ts']}  ·  {runs.loc[i, 'pnl']}",
        key="hist_pick",
    )
    if c2.button("Load config into form", key="load_cfg"):
        row = runs.loc[idx]
        st.session_state["_load_cfg"] = {k: row[k] for k in _CFG_KEYS if k in row}
        st.success("Config staged — open the Wheel tab.")
