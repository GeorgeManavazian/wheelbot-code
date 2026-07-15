"""Wheel backtest page: pick ticker + config, run, see report. Thin view over
wheel_report, built to the 2026-07-12 wheel engine API contract."""
import os
from dataclasses import replace
import pandas as pd
import streamlit as st
from dashboard import charts, labels, theme
from src.engine_v2.options.data import available_tickers, chain_path, intraday_path
from src.engine_v2.options.select import derived_band
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import wheel_report

FIXTURE_TICKER = "SPY (2024 sample fixture)"
FIXTURE = "fixtures/spy_wheel_cycle.parquet"

# Pre-registered unseen basket tickers (amendment 2026-07-13d, binding): the
# dashboard must not offer them until the basket run reports — one dropdown
# click here would burn the out-of-sample set. Same guard as the runner scripts.
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"}

# XOP's chain is unadjusted through its 1:4 reverse split (2020-03-31) —
# results that span it book phantom gains (STATUS 2026-07-14).
XOP_CLEAN_START = pd.Timestamp("2020-07-01")


def _sources() -> dict:
    """Ticker -> EOD chain path. Seen tickers only + fixture fallback."""
    out = {t: chain_path(t) for t in available_tickers() if t not in UNSEEN}
    if os.path.exists(FIXTURE):
        out[FIXTURE_TICKER] = FIXTURE
    return out


