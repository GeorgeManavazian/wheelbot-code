import streamlit as st

from dashboard import loader, recompute, shared
from src.batch.runner import luck_warning

METRIC_ORDER = ["label", "name", "sharpe", "cagr", "max_dd", "n_trades",
                "sample_flag", "positive_years", "total_years", "turnover",
                "exposure"]


def render():
    st.title("Strategy leaderboard")
    lb = shared.current_lb()
    ok, bad = loader.split_errors(lb)

    eq_index = shared.playground()["date"]
    years = (eq_index.max() - eq_index.min()).days / 365.25
    st.warning(luck_warning(len(lb), years))

    cols = [c for c in METRIC_ORDER if c in ok.columns]
    extras = [c for c in ok.columns if c not in cols + ["error"]]
    event = st.dataframe(
        ok[cols + extras], hide_index=True,
        on_select="rerun", selection_mode="single-row",
        use_container_width=True)
    if event.selection.rows:
        label = ok.iloc[event.selection.rows[0]]["label"]
        st.session_state["selected_label"] = label
        st.caption(f"Selected **{label}** — open *Run detail* in the sidebar.")

    st.subheader("What these strategies do")
    for name in ok["name"].unique():
        cls = recompute.STRATEGIES.get(name)
        with st.expander(name):
            st.markdown(cls.description if cls else
                        "_plugin not found in src/strategies/_")

    if len(bad):
        st.subheader(":red[Crashed runs]")
        st.dataframe(bad[["label", "name", "error"]], hide_index=True,
                     use_container_width=True)
