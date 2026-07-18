"""Live paper-trading monitor -- TOS-style. Reads the bot's store in data/live/
(state, trades, snapshots, config) and marks open positions against REAL Schwab
option quotes on an auto-refresh, so unrealized P&L moves during market hours.

  .venv-live/bin/python -m streamlit run dashboard/live.py

Data-only: it pulls quotes to VALUE positions, never places an order. When the
market is closed or a quote pull fails, it degrades to the last recorded mark
and flags the price stale -- it never invents a price."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DATA = Path("data/live")
STATE, TRADES, SNAPS, CONFIG = (DATA / "state.json", DATA / "trades.jsonl",
                                DATA / "snapshots.jsonl", DATA / "config.json")

st.set_page_config(page_title="Wheel Bot — Live", layout="wide",
                   initial_sidebar_state="collapsed")

# --- TOS-ish dark skin ---
st.markdown("""<style>
  .stApp { background:#0b0e11; color:#d1d4dc; }
  .block-container { padding-top:1.2rem; max-width:1500px; }
  h1,h2,h3 { color:#e6e9ef; font-weight:600; }
  [data-testid="stMetricValue"] { font-variant-numeric:tabular-nums;
     font-family:'SF Mono',Menlo,monospace; font-size:1.6rem; }
  .kpi { font-family:'SF Mono',Menlo,monospace; }
  .pos { color:#26a69a; } .neg { color:#ef5350; }
  .tag { padding:2px 9px; border-radius:4px; font-size:.72rem; font-weight:600;
     font-family:'SF Mono',Menlo,monospace; }
  .live { background:#0d3b2e; color:#26a69a; } .stale { background:#33261a; color:#e0a458; }
  table { font-family:'SF Mono',Menlo,monospace !important; font-size:.86rem; }
</style>""", unsafe_allow_html=True)


def _load_json(p, default):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default


def _load_jsonl(p):
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


@st.cache_resource
def _client():
    sys.path.insert(0, "scripts/schwab")
    from schwab_client import get_client
    return get_client()


def _pull_marks(positions):
    """{ticker: mark} from live Schwab quotes; {} if closed/unauth/failed."""
    try:
        from live.marks import live_marks
        return live_marks(_client(), positions)
    except Exception:
        return {}


def _fmt(x, dollars=True):
    s = f"${abs(x):,.2f}" if dollars else f"{abs(x):,.2f}"
    return ("-" + s) if x < 0 else s


def _rows_and_equity(state, marks):
    """Per-position rows + (equity, total unrealized). Short-put liability lowers
    equity; assigned shares add spot*qty. Live mark when present, else last mark."""
    cash = state.get("cash", 0.0)
    rows, equity_positions, total_unreal = [], 0.0, 0.0
    for p in state.get("positions", []):
        tk, phase = p["ticker"], p.get("phase", "?")
        short = p.get("short")
        live = tk in marks
        if short:
            k = short["contracts"]
            credit = short["credit"]
            mark = marks.get(tk, short.get("last_mid", credit))
            collected = credit * 100 * k
            liability = mark * 100 * k
            unreal = collected - liability            # sold at credit, buy back at mark
            equity_positions -= liability             # short leg is owed
            detail = f"{short['contract']['strike']:g}{short['contract']['right']} x{k}"
            markcol = mark
        else:                                         # bare shares (rare in current state)
            sh = p.get("shares", 0)
            basis = p.get("basis", 0.0)
            spot = p.get("last_spot", basis)
            unreal = (spot - basis) * sh
            equity_positions += spot * sh
            detail = f"{sh} sh @ {basis:g}"
            markcol = spot
            credit = 0.0
        total_unreal += unreal
        rows.append({"Ticker": tk, "Phase": phase, "Position": detail,
                     "Mark": markcol, "Unreal P&L": unreal,
                     "_live": live, "_credit": credit})
    return rows, cash + equity_positions, total_unreal


def _pnl_html(x):
    cls = "pos" if x >= 0 else "neg"
    return f'<span class="{cls}">{_fmt(x)}</span>'


def board(refresh="15s"):
    state = _load_json(STATE, {"cash": 0.0, "positions": []})
    cfg = _load_json(CONFIG, {"n": 5, "capital": 100000})
    snaps = _load_jsonl(SNAPS)
    positions = state.get("positions", [])
    marks = _pull_marks(positions) if positions else {}
    any_live = len(marks) > 0

    rows, equity, total_unreal = _rows_and_equity(state, marks)
    baseline = float(cfg.get("capital", 100000))
    prev_equity = snaps[-1].get("equity", baseline) if snaps else baseline
    total_pnl = equity - baseline
    day_pnl = equity - prev_equity

    tag = ('<span class="tag live">● LIVE QUOTES</span>' if any_live
           else '<span class="tag stale">◌ MARKET CLOSED — last marks</span>')
    st.markdown(f"### Wheel Bot — Live Paper &nbsp; {tag}", unsafe_allow_html=True)
    st.caption(f"N={cfg.get('n')} slots · start ${baseline:,.0f} · "
               f"{len(positions)} open · updates every {refresh}")

    c = st.columns(4)
    c[0].metric("Equity", _fmt(equity))
    c[1].metric("Total P&L", _fmt(total_pnl), delta=f"{total_pnl/baseline*100:+.2f}%")
    c[2].metric("Open unrealized", _fmt(total_unreal))
    c[3].metric("Cash", _fmt(state.get("cash", 0.0)))

    st.markdown("#### Positions")
    if rows:
        df = pd.DataFrame(rows)
        disp = pd.DataFrame({
            "Ticker": df["Ticker"], "Phase": df["Phase"], "Position": df["Position"],
            "Mark": df["Mark"].map(lambda v: f"{v:.2f}"),
            "": df["_live"].map(lambda b: "live" if b else "stale"),
            "Unreal P&L": df["Unreal P&L"].map(_pnl_html),
        })
        st.markdown(disp.to_html(escape=False, index=False), unsafe_allow_html=True)
    else:
        st.info("No open positions. The bot opens them on the next 5pm run.")

    st.markdown("#### Equity curve")
    if snaps:
        sd = pd.DataFrame(snaps)
        fig = go.Figure(go.Scatter(x=pd.to_datetime(sd["date"]), y=sd["equity"],
                                   mode="lines+markers", line=dict(color="#26a69a")))
        fig.add_hline(y=baseline, line=dict(color="#555", dash="dot"))
        fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                          paper_bgcolor="#0b0e11", plot_bgcolor="#0b0e11",
                          font=dict(color="#8b93a7"), xaxis=dict(gridcolor="#1c2230"),
                          yaxis=dict(gridcolor="#1c2230", tickprefix="$"))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Equity curve fills in one point per trading day.")

    st.markdown("#### Trades")
    trades = _load_jsonl(TRADES)
    if trades:
        td = pd.DataFrame(trades)
        td["date"] = pd.to_datetime(td["date"]).dt.strftime("%Y-%m-%d")
        show = td[["date", "action", "ticker", "strike", "right", "contracts", "price"]]
        st.dataframe(show.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.caption("No trades yet.")


def _render():
    # refresh control -- redefines the fragment's interval when changed
    opt = st.sidebar.selectbox("Refresh", ["5s", "15s", "30s", "60s"], index=1) or "15s"
    st.sidebar.caption("Quotes are live only during market hours (Mon–Fri 9:30–4 ET). "
                       "Off-hours the P&L holds at the last recorded mark.")
    st.fragment(run_every=opt)(board)(refresh=opt)


# render under `streamlit run`; stay silent on a plain import (so the money-math
# helpers above can be imported and unit-tested without a Streamlit runtime).
try:
    from streamlit.runtime import exists as _rt_exists
    _in_runtime = _rt_exists()
except Exception:
    _in_runtime = False
if _in_runtime:
    _render()
