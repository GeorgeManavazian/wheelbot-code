"""Run page: pick strategy + tickers + dates + cost knobs, click Run, see
gate-free diagnostics on the real 20-ETF universe. Calls run_simple
in-process — no CSV round-trip. Headline = recent window; also shows the
year-by-year Sharpe decay curve (standing methodology)."""
import os
import pandas as pd
import streamlit as st
from dashboard import charts, labels, sensitivity
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy
from src.engine_v2.data.source import default_source, intraday_source, INTRADAY_PATH
from src.engine_v2.backtest.simple import run_simple
from src.engine_v2.backtest.orchestrator import BacktestConfig

def render():
    st.title("Run a backtest")
    choice = st.selectbox("Data", ["Daily (20-ETF universe)",
                                    "Intraday 1-min (SPY/QQQ)"], key="data_source")
    if choice.startswith("Intraday"):
        if not os.path.exists(INTRADAY_PATH):
            st.warning("Intraday cache not built. Run:\n\n"
                       "`.venv/bin/python -m scripts.build_intraday_cache`\n\n"
                       "then reload.")
            st.stop()
        src = intraday_source()
    else:
        src = default_source()
    tickers_all = src.available_tickers()
    lo, hi = src.date_range()

    name = st.selectbox("Strategy", list(STRATEGIES), key="strategy")
    st.caption(get_strategy(name).mechanism)
    tickers = st.multiselect("Universe", tickers_all, default=tickers_all, key="tickers")
    start, end = st.slider("Date range", min_value=lo.to_pydatetime(),
                           max_value=hi.to_pydatetime(),
                           value=(lo.to_pydatetime(), hi.to_pydatetime()),
                           key="date_range")
    spread = st.number_input("Spread (bps/side)", 0.0, 100.0, 1.0, 0.5, key="spread")
    borrow = st.number_input("Borrow (bps/yr)", 0.0, 2000.0, 50.0, 10.0, key="borrow")

    if st.button("Run", key="run_backtest", type="primary"):
        if not tickers:
            st.warning("Pick at least one ticker.")
            st.stop()
        # Include the whole final day: load_bars slices df.loc[start:end], and the
        # intraday date slider snaps to 09:30, so a bare end would keep only the
        # opening minute of the last day. Push end to end-of-day (harmless for daily).
        end_ts = pd.Timestamp(end).normalize() + pd.Timedelta(hours=23, minutes=59)
        bars = src.load(tickers, start, end_ts)
        cfg = BacktestConfig(spread_bps_per_side=spread, borrow_bps_annual=borrow)
        bench_tickers = [t for t in ("SPY", "TLT") if t in tickers_all]
        bench_bars = src.load(bench_tickers, start, end_ts) if bench_tickers else None
        st.session_state["_result"] = run_simple(
            get_strategy(name), bars, config=cfg, benchmark_bars=bench_bars)
        # Kept so the cost sweep can re-run the same backtest across spread levels.
        st.session_state["_sweep"] = dict(
            name=name, bars=bars, bench_bars=bench_bars, borrow=borrow)

    res = st.session_state.get("_result")
    if res is not None:
        st.subheader(f"Headline — recent since {res.recent['start']}")
        # Headline is P&L, not CAGR (owner's rule #3, per the wheel engine API
        # contract). CAGR still lives in the full-history caption below.
        eq_recent = res.equity[res.equity.index >= res.recent["start"]]
        pnl = eq_recent.iloc[-1] - eq_recent.iloc[0]
        total_return = eq_recent.iloc[-1] / eq_recent.iloc[0] - 1.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("P&L", f"${pnl:,.0f}", f"{total_return:+.2%}")
        c2.metric("Sharpe", f"{res.recent['sharpe']:.2f}")
        c3.metric("Max drawdown", f"{res.recent['max_drawdown']:.2%}")

        # vs buy & hold: benchmarks is {} only if the data source has no SPY at
        # all. Deselecting SPY from the universe does NOT empty it — bench_tickers
        # comes from tickers_all, so SPY buy & hold stays the benchmark whatever
        # you trade. That is the honest comparison.
        spy = res.benchmarks.get("SPY")
        if spy is None:
            c4.metric("vs Buy & Hold", "—", help="SPY is not in this dataset.")
        else:
            spy_recent = spy[spy.index >= res.recent["start"]]
            spy_return = spy_recent.iloc[-1] / spy_recent.iloc[0] - 1.0
            gap = total_return - spy_return
            c4.metric("vs Buy & Hold", f"{gap:+.2%}",
                      help=f"SPY buy & hold returned {spy_return:+.2%} over the "
                           f"same window. This is the number that stops the "
                           f"dashboard flattering you.")

        st.caption(f"Full history: CAGR {res.cagr:.2%} · Sharpe {res.sharpe:.2f} · "
                   f"maxDD {res.max_drawdown:.2%} · trades {res.trades}")

        st.subheader("Equity curve")
        st.plotly_chart(charts.equity_curve(res.equity, res.benchmarks),
                        use_container_width=True)
        st.subheader("Underwater — how deep, and how long")
        st.plotly_chart(charts.underwater(res.equity), use_container_width=True)

        left, right = st.columns(2)
        with left:
            st.subheader("Year-by-year Sharpe")
            st.caption("The decay curve. Never headline one blended number.")
            st.plotly_chart(charts.yearly_bars(res.yearly_sharpe, percent=False),
                            use_container_width=True)
        with right:
            st.subheader("Year-by-year return")
            st.plotly_chart(charts.yearly_bars(res.yearly, percent=True),
                            use_container_width=True)

        st.subheader("Monthly returns")
        st.plotly_chart(charts.monthly_heatmap(res.equity), use_container_width=True)

        st.subheader("By regime")
        st.dataframe(labels.humanize(res.regime), use_container_width=True)
        st.caption(f"bars/yr ≈ {res.periods_per_year:.0f}")

        # Cost sensitivity: one backtest per spread level, so it sits behind its own
        # button and never fires on a normal run. Reuses the bars the run already
        # loaded — no second data load.
        st.subheader("Cost sensitivity")
        st.caption(f"Re-runs at spreads {list(sensitivity.SPREAD_LEVELS)} bps/side "
                   "to find where the edge dies. Slow — one backtest per level.")
        if st.button("Run cost sweep", key="cost_sweep"):
            s = st.session_state["_sweep"]
            with st.spinner(f"Running {len(sensitivity.SPREAD_LEVELS)} backtests…"):
                sweep = sensitivity.cost_sweep(
                    get_strategy(s["name"]), s["bars"],
                    BacktestConfig(borrow_bps_annual=s["borrow"]),
                    benchmark_bars=s["bench_bars"])
            st.dataframe(labels.humanize(sweep), use_container_width=True,
                         hide_index=True)
