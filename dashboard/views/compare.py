import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import loader, naming, recompute, shared, style

METRICS = ["sharpe", "cagr", "max_dd", "n_trades", "turnover", "exposure",
           "positive_years", "total_years", "sample_flag"]

DASHES = ["solid", "dash", "dot", "dashdot"]


def render():
    st.title("Compare runs")
    st.caption("Overlay a few runs to see which behaved best, and when.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Every line starts at $1 — whoever ends highest grew the most.\n"
            "- Line **color = strategy family** (same colors as everywhere else); "
            "same-family runs differ by line style.\n"
            "- Watch the rough patches, not just the finish: a line that "
            "dives 30% before recovering was a much scarier ride.\n"
            "- The table below shades the same metrics as the leaderboard."
        )
    ok, _ = loader.split_errors(shared.current_lb())
    fmap = naming.friendly_map(ok)
    labels = st.multiselect("Pick 2–4 runs", ok["label"].tolist(),
                            max_selections=4, format_func=lambda l: fmap[l])
    if len(labels) < 2:
        st.info("Pick at least 2 runs.")
        return

    curves, families = {}, {}
    for label in labels:
        row = ok[ok["label"] == label].iloc[0].to_dict()
        try:
            result = shared.run_result(row)
        except recompute.UnknownStrategyError as e:
            st.error(f"{fmap[label]}: {e}")
            continue
        curves[fmap[label]] = result.equity / result.equity.iloc[0]
        families[fmap[label]] = row["name"]

    if len(curves) >= 2:
        df = pd.DataFrame(curves)
        fig = px.line(df, log_y=True, title="Growth of $1 (log scale)",
                      color_discrete_map={f: style.family_color(n)
                                          for f, n in families.items()},
                      labels={"value": "growth", "variable": "run"})
        for i, tr in enumerate(fig.data):
            tr.line.dash = DASHES[i % len(DASHES)]
        st.plotly_chart(style.apply_plotly_defaults(fig))

    side = ok[ok["label"].isin(labels)].copy()
    side.insert(0, "strategy", side["label"].map(fmap))
    side = side.set_index("strategy")[[c for c in METRICS if c in side.columns]]
    st.dataframe(style.style_metrics(side), width="stretch")
