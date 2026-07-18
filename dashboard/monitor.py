"""Live paper-trading monitor -- TOS-style. Reads the bot's store in data/live/
(state, trades, snapshots, config) and marks open positions against REAL Schwab
option quotes on an auto-refresh, so unrealized P&L moves during market hours.

  .venv-live/bin/python -m streamlit run dashboard/app.py

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

from live.accounts import CAPITALS, NS, cap_label, account_paths

_CSS = """<style>
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
</style>"""


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


def _pull_marks(positions, timeout=8.0):
    """{ticker: mark} from live Schwab quotes; {} if closed/unauth/failed. Runs
    the network call in a worker thread with a hard timeout so a hung Schwab
    request (token refresh, closed market) can NEVER block the render thread --
    that hang was what blanked the page. Called only on an explicit button press,
    never from the auto-refresh path."""
    import concurrent.futures as _cf

    def _work():
        from live.marks import live_marks
        return live_marks(_client(), positions)
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_work).result(timeout=timeout)
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


def board(paths, capital, n, label, refresh="15s"):
    state = _load_json(Path(paths["state"]), {"cash": float(capital), "positions": []})
    snaps = _load_jsonl(Path(paths["snapshots"]))
    positions = state.get("positions", [])
    # marks come from session_state, keyed per account (populated by this page's
    # "Pull live quotes" button) -- the render path NEVER hits the network.
    marks = st.session_state.get(f"marks_{label}", {})
    any_live = len(marks) > 0

    rows, equity, total_unreal = _rows_and_equity(state, marks)
    baseline = float(capital)
    prev_equity = snaps[-1].get("equity", baseline) if snaps else baseline
    total_pnl = equity - baseline
    day_pnl = equity - prev_equity

    at = st.session_state.get(f"marks_at_{label}")
    tag = (f'<span class="tag live">● LIVE QUOTES · {at}</span>' if any_live
           else '<span class="tag stale">◌ last recorded marks — press "Pull live quotes"</span>')
    st.markdown(f"### {cap_label(capital)} account · N={n} &nbsp; {tag}", unsafe_allow_html=True)
    st.caption(f"${baseline:,.0f} capital · up to {n} positions · "
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
    trades = _load_jsonl(Path(paths["trades"]))
    if trades:
        td = pd.DataFrame(trades)
        td["date"] = pd.to_datetime(td["date"]).dt.strftime("%Y-%m-%d")
        show = td[["date", "action", "ticker", "strike", "right", "contracts", "price"]]
        st.dataframe(show.iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.caption("No trades yet.")


def capital_page(capital):
    """One dashboard page for a capital level: an N dropdown (1-5) picks which of
    that capital's 5 accounts to show; the board renders it live."""
    c = st.columns([1, 1, 1.4])
    n = c[0].selectbox("N (positions at once)", NS, key=f"n_{capital}")
    refresh = c[1].selectbox("Auto-refresh", ["5s", "15s", "30s", "60s"],
                             index=1, key=f"r_{capital}") or "15s"
    label = f"{cap_label(capital)}_N{n}"
    paths = account_paths(capital, n)
    with c[2]:
        st.write("")  # align button with the dropdowns
        # Live-quote pull: the ONLY network hit, timeout-guarded, on button press.
        if st.button("⟳ Pull live quotes", key=f"pull_{label}", use_container_width=True):
            state = _load_json(Path(paths["state"]), {"positions": []})
            with st.spinner("Pulling Schwab quotes…"):
                marks = _pull_marks(state.get("positions", []))
            st.session_state[f"marks_{label}"] = marks
            st.session_state[f"marks_at_{label}"] = pd.Timestamp.now().strftime("%H:%M:%S")
            if not marks:
                st.warning("No quotes (market closed or token expired).")
    st.fragment(run_every=refresh)(board)(paths, capital, n, label, refresh)


def main():
    """Streamlit entry -- MUST be called on every run (app.py calls it; a cached
    import would render once then blank). One page per capital level; each page's
    N dropdown selects the account. set_page_config + CSS re-apply every rerun."""
    st.set_page_config(page_title="Wheel Bot — Live", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    st.sidebar.caption("25 paper accounts = 5 capitals x N 1-5. Quotes live only in "
                       "market hours (Mon–Fri 9:30–4 ET); boards auto-refresh from disk.")

    def _page_fn(cap):
        def render():
            capital_page(cap)
        render.__name__ = f"acct_{cap_label(cap)}"
        return render

    pages = [st.Page(_page_fn(cap), title=f"{cap_label(cap)} account",
                     url_path=f"acct_{cap_label(cap)}") for cap in CAPITALS]
    st.navigation(pages).run()


# Direct entry: `streamlit run dashboard/live.py`. When app.py is the entry it
# imports and calls main() itself. A plain `import` (unit tests) does neither,
# so the money-math helpers above import without a Streamlit runtime.
if __name__ == "__main__":
    main()
