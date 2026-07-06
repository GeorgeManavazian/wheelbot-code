import pandas as pd
import streamlit as st

from dashboard import benchmark, loader, naming, recompute, shared, style
from src.batch.runner import luck_warning

# raw column -> (display label, hover help)
COLUMNS = {
    "strategy": ("Strategy", "Strategy + settings in plain words (same run as the technical label, just readable)."),
    "sharpe": ("Sharpe", "Return per unit of risk. Above 1 good, above 2 excellent. Below the luck line: meaningless."),
    "cagr": ("CAGR", "Compound annual growth rate — average yearly return."),
    "max_dd": ("Max DD", "Max drawdown — worst peak-to-trough loss along the way. Closer to 0 is better."),
    "n_trades": ("Trades", "Trades in the backtest. Under 30 = statistically worthless; 100+ preferred."),
    "sample_flag": ("Sample", "OK = enough trades to take the stats seriously."),
    "years_up": ("Years up", "Calendar years that ended with a gain, out of years tested."),
    "turnover": ("Turnover/yr", "How much of the portfolio is replaced per year (1.0 = fully replaced once)."),
    "exposure": ("Exposure", "Share of time the money was invested rather than sitting in cash."),
    "best_year": ("Best yr", "Single best calendar-year return."),
    "worst_year": ("Worst yr", "Single worst calendar-year return."),
    "top2_share": ("Top-2 share", "How much of total profit came from just the 2 best periods — high = fragile."),
    "label": ("(technical)", "Technical label (used internally to identify the run)."),
}

METRIC_ORDER = ["strategy", "sharpe", "cagr", "max_dd", "n_trades",
                "sample_flag", "years_up", "turnover", "exposure",
                "best_year", "worst_year", "top2_share"]


def _years_up(df):
    return (df["positive_years"].astype(int).astype(str) + " of "
            + df["total_years"].astype(int).astype(str))


def render():
    st.title("Strategy leaderboard")
    st.caption("Every backtest in this batch, ranked by risk-adjusted return.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Each row is one strategy with one specific setting, tested on 2010–2020.\n"
            "- **Click any column header to sort.** Hover a column name for what it means.\n"
            "- The orange banner is the **luck line**: with this many attempts, the best "
            "random junk would score about that Sharpe. Anything below it proves nothing.\n"
            "- Colors: **green only above the luck line** — gray Sharpe cells are "
            "statistically indistinguishable from noise, whatever the number says.\n"
            "- The tinted top row is the do-nothing benchmark: buy SPY and hold.\n"
            "- Click a row, then use the link that appears to open its charts."
        )

    lb = shared.current_lb()
    ok, bad = loader.split_errors(lb)

    dates = shared.playground()["date"]
    years = (dates.max() - dates.min()).days / 365.25
    st.warning(luck_warning(len(lb), years))
    luck = shared.luck_threshold()

    fmap = naming.friendly_map(ok)
    view = ok.copy()
    view.insert(0, "strategy", view["label"].map(fmap))
    view["years_up"] = _years_up(view)

    spy_eq, spy_row = shared.spy_benchmark()
    if spy_row is not None:
        bench = dict(spy_row)
        bench["strategy"] = benchmark.FRIENDLY
        bench["years_up"] = (f"{int(bench['positive_years'])} of "
                             f"{int(bench['total_years'])}")
        view = pd.concat([pd.DataFrame([bench]), view], ignore_index=True)
    else:
        st.info("SPY not in dataset — benchmark hidden")

    cols = [c for c in METRIC_ORDER if c in view.columns] + ["label"]
    display = view[cols]

    event = st.dataframe(
        style.style_metrics(display, luck_sharpe=luck,
                            highlight_label=benchmark.LABEL),
        hide_index=True,
        on_select="rerun", selection_mode="single-row",
        width="stretch",
        height=min(35 * (len(display) + 1) + 3, 1200),
        column_config={c: st.column_config.Column(label=lab, help=h)
                       for c, (lab, h) in COLUMNS.items()
                       if c in display.columns})
    if event.selection.rows:
        label = display.iloc[event.selection.rows[0]]["label"]
        if label != benchmark.LABEL:
            st.session_state["selected_label"] = label
            pages = st.session_state.get("pages")
            if pages:
                st.page_link(pages["run"],
                             label=f"Open run detail → {fmap[label]}")
            else:
                st.caption(f"Selected **{fmap[label]}** — open *Run detail* "
                           "in the sidebar.")

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
