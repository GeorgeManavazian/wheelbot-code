import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import loader, naming, recompute, shared, style

METRICS = ["sharpe", "cagr", "max_dd", "n_trades", "turnover", "exposure",
           "positive_years", "total_years", "sample_flag"]

DASHES = ["solid", "dash", "dot", "dashdot"]

BENCH_NAME = "S&P 500 (benchmark)"


def render():
    st.title("Compare runs")
    st.caption("Overlay a few runs to see which behaved best, and when.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Every line starts at $1 — whoever ends highest grew the most.\n"
            "- Line **color = strategy family**; same-family runs get "
            "lighter/darker shades of the family color plus different dashes.\n"
            "- The gray line is the do-nothing benchmark: buy SPY and hold.\n"
            "- Watch the rough patches, not just the finish: a line that "
            "dives 30% before recovering was a much scarier ride.\n"
            "- The table below shades the same metrics as the leaderboard."
        )
    ok, _ = loader.split_errors(shared.current_lb())
    fmap = naming.friendly_map(ok)
    labels = st.multiselect("Pick 2–4 runs", ok["label"].tolist(),
                            max_selections=4, format_func=lambda l: fmap[l])
    if labels:
        st.caption("Picked: " + " · ".join(f"**{fmap[l]}**" for l in labels))
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
        spy_eq, _ = shared.spy_benchmark()
        if spy_eq is not None:
            df[BENCH_NAME] = (spy_eq / spy_eq.iloc[0]).reindex(df.index)

        by_family = {}
        for friendly, name in families.items():
            by_family.setdefault(name, []).append(friendly)
        color_map = {}
        for name, members in by_family.items():
            base = style.family_color(name)
            for i, friendly in enumerate(members):
                color_map[friendly] = style.shade_family(base, i, len(members))
        color_map[BENCH_NAME] = style.MUTED

        fig = px.line(df, log_y=True, title="Growth of $1 (log scale)",
                      color_discrete_map=color_map,
                      labels={"value": "growth of $1", "variable": "run"})
        for i, tr in enumerate(fig.data):
            tr.line.dash = ("dot" if tr.name == BENCH_NAME
                            else DASHES[i % len(DASHES)])
            if tr.name == BENCH_NAME:
                tr.line.width = 1.5
        st.plotly_chart(style.apply_plotly_defaults(fig))

    side = ok[ok["label"].isin(labels)].copy()
    side.insert(0, "strategy", side["label"].map(fmap))
    side = side.set_index("strategy")[[c for c in METRICS if c in side.columns]]
    st.dataframe(style.style_metrics(side, luck_sharpe=shared.luck_threshold()),
                 width="stretch")
