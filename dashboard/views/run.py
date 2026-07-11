"""Run page: pick strategy + tickers + dates + cost knobs, click Run, see
gate-free diagnostics on the real 20-ETF universe. Calls run_simple
in-process — no CSV round-trip. Headline = recent window; also shows the
year-by-year Sharpe decay curve (standing methodology)."""
import os
import streamlit as st
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
        bars = src.load(tickers, start, end)
        cfg = BacktestConfig(spread_bps_per_side=spread, borrow_bps_annual=borrow)
        bench_tickers = [t for t in ("SPY", "TLT") if t in tickers_all]
        bench_bars = src.load(bench_tickers, start, end) if bench_tickers else None
        res = run_simple(get_strategy(name), bars, config=cfg, benchmark_bars=bench_bars)

        st.subheader(f"Headline — recent since {res.recent['start']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("CAGR", f"{res.recent['cagr']:.2%}")
        c2.metric("Sharpe", f"{res.recent['sharpe']:.2f}")
        c3.metric("Max drawdown", f"{res.recent['max_drawdown']:.2%}")
        st.caption(f"Full history: CAGR {res.cagr:.2%} · Sharpe {res.sharpe:.2f} · "
                   f"maxDD {res.max_drawdown:.2%} · trades {res.trades}")

        curve = res.equity.rename("strategy").to_frame()
        for bname, series in res.benchmarks.items():
            curve[bname] = series
        st.subheader("Equity curve")
        st.line_chart(curve)
        st.subheader("Year-by-year Sharpe (decay curve)")
        st.bar_chart(res.yearly_sharpe)
        st.subheader("Year-by-year return")
        st.bar_chart(res.yearly)
        st.subheader("By regime")
        st.dataframe(res.regime, use_container_width=True)
        st.caption(f"bars/yr ≈ {res.periods_per_year:.0f}")
