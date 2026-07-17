"""Universal wheel: the chop-scanner rotation bot. No ticker picker — it scans
the fixed 9-ticker universe and rents the plain+basis wheel on whatever is in
good chop weather, N at a time. Replaces the single-ticker wheel page."""
import pandas as pd
import streamlit as st
from dashboard import charts, labels
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.select import derived_band
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                             DEFAULT_CLEAN_START)
from src.engine_v2.options.report import portfolio_campaign_table, portfolio_summary_stats
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for
from src.engine_v2.backtest import metrics_simple as m

UNIVERSE = list(ROTATION_TIE_ORDER)


@st.cache_data(ttl=3600, show_spinner=False)
def _load():
    chains = {}
    for t in UNIVERSE:
        ch = pd.read_parquet(chain_path(t)); ch["date"] = pd.to_datetime(ch["date"])
        chains[t] = ch
    states = {t: regime_series(closes_for(t)) for t in UNIVERSE}
    return chains, states


def render():
    st.title("Universal wheel — chop scanner")
    st.caption("Scans the whole universe and rents the plain+basis wheel on "
               "whatever is in good chop weather (range-bound, not stressed), "
               "N tickers at a time. Exploration only.")
    st.markdown("**Universe (fixed):** " + " · ".join(UNIVERSE))

    try:
        chains, states = _load()
    except FileNotFoundError:
        st.warning("Missing options data for the universe. Pull the chains first.")
        st.stop()
    all_dates = sorted({d for t in UNIVERSE for d in chains[t]["date"]})
    min_d, max_d = all_dates[0].date(), all_dates[-1].date()

    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="uw_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="uw_end")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.20, 0.05, key="uw_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.50, 0.05, key="uw_cd")
    target_dte = c2.number_input("Target DTE", 5, 60, 7, key="uw_tdte")
    band_lo, band_hi = derived_band(int(target_dte))
    c2.caption(f"trades expiries {band_lo}–{band_hi} days out")
    tp = c3.slider("Take-profit (% of credit)", 1, 100, 50, key="uw_tp")
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="uw_cap")
    n_slots = st.slider("How many tickers at once (N)", 1, len(UNIVERSE), 5, key="uw_n")

    if pd.Timestamp(start) < DEFAULT_CLEAN_START["XOP"]:
        st.caption("Note: XOP is split-broken before 2020-07-01; the engine clean-starts "
                   "it at 2020-07-01 automatically.")

    if st.button("Run", key="run_uw", type="primary"):
        w = {t: chains[t][(chains[t]["date"] >= pd.Timestamp(start))
                          & (chains[t]["date"] <= pd.Timestamp(end))] for t in UNIVERSE}
        if len({d for t in UNIVERSE for d in w[t]["date"].unique()}) < 5:
            st.warning("Window too short."); st.stop()
        cfg = WheelConfig(ticker="SPY", put_delta=put_delta, call_delta=call_delta,
                          target_dte=int(target_dte), take_profit_pct=tp / 100.0,
                          starting_capital=float(capital), call_min_strike="basis")
        res = run_portfolio_wheel(w, cfg, states, selector="chop", n_slots=int(n_slots))
        last_spots = {t: float(w[t].groupby("date")["underlying"].first().iloc[-1])
                      for t in UNIVERSE if len(w[t])}
        st.session_state["_uw"] = (res, cfg, last_spots)

    stashed = st.session_state.get("_uw")
    if stashed is None:
        return
    res, cfg, last_spots = stashed
    ct = portfolio_campaign_table(res, cfg, last_spots)
    s = portfolio_summary_stats(ct)
    pnl = res.equity.iloc[-1] - cfg.starting_capital
    idle = res.days_flat / len(res.equity) if len(res.equity) else 0.0

    a, b, c, d = st.columns(4)
    a.metric("P&L", f"${pnl:,.0f}", f"{res.equity.iloc[-1]/cfg.starting_capital-1:+.1%}")
    b.metric("Finished-rentals win %",
             "—" if pd.isna(s["finished_win_rate"]) else f"{s['finished_win_rate']:.0%}")
    c.metric("Sold-today win %",
             "—" if pd.isna(s["soldtoday_win_rate"]) else f"{s['soldtoday_win_rate']:.0%}")
    d.metric("Avg % per win",
             "—" if pd.isna(s["avg_pct_per_win"]) else f"{s['avg_pct_per_win']:+.2%}")
    st.caption("The gap between the two win rates is money hidden in shares the bot "
               "is still holding — if they're equal, nothing's hidden. "
               f"({s['n_open']} campaigns still open.)")

    e, f, g, h = st.columns(4)
    e.metric("# campaigns", f"{res.n_campaigns_opened:,}")
    f.metric("# trades", f"{len(res.trades):,}")
    g.metric("Max drawdown", f"{m.max_drawdown(res.equity):.1%}")
    h.metric("Idle % (cash)", f"{idle:.0%}")

    st.subheader("Account value")
    st.plotly_chart(charts.equity_curve(res.equity, {}), width="stretch")

    st.subheader("Rentals by ticker")
    per = ct.groupby("ticker").size().sort_values(ascending=False)
    st.dataframe(per.rename("campaigns").reset_index(), width="stretch", hide_index=True)

    st.subheader("Campaign blotter")
    disp = ct.sort_values("opened", ascending=False).copy()
    for col in ("opened", "closed"):
        disp[col] = pd.to_datetime(disp[col]).dt.strftime("%Y-%m-%d")
    disp["pnl_realized"] = disp["pnl_realized"].map(lambda x: f"${x:,.0f}")
    disp["pct_return"] = disp["pct_return"].map(lambda x: f"{x:+.1%}")
    disp["status"] = disp["open_at_end"].map(lambda o: "open (holding shares)" if o else "closed")
    st.dataframe(labels.humanize(disp[["ticker", "opened", "closed", "n_trades",
                 "pnl_realized", "pct_return", "status"]]),
                 width="stretch", hide_index=True)
