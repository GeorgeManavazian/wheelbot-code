import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared

METRICS = ["sharpe", "cagr", "max_dd", "n_trades", "turnover", "exposure",
           "positive_years", "total_years", "sample_flag"]


def render():
    st.title("Compare runs")
    ok, _ = loader.split_errors(shared.current_lb())
    labels = st.multiselect("Pick 2–4 runs", ok["label"].tolist(),
                            max_selections=4)
    if len(labels) < 2:
        st.info("Pick at least 2 runs.")
        return

    curves = {}
    for label in labels:
        row = ok[ok["label"] == label].iloc[0].to_dict()
        try:
            result = shared.run_result(row)
        except recompute.UnknownStrategyError as e:
            st.error(f"{label}: {e}")
            continue
        curves[label] = result.equity / result.equity.iloc[0]

    if len(curves) >= 2:
        df = pd.DataFrame(curves)
        st.plotly_chart(
            px.line(df, log_y=True,
                    title="Growth of $1 (log scale)",
                    labels={"value": "growth", "variable": "run"}),
            use_container_width=True)

    side = (ok[ok["label"].isin(labels)]
            .set_index("label")[METRICS].T)
    st.dataframe(side, use_container_width=True)