def render():
    st.title("Wheel backtest")
    sources = _sources()
    if not sources:
        st.warning("No options data found under data/options/. Pull a chain first.")
        st.stop()

    # Load-config-back from the History tab: stage widget defaults once, then
    # pop so the staged values don't stick past this run. Old history rows may
    # lack target_dte (pre-contract runs logged dte_min/dte_max) — tolerate.
    _load = st.session_state.pop("_load_cfg", None)
    if _load:
        if _load.get("data_source") in sources:
            st.session_state["wheel_data"] = _load["data_source"]
        if "put_delta" in _load:
            st.session_state["w_pd"] = float(_load["put_delta"])
        if "call_delta" in _load:
            st.session_state["w_cd"] = float(_load["call_delta"])
        if "target_dte" in _load and pd.notna(_load.get("target_dte")):
            st.session_state["w_tdte"] = int(_load["target_dte"])
        if "take_profit" in _load and pd.notna(_load.get("take_profit")):
            try:
                st.session_state["w_tp"] = int(float(_load["take_profit"]))
            except (TypeError, ValueError):
                pass  # pre-contract rows stored "50%" strings; skip them
        if "capital" in _load:
            st.session_state["w_cap"] = int(_load["capital"])
        if _load.get("start"):
            st.session_state["wheel_start"] = pd.Timestamp(_load["start"]).date()
        if _load.get("end"):
            st.session_state["wheel_end"] = pd.Timestamp(_load["end"]).date()

    ticker_name = st.selectbox(
        "Ticker", list(sources), key="wheel_data",
        help="Unseen basket tickers (XBI EEM EWZ TLT ARKK QQQ) are hidden until "
             "the pre-registered basket run reports — amendment 2026-07-13d.")
    path = sources[ticker_name]
    ticker = "SPY" if ticker_name == FIXTURE_TICKER else ticker_name
    if not os.path.exists(path):
        st.warning(f"Data not found: {path}. Build it first."); st.stop()
    ch = pd.read_parquet(path)

    min_d, max_d = ch["date"].min().date(), ch["date"].max().date()
    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="wheel_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="wheel_end")
    if ticker == "XOP" and pd.Timestamp(start) < XOP_CLEAN_START:
        st.warning("XOP's chain is split-broken before 2020-07-01 (unadjusted 1:4 "
                   "reverse split 2020-03-31): any window spanning it books phantom "
                   "gains. Numbers from this window are provenance-only.")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.20, 0.05, key="w_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.20, 0.05, key="w_cd")
    target_dte = c2.number_input("Target DTE", 5, 60, 7, key="w_tdte")
    band_lo, band_hi = derived_band(int(target_dte))
    c2.caption(f"trades expiries {band_lo}–{band_hi} days out")
    tp_slider = c3.slider("Take-profit (% of credit)", 1, 100, 50, key="w_tp")
    if tp_slider == 100:
        c3.caption("100% = hold to expiry")
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="w_cap")

    # Per-ticker hourly OHLC; the pull is still in flight for some tickers.
    intra = None if ticker_name == FIXTURE_TICKER else intraday_path(ticker)
    if ticker_name == FIXTURE_TICKER:
        INTRA_FIXTURE = "fixtures/spy_wheel_intraday_sample.parquet"
        intra = INTRA_FIXTURE if os.path.exists(INTRA_FIXTURE) else None
    intraday_on = st.checkbox(
        "Intraday take-profit (hourly)", key="intraday_tp",
        disabled=intra is None,
        help=None if intra else "No hourly OHLC on disk for this ticker yet.")

    if st.button("Run", key="run_wheel", type="primary"):
        ch = ch[(ch["date"] >= pd.Timestamp(start)) & (ch["date"] <= pd.Timestamp(end))]
        if ch["date"].nunique() < 5:
            st.warning("Window too short."); st.stop()
        cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                          call_delta=call_delta, target_dte=int(target_dte),
                          take_profit_pct=tp_slider / 100.0, ticker=ticker,
                          call_min_strike="basis")
        plain_cfg = replace(cfg, call_min_strike=None)
        if intraday_on and intra:
            from src.engine_v2.options.intraday import run_wheel_intraday
            intra_df = pd.read_parquet(intra)
            res_basis = run_wheel_intraday(ch, cfg, intra_df, regime_states=None)
            res_plain = run_wheel_intraday(ch, plain_cfg, intra_df, regime_states=None)
        else:
            res_basis = run_wheel(ch, cfg, regime_states=None)
            res_plain = run_wheel(ch, plain_cfg, regime_states=None)
        rep_basis = wheel_report(res_basis, ch, cfg)
        rep_plain = wheel_report(res_plain, ch, plain_cfg)
        st.session_state["_wheel_result"] = (res_basis, rep_basis, res_plain, rep_plain, cfg, ch)

        from dashboard.wheel_history import log_run
        pnl = res_basis.equity.iloc[-1] - cfg.starting_capital
        log_run({
            "ts": pd.Timestamp.now().isoformat(timespec="seconds"),
            "data_source": ticker_name,
            "start": pd.Timestamp(start).date().isoformat(),
            "end": pd.Timestamp(end).date().isoformat(),
            "put_delta": put_delta,
            "call_delta": call_delta,
            "target_dte": int(target_dte),
            "take_profit": tp_slider,
            "capital": capital,
            "intraday": bool(intraday_on),
            "defense": "basis",
            "total_return": rep_basis.metrics["total_return"],
            "max_drawdown": rep_basis.metrics["max_drawdown"],
            "sharpe": rep_basis.metrics["sharpe"],
            "pnl": float(pnl),
            "n_trades": len(res_basis.trades),
            "n_assignments": rep_basis.stats["n_assignments"],
            "plain_total_return": rep_plain.metrics["total_return"],
        })

    stashed = st.session_state.get("_wheel_result")
    if stashed is not None:
        res, rep, res_plain, rep_plain, cfg, ch = stashed
        pnl = res.equity.iloc[-1] - cfg.starting_capital

        rows = {}
        def _arm_row(equity, m):
            return {"P&L": float(equity.iloc[-1] - cfg.starting_capital),
                    "Return": m["total_return"], "Sharpe": m["sharpe"],
                    "MaxDD": m["max_drawdown"]}
        rows["Basis wheel"] = _arm_row(res.equity, rep.metrics)
        rows["Plain wheel"] = _arm_row(res_plain.equity, rep_plain.metrics)
        bu = rep.benchmark_underlying
        rows[f"Buy-hold {rep.ticker}"] = {"P&L": None, "Return": bu["total_return"],
                                          "Sharpe": None, "MaxDD": bu.get("max_drawdown")}
        if rep.benchmark_spy is not None and rep.ticker != "SPY":
            bs = rep.benchmark_spy
            rows["Buy-hold SPY"] = {"P&L": None, "Return": bs["total_return"],
                                    "Sharpe": None, "MaxDD": bs.get("max_drawdown")}
        cmp_df = pd.DataFrame.from_dict(rows, orient="index")
        st.subheader("Basis wheel vs plain wheel")
        st.dataframe(cmp_df.style.format({
            "P&L": lambda x: "" if pd.isna(x) else f"${x:,.0f}",
            "Return": lambda x: "" if pd.isna(x) else f"{x:+.2%}",
            "Sharpe": lambda x: "" if pd.isna(x) else f"{x:.2f}",
            "MaxDD": lambda x: "" if pd.isna(x) else f"{x:.2%}"}), width="stretch")

        a, b, c, d = st.columns(4)
        a.metric("P&L", f"${pnl:,.0f}", f"{rep.metrics['total_return']:+.2%}")
        b.metric("Sharpe", f"{rep.metrics['sharpe']:.2f}")
        c.metric("Max drawdown", f"{rep.metrics['max_drawdown']:.2%}")
        # Honest cross-ticker benchmark: real SPY, not the traded underlying.
        if rep.benchmark_spy is not None:
            gap = rep.metrics["total_return"] - rep.benchmark_spy["total_return"]
            d.metric("vs SPY buy & hold", f"{gap:+.2%}",
                     help=f"Buy-hold real SPY returned "
                          f"{rep.benchmark_spy['total_return']:+.2%} over this window.")
        else:
            d.metric("vs SPY buy & hold", "—",
                     help="No SPY chain on disk to benchmark against.")
        st.caption(
            f"vs buy-hold {rep.ticker}: {rep.benchmark_underlying['total_return']:+.2%}"
            f"  ·  over {len(res.equity)} trading days")

        # Days flat changes what the benchmark comparison even means — don't bury.
        s = rep.stats
        if s["n_days_flat"]:
            st.warning(f"Flat {s['n_days_flat']} days ({s['pct_days_flat']:.0%} of the "
                       f"window) — no expiry inside the {band_lo}–{band_hi} DTE band. "
                       f"The benchmark held {rep.ticker} on those days; the bot held cash.")

        # Defense diagnostics — the campaign-level answer to "did the defense
        # help", visible wherever the defense is chosen.
        if getattr(rep, "defense", None):
            dd = rep.defense
            st.subheader("Defense stats (campaign-level)")
            wr = dd["campaign_win_rate"]
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Campaigns", dd["n_campaigns"])
            e2.metric("Campaign win rate", "n/a" if pd.isna(wr) else f"{wr:.0%}")
            e3.metric("Rolls", rep.stats["n_rolls"])
            e4.metric("Stops", dd["n_stops"])
            adv = dd["roll_advantage_total"]
            skip = dd.get("roll_counterfactual_skipped", 0)
            st.caption(
                f"Roll counterfactual (short-leg approx): total advantage {adv:+,.0f} "
                f"(helped {dd['roll_advantage_positive']}, hurt {dd['roll_advantage_negative']})"
                + (f" · {skip} leg(s) beyond data window" if skip else "")
                + f" · shares uncovered {dd['days_shares_uncovered']} days"
                + f" · no-mark days {dd['n_no_mark_days']}"
                + f" · late expiry resolutions {dd['n_late_expiries']}")
            if dd["liquidate_fill_note"]:
                st.caption("Liquidation fills at EOD spot — no stock spread/slippage "
                           "modeled (options pay full spread).")

        st.subheader("Equity vs buy & hold")
        bench = {}
        from src.engine_v2.options.report import buy_hold_curve, spy_curve
        bench[rep.ticker] = buy_hold_curve(ch, cfg.starting_capital).reindex(res.equity.index).ffill()
        spy_b = spy_curve(cfg.starting_capital, res.equity.index)
        if spy_b is not None and rep.ticker != "SPY":
            bench["SPY"] = spy_b
        bench["Plain wheel"] = res_plain.equity.reindex(res.equity.index).ffill()
        st.plotly_chart(charts.equity_curve(res.equity, bench), width="stretch")

        st.subheader("Year-by-year return")
        st.plotly_chart(charts.yearly_bars(rep.yearly_return, percent=True),
                        width="stretch")

        st.subheader("Wheel stats")
        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Puts sold", s["n_puts_sold"])
        t1.metric("Calls sold", s["n_calls_sold"])
        t2.metric("Assignments", s["n_assignments"],
                  help=f"rate {s['assignment_rate']:.0%}")
        t2.metric("Called away", s["n_called_away"])
        t3.metric("Take-profits", s["n_take_profits"])
        t3.metric("Net premium", f"${s['net_premium']:,.0f}")
        t4.metric("Days flat", s["n_days_flat"], help=f"{s['pct_days_flat']:.0%} of window")
        t4.metric("Commission", f"${s['commission_paid']:,.0f}")

        # Deterministic selection, proven: DTE at each sale should cluster tight
        # around the target, not spray across a window.
        if len(s["realized_dte"]):
            st.subheader("Realized DTE at sale")
            st.caption("Selection is deterministic now — this should cluster at the target.")
            st.plotly_chart(charts.yearly_bars(s["realized_dte"].sort_index(),
                                               percent=False),
                            width="stretch")

        st.subheader("Trade blotter")
        from src.engine_v2.options.report import position_log
        blotter = position_log(res, cfg)
        display = blotter.copy()
        for col in ("opened", "closed", "expiry"):
            display[col] = pd.to_datetime(display[col]).dt.strftime("%Y-%m-%d").replace("NaT", "")
        for col in ("credit", "cost_to_close", "realized_pnl"):
            display[col] = display[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        display["strike"] = display["strike"].map(
            lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        display["pct_of_credit"] = display["pct_of_credit"].map(
            lambda x: f"{x:+.1%}" if pd.notna(x) else "")
        st.dataframe(_style_blotter(labels.humanize(display), blotter),
                     width="stretch", hide_index=True)


def _hex_tint(hex_color: str, alpha: float) -> str:
    """Faint rgba background from a theme hex constant."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _style_blotter(display: pd.DataFrame, raw: pd.DataFrame):
    """Row tint by outcome (lost money > assigned > kept premium), P&L text
    coloured by sign. Tints derive from the raw frame — the display copy has
    already been formatted to strings."""
    # Opacities tuned for the dark theme: fainter than this and the tint is
    # imperceptible against #0e1117 (first attempt used 0.06 — invisible).
    lose = _hex_tint(theme.NEGATIVE, 0.22)
    warn = _hex_tint(theme.WARNING, 0.20)
    keep = _hex_tint(theme.POSITIVE, 0.14)
    tints = []
    for _, r in raw.iterrows():
        pnl = r["realized_pnl"]
        if pd.notna(pnl) and pnl < 0:
            tints.append(f"background-color: {lose}")
        elif r["outcome"] == "Assigned":
            tints.append(f"background-color: {warn}")
        elif pd.notna(pnl):
            tints.append(f"background-color: {keep}")
        else:
            tints.append("")

    def _rows(col):
        return tints

    def _pnl_colour(col):
        out = []
        for pnl in raw["realized_pnl"]:
            if pd.isna(pnl):
                out.append("")
            else:
                colour = theme.POSITIVE if pnl >= 0 else theme.NEGATIVE
                out.append(f"color: {colour}; font-weight: 600")
        return out

    styled = display.style.apply(_rows, axis=0)
    pnl_col = labels.label("realized_pnl")
    if pnl_col in display.columns:
        styled = styled.apply(_pnl_colour, axis=0, subset=[pnl_col])
    return styled
