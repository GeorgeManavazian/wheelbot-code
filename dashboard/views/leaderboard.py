import streamlit as st

from dashboard import loader, naming, recompute, shared, style
from src.batch.runner import luck_warning

COLUMN_HELP = {
    "strategy": "Strategy + settings in plain words (same run as the technical label, just readable).",
    "sharpe": "Return per unit of risk. Above 1 good, above 2 excellent. Below the luck line: meaningless.",
    "cagr": "Compound annual growth rate — average yearly return.",
    "max_dd": "Max drawdown — worst peak-to-trough loss along the way. Closer to 0 is better.",
    "n_trades": "Trades in the backtest. Under 30 = statistically worthless; 100+ preferred.",
    "sample_flag": "OK = enough trades to take the stats seriously.",
    "positive_years": "Number of years that ended with a gain.",
    "total_years": "Years covered by the backtest.",
    "turnover": "How much of the portfolio is replaced per year (1.0 = fully replaced once).",
    "exposure": "Share of time the money was invested rather than sitting in cash.",
    "best_year": "Single best calendar-year return.",
    "worst_year": "Single worst calendar-year return.",
    "top2_share": "How much of total profit came from just the 2 best periods — high = fragile.",
    "label": "Technical label (used internally to identify the run).",
}

METRIC_ORDER = ["strategy", "sharpe", "cagr", "max_dd", "n_trades", "sample_flag",
                "positive_years", "total_years", "turnover", "exposure",
                "best_year", "worst_year", "top2_share"]


def render():
    st.title("Strategy leaderboard")
    st.caption("Every backtest in this batch, ranked by risk-adjusted return.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Each row is one strategy with one specific setting, tested on 2010–2020.\n"
            "- **Click any column header to sort.** Hover a column name for what it means.\n"
            "- The orange banner is the **luck line**: with this many attempts, the best "
            "random junk would score about that Sharpe. Anything below it proves nothing.\n"
            "- Colors: greener = better Sharpe/growth, darker red = deeper worst-case loss.\n"
            "- Click a row, then open **Run detail** in the sidebar for charts and the story."
        )

    lb = shared.current_lb()
    ok, bad = loader.split_errors(lb)

    eq_index = shared.playground()["date"]
    years = (eq_index.max() - eq_index.min()).days / 365.25
    st.warning(luck_warning(len(lb), years))

    fmap = naming.friendly_map(ok)
    view = ok.copy()
    view.insert(0, "strategy", view["label"].map(fmap))
    cols = [c for c in METRIC_ORDER if c in view.columns] + ["label"]
    display = view[cols]

    event = st.dataframe(
        style.style_metrics(display), hide_index=True,
        on_select="rerun", selection_mode="single-row",
        width="stretch",
        column_config={c: st.column_config.Column(help=h)
                       for c, h in COLUMN_HELP.items() if c in display.columns})
    if event.selection.rows:
        label = display.iloc[event.selection.rows[0]]["label"]
        st.session_state["selected_label"] = label
        st.caption(f"Selected **{fmap[label]}** — open *Run detail* in the sidebar.")

    st.subheader("What these strategies do")
    for name in ok["name"].unique():
        cls = recompute.STRATEGIES.get(name)
        title = getattr(cls, "display_name", "") or name
        dot = style.family_color(name)
        with st.expander(title):
            st.markdown(
                f'<span style="color:{dot}">●</span> shown in this color on all charts',
                unsafe_allow_html=True)
            st.markdown(cls.description if cls else
                        "_plugin not found in src/strategies/_")

    if not bad.empty:
        st.subheader(":red[Crashed runs]")
        st.dataframe(bad[["label", "name", "error"]], hide_index=True,
                     width="stretch")
