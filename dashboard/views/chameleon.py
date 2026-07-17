"""Chameleon (regime router) page: pick ticker + config, run, see the router's
postures shaded on the price chart with an option-leg blotter. Exploration only
— the referee CLI stays the citation gate. Mirrors views/wheel.py."""
import os
import pandas as pd
import streamlit as st
from dashboard import bars, charts, labels, theme, trades
from dashboard.guard import seen_sources
from src.engine_v2.options.data import intraday_path
from src.engine_v2.options.select import derived_band
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.intraday import intraday_marks
from src.engine_v2.options.report import router_report
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

FIXTURE_TICKER = "SPY (2024 sample fixture)"
FIXTURE = "fixtures/spy_wheel_cycle.parquet"
XOP_CLEAN_START = pd.Timestamp("2020-07-01")
FROZEN = dict(put_delta=0.20, call_delta=0.20, target_dte=7, take_profit_pct=0.50)


def render():
    st.title("Chameleon — regime router")
    st.caption("Routes per-ticker regime between hold-shares (TREND), the wheel "
               "(WHEEL) and cash (CASH). Exploration only — cite numbers off the "
               "referee CLI, not this page.")
    sources = seen_sources(include_fixture_name=FIXTURE_TICKER, fixture_path=FIXTURE)
    if not sources:
        st.warning("No options data under data/options/. Pull a seen-ticker chain first.")
        st.stop()

    ticker_name = st.selectbox(
        "Ticker", list(sources), key="cham_data",
        help="Unseen basket tickers stay hidden until the pre-registered basket "
             "run reports (amendment 2026-07-13d).")
    path = sources[ticker_name]
    ticker = "SPY" if ticker_name == FIXTURE_TICKER else ticker_name
    if not os.path.exists(path):
        st.warning(f"Data not found: {path}."); st.stop()
    ch = pd.read_parquet(path)
    ch["date"] = pd.to_datetime(ch["date"])

    min_d, max_d = ch["date"].min().date(), ch["date"].max().date()
    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="cham_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="cham_end")
    if ticker == "XOP" and pd.Timestamp(start) < XOP_CLEAN_START:
        st.warning("XOP's chain is split-broken before 2020-07-01: windows spanning "
                   "it book phantom gains — provenance-only.")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.20, 0.05, key="c_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.20, 0.05, key="c_cd")
    target_dte = c2.number_input("Target DTE", 5, 60, 7, key="c_tdte")
    band_lo, band_hi = derived_band(int(target_dte))
    c2.caption(f"trades expiries {band_lo}–{band_hi} days out")
    tp_slider = c3.slider("Take-profit (% of credit)", 1, 100, 50, key="c_tp")
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="c_cap")

    # Off-config banner (non-blocking): only the frozen form is the citable router.
    off = (put_delta != FROZEN["put_delta"] or call_delta != FROZEN["call_delta"]
           or int(target_dte) != FROZEN["target_dte"] or tp_slider != int(FROZEN["take_profit_pct"] * 100))
    if off:
        st.caption("⚠ Off-config — not the pre-registered router; posture routing "
                   "shifts with DTE (see the v2 DTE experiment).")
    else:
        st.caption("✓ Pre-registered form (frozen config).")

    intra = None if ticker_name == FIXTURE_TICKER else intraday_path(ticker)
    if ticker_name == FIXTURE_TICKER:
        INTRA_FIXTURE = "fixtures/spy_wheel_intraday_sample.parquet"
        intra = INTRA_FIXTURE if os.path.exists(INTRA_FIXTURE) else None
    intraday_on = st.checkbox("Intraday take-profit (hourly)", key="cham_intraday",
                              disabled=intra is None,
                              help=None if intra else "No hourly OHLC on disk for this ticker yet.")

    if st.button("Run", key="run_cham", type="primary"):
        ch = ch[(ch["date"] >= pd.Timestamp(start)) & (ch["date"] <= pd.Timestamp(end))].reset_index(drop=True)
        marks = None
        if intraday_on and intra:
            ih = pd.read_parquet(intra)
            ih["timestamp"] = pd.to_datetime(ih["timestamp"])
            # Window the chain to the hourly span (amendment 16a) so no day
            # silently falls back to EOD inside a run labelled hourly.
            h_lo, h_hi = ih["timestamp"].min().normalize(), ih["timestamp"].max().normalize()
            ch = ch[(ch["date"] >= h_lo) & (ch["date"] <= h_hi)].reset_index(drop=True)
            marks = intraday_marks(ih)
        if ch["date"].nunique() < 5:
            st.warning("Window too short."); st.stop()
        states = regime_series(closes_for(ticker))
        if states.empty:
            st.warning(f"Not enough history for a regime state on {ticker}."); st.stop()
        cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                          call_delta=call_delta, target_dte=int(target_dte),
                          take_profit_pct=tp_slider / 100.0, ticker=ticker,
                          call_min_strike="basis")
        res = run_regime_router(ch, cfg, states, intraday=marks)
        rep = router_report(res, ch, cfg)
        st.session_state["_cham_result"] = (res, rep, cfg)

    stashed = st.session_state.get("_cham_result")
    if stashed is not None:
        res, rep, cfg = stashed
        pnl = res.equity.iloc[-1] - cfg.starting_capital
        a, b, c, d = st.columns(4)
        a.metric("P&L", f"${pnl:,.0f}", f"{rep.metrics['total_return']:+.2%}")
        b.metric("Sharpe", f"{rep.metrics['sharpe']:.2f}")
        c.metric("Max drawdown", f"{rep.metrics['max_drawdown']:.2%}")
        gap = rep.metrics["total_return"] - rep.benchmark_underlying["total_return"]
        d.metric(f"vs buy-hold {rep.ticker}", f"{gap:+.2%}",
                 help=f"Buy-hold {rep.ticker} returned "
                      f"{rep.benchmark_underlying['total_return']:+.2%} over this window.")
        p = rep.posture
        st.caption(f"days TREND/WHEEL/CASH {p['days'].get('TREND',0)}/{p['days'].get('WHEEL',0)}/"
                   f"{p['days'].get('CASH',0)}  ·  transitions {p['transitions']}  ·  "
                   f"whipsaws {p['whipsaws']}  ·  intraday-TP {rep.fills['intraday_tp']}  ·  "
                   f"EOD-TP {rep.fills['eod_tp']}")

        st.subheader("Postures on price")
        st.caption("Bands: TREND (holding shares) · WHEEL (running the wheel) · "
                   "CASH (sidelined). Option legs draw only in WHEEL; TREND P&L is "
                   "in the equity curve, not the blotter.")
        w0, w1 = res.equity.index[0], res.equity.index[-1]
        bars_df = bars.load_bars(rep.ticker, w0, w1)
        if bars_df.empty:
            st.info(f"No daily bars on disk for {rep.ticker} — chart unavailable.")
        else:
            st.plotly_chart(
                charts.candles_with_trades(
                    bars_df, trades.overlay_frame(rep.blotter, w1),
                    posture=charts.posture_bands(res.route_log)),
                width="stretch")

        st.subheader("Year-by-year return")
        st.plotly_chart(charts.yearly_bars(rep.yearly_return, percent=True), width="stretch")

        st.subheader("Trade blotter (option legs)")
        display = rep.blotter.copy()
        for col in ("opened", "closed", "expiry"):
            display[col] = pd.to_datetime(display[col]).dt.strftime("%Y-%m-%d").replace("NaT", "")
        st.dataframe(labels.humanize(display), width="stretch", hide_index=True)
