"""Wheel backtest page: pick data + config, run, see report (metrics, equity vs
SPY, year-by-year, wheel stats, trade log). Thin view over wheel_report."""
import os
import pandas as pd
import streamlit as st
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import wheel_report, spy_buy_hold, position_log

FIXTURE = "fixtures/spy_wheel_cycle.parquet"
FULL = "data/options/spy_greeks_eod_all.parquet"

def render():
    st.title("Wheel backtest")
    sources = {"Sample cycle (2024 fixture)": FIXTURE}
    if os.path.exists(FULL):
        sources["Full SPY history"] = FULL

    # Load-config-back from the History tab: stage widget defaults once, then
    # pop so the staged values don't stick past this run.
    _load = st.session_state.pop("_load_cfg", None)
    if _load:
        if _load.get("data_source") in sources:
            st.session_state["wheel_data"] = _load["data_source"]
        if "put_delta" in _load:
            st.session_state["w_pd"] = float(_load["put_delta"])
        if "call_delta" in _load:
            st.session_state["w_cd"] = float(_load["call_delta"])
        if "dte_min" in _load:
            st.session_state["w_dmin"] = int(_load["dte_min"])
        if "dte_max" in _load:
            st.session_state["w_dmax"] = int(_load["dte_max"])
        if "take_profit" in _load:
            st.session_state["w_tp"] = _load["take_profit"]
        if "capital" in _load:
            st.session_state["w_cap"] = int(_load["capital"])

    src_name = st.selectbox("Data", list(sources), key="wheel_data")
    path = sources[src_name]
    if not os.path.exists(path):
        st.warning(f"Data not found: {path}. Build it first."); st.stop()
    ch = pd.read_parquet(path)

    min_d, max_d = ch["date"].min().date(), ch["date"].max().date()
    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="wheel_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="wheel_end")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.30, 0.05, key="w_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.30, 0.05, key="w_cd")
    dte_min = c2.number_input("DTE min", 0, 90, 25, key="w_dmin")
    dte_max = c2.number_input("DTE max", 1, 120, 45, key="w_dmax")
    tp = c3.selectbox("Take-profit", ["50%", "25%", "75%", "Hold to expiry"], key="w_tp")
    tp_map = {"50%": 0.50, "25%": 0.25, "75%": 0.75, "Hold to expiry": None}
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="w_cap")
    intraday_on = st.checkbox("Intraday take-profit (hourly)", key="intraday_tp")
    INTRA = "fixtures/spy_wheel_intraday_sample.parquet"

    if st.button("Run", key="run_wheel", type="primary"):
        ch = ch[(ch["date"] >= pd.Timestamp(start)) & (ch["date"] <= pd.Timestamp(end))]
        if ch["date"].nunique() < 5:
            st.warning("Window too short."); st.stop()
        cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                          call_delta=call_delta, dte_min=int(dte_min), dte_max=int(dte_max),
                          take_profit_pct=tp_map[tp])
        if intraday_on and os.path.exists(INTRA):
            from src.engine_v2.options.intraday import run_wheel_intraday
            res = run_wheel_intraday(ch, cfg, pd.read_parquet(INTRA))
        else:
            res = run_wheel(ch, cfg)
            if intraday_on:
                st.info("No intraday sample for this dataset — ran EOD.")
        rep = wheel_report(res, ch, cfg)
        pnl = res.equity.iloc[-1] - cfg.starting_capital
        a, b, c = st.columns(3)
        a.metric("P&L", f"${pnl:,.0f}", f"{rep.metrics['total_return']:+.2%}")
        b.metric("Sharpe", f"{rep.metrics['sharpe']:.2f}")
        c.metric("Max drawdown", f"{rep.metrics['max_drawdown']:.2%}")
        st.caption(f"vs SPY buy-hold: {rep.benchmark['total_return']:+.2%}"
                   f"  ·  over {len(res.equity)} trading days"
                   f"  ·  Sharpe {rep.metrics['sharpe']:.2f}")

        curve = res.equity.rename("wheel").to_frame()
        curve["spy_buy_hold"] = spy_buy_hold(ch, cfg.starting_capital).reindex(res.equity.index).ffill()
        st.subheader("Equity vs SPY buy-hold"); st.line_chart(curve)
        st.subheader("Year-by-year return"); st.bar_chart(rep.yearly_return)

        s = rep.stats
        st.subheader("Wheel stats")
        st.write(f"Puts sold **{s['n_puts_sold']}** · calls **{s['n_calls_sold']}** · "
                 f"assignments **{s['n_assignments']}** (rate {s['assignment_rate']:.0%}) · "
                 f"take-profits **{s['n_take_profits']}** · net premium **{s['net_premium']:.0f}** · "
                 f"commission {s['commission_paid']:.0f}")
        st.subheader("Trade blotter")
        blotter = position_log(res, cfg)
        display = blotter.copy()
        for col in ("opened", "closed", "expiry"):
            display[col] = pd.to_datetime(display[col]).dt.strftime("%Y-%m-%d").replace("NaT", "")
        for col in ("credit", "cost_to_close", "realized_pnl"):
            display[col] = display[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        display["pct_of_credit"] = display["pct_of_credit"].map(
            lambda x: f"{x:+.1%}" if pd.notna(x) else "")
        st.dataframe(display, use_container_width=True)

        from dashboard.wheel_history import log_run
        log_run({
            "ts": pd.Timestamp.now().isoformat(timespec="seconds"),
            "data_source": src_name,
            "start": pd.Timestamp(start).date().isoformat(),
            "end": pd.Timestamp(end).date().isoformat(),
            "put_delta": put_delta,
            "call_delta": call_delta,
            "dte_min": int(dte_min),
            "dte_max": int(dte_max),
            "take_profit": tp,
            "capital": capital,
            "intraday": bool(intraday_on),
            "total_return": rep.metrics["total_return"],
            "max_drawdown": rep.metrics["max_drawdown"],
            "sharpe": rep.metrics["sharpe"],
            "pnl": float(pnl),
            "n_trades": len(res.trades),
            "n_assignments": rep.stats["n_assignments"],
        })
