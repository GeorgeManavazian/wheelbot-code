"""Regime tab: today's market/ticker state, base rates, wheel autopsy.
Read-only — no knobs, no trading decisions."""
import pandas as pd
import streamlit as st
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series, describe
from src.engine_v2.regime.base_rates import base_rate_table
from src.engine_v2.regime.autopsy import campaign_regimes, autopsy_table
from src.engine_v2.options.data import available_tickers

@st.cache_data(ttl=3600, show_spinner=False)
def _states_for(symbol: str) -> pd.DataFrame:
    """Cached closes+states — render() touches up to 2 symbols in 3 sections;
    without this every Streamlit rerun re-reads the parquet 6 times."""
    return regime_series(closes_for(symbol))

def current_state_lines(symbol: str) -> list:
    states = _states_for(symbol)
    if states.empty:
        return [f"{symbol} — not enough history for a regime state "
                f"(needs ~273 trading days of closes)."]
    row = states.iloc[-1]
    return [f"{symbol} — {describe(row)}",
            f"as of {states.index[-1].date()}"]

@st.cache_data(ttl=3600, show_spinner=False)
def base_rate_display(symbol: str) -> pd.DataFrame:
    return base_rate_table(closes_for(symbol))

def render():
    st.title("Regime")
    st.caption("Read-only advisor: where the market is, what usually followed, "
               "and how the wheel's own campaigns fared by regime.")
    # Unseen basket tickers hidden until the basket run reports (13d discipline,
    # same guard as the Wheel tab) — even read-only surfaces stay consistent.
    UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"}
    tickers = [t for t in available_tickers() if t != "SPY" and t not in UNSEEN]
    tick = st.selectbox("Ticker", ["SPY"] + tickers, key="regime_ticker")

    st.subheader("Today")
    for sym in dict.fromkeys(["SPY", tick]):
        try:
            for line in current_state_lines(sym):
                st.write(line)
        except FileNotFoundError:
            st.warning(f"No daily closes on disk for {sym}.")

    st.subheader("Base rates — next 21 trading days from each state")
    for sym in dict.fromkeys(["SPY", tick]):
        try:
            tbl = base_rate_display(sym)
        except FileNotFoundError:
            st.warning(f"No daily closes on disk for {sym} — base rates skipped.")
            continue
        if tbl.empty:
            st.warning(f"{sym}: not enough history for base rates.")
            continue
        st.write(f"**{sym}**")
        st.dataframe(tbl.style.format({"win_rate": "{:.0%}", "median_fwd": "{:+.2%}",
                                       "mean_fwd": "{:+.2%}", "p5_fwd": "{:+.2%}"}),
                     width="stretch")
        st.caption(tbl.attrs["caveat"])

    stash = st.session_state.get("_wheel_result")
    if stash is not None:
        res, rep, cfg, _ch = stash
        st.subheader(f"Wheel autopsy by regime — last run ({cfg.ticker})")
        try:
            tagged = campaign_regimes(res, cfg, closes_for("SPY"), closes_for(cfg.ticker))
        except FileNotFoundError:
            st.warning("Missing closes for the autopsy."); return
        c1, c2 = st.columns(2)
        for col, by, label in ((c1, "market", "by MARKET (SPY) state"),
                               (c2, "ticker", f"by {cfg.ticker} state")):
            t = autopsy_table(tagged, by=by)
            col.write(f"**{label}**")
            col.dataframe(t.style.format({"win_rate": "{:.0%}", "total_pnl": "{:+,.0f}",
                                          "mean_pnl": "{:+,.0f}"}), width="stretch")
            if t.attrs["n_open_excluded"]:
                col.caption(f"{t.attrs['n_open_excluded']} open campaign(s) excluded.")
    else:
        st.info("Run a wheel backtest first to see the campaign autopsy here.")
