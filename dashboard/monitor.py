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
from live.compare import account_metrics

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
        from live.marks import live_asks
        return live_asks(_client(), positions)
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
            c_ = short["contract"]
            strike, right = float(c_["strike"]), c_["right"]
            k = short["contracts"]
            credit = short["credit"]
            # The ASK, to match how the engine marks equity (owner decision B,
            # 2026-07-31): a short leg is closed by buying, so the offer is the
            # only price the account can actually get out at. Falling back to
            # last_mid here would print a friendlier number than the account it
            # is describing. `credit` remains the last resort for a leg that has
            # never been marked at all.
            mark = marks.get(tk, short.get("last_ask", short.get("last_mid", credit)))
            collected = credit * 100 * k              # premium collected on this leg
            liability = mark * 100 * k
            unreal = collected - liability            # sold at credit, buy back at mark
            equity_positions -= liability             # short leg is owed
            spot = p.get("last_spot", 0.0)
            # C9: a COVERED position carries shares alongside the short call --
            # they were silently worth $0 here (a $10k covered lot rendered as
            # a bare -$150 liability). Value them like the bare-shares branch:
            # spot, falling back to basis when no spot is stored (C17 class).
            sh = p.get("shares", 0)
            if sh:
                equity_positions += sh * (spot if spot > 0
                                          else (p.get("basis") or 0.0))
            dte = (pd.Timestamp(c_["expiry"]).normalize() - pd.Timestamp.now().normalize()).days
            # put: spot above strike = OTM cushion (>0 safe); below = ITM (assignment risk)
            # C17: no stored spot (0.0 / absent, the HAL/WBD class) -> no
            # distance -- None, the sentinel bare-shares rows already use.
            # 0/strike - 1 printed a fabricated "-100% ITM" max-risk signal.
            dist = (spot / strike - 1.0) if (strike and spot > 0) else None
            row = {"Ticker": tk, "Phase": phase, "Strike": f"{strike:g}", "Right": right,
                   "Contracts": k, "_premium": collected, "DTE": dte, "_dist": dist,
                   "_mark": mark, "Unreal P&L": unreal, "_live": live,
                   # C1: when the stored mark was taken; None on pre-C1 legs
                   "_asof": short.get("mark_asof")}
        else:                                         # bare shares (post-assignment)
            sh = p.get("shares", 0)
            basis = p.get("basis", 0.0)
            spot = p.get("last_spot", basis)
            unreal = (spot - basis) * sh
            equity_positions += spot * sh
            row = {"Ticker": tk, "Phase": phase, "Strike": f"{basis:g}", "Right": "shares",
                   "Contracts": sh, "_premium": p.get("premium", 0.0), "DTE": "—",
                   "_dist": None, "_mark": spot, "Unreal P&L": unreal, "_live": live,
                   "_asof": None}
        total_unreal += unreal
        rows.append(row)
    return rows, cash + equity_positions, total_unreal


def naked_days_line(state) -> str | None:
    """C16: `days_shares_uncovered` was incremented and persisted with ZERO
    live readers -- shares could sit unrented for weeks (SLV backtest: 295
    gated days) and no page ever said so. One line, only when nonzero."""
    d = state.get("days_shares_uncovered", 0)
    if not d:
        return None
    return (f"shares have sat UNCOVERED (no covered call) for {d} "
            f"session{'s' if d != 1 else ''} over this account's life -- "
            f"if it is climbing daily, the call is being refused (A3b/A4) "
            f"or deferred; see the run log")


def _pnl_html(x):
    cls = "pos" if x >= 0 else "neg"
    return f'<span class="{cls}">{_fmt(x)}</span>'


def split_gap_records(records):
    """C2/C3: the two disclosure classes, from the folded ledger RECORDS
    (reasons included), not the bare dates. The old banner asserted 'never
    traded, cannot be backfilled' for every date -- false for 45.5% of
    realized P&L (07-23 closed the DOW campaign for $25,616 while labelled
    no_run; 07-24 opened WMT in 19 accounts off stale prices while labelled
    missed). Class split: a plain `no_run` really is a hole; anything with a
    correction or another reason DID have activity and carries that reason
    as its caveat."""
    full_miss, degraded = [], []
    for r in records:
        reason = str(r.get("reason", "?"))
        if r.get("correction") is True or reason != "no_run":
            degraded.append((str(r["date"]), reason))
        else:
            full_miss.append((str(r["date"]), reason))
    return full_miss, degraded


def gap_banner():
    """Show missed/degraded trading days at the top of every page.

    The bot records them in gaps.jsonl, but until this existed nothing read
    that file -- and until C2/C3 the banner rendered only the DATES with a
    blanket 'never traded' claim the ledger's own reasons contradicted."""
    from live.gaps import gap_summary
    g = gap_summary()
    if not g["count"]:
        return
    full_miss, degraded = split_gap_records(g["records"])
    parts = []
    if full_miss:
        parts.append(
            f"<b>&#9888; {len(full_miss)} day(s) with no bot activity</b> "
            f"&mdash; {', '.join(d for d, _ in full_miss)}"
            f"<br><span style='color:#c9b184;'>Nothing ran; these cannot be "
            f"backfilled (Schwab has no historical chain endpoint). The "
            f"equity curve has holes on these dates.</span>")
    if degraded:
        rows = ", ".join(f"{d} ({why})" for d, why in degraded)
        parts.append(
            f"<b>&#9888; {len(degraded)} degraded day(s)</b> &mdash; {rows}"
            f"<br><span style='color:#c9b184;'>The bot DID act on these days "
            f"-- the reason above is the caveat its numbers carry (partial "
            f"run, stale prices, or a corrected record). Do not read them as "
            f"clean sessions or as holes.</span>")
    st.markdown(
        f"<div style='background:#3a2a0e;border:1px solid #8a6d1f;border-radius:6px;"
        f"padding:10px 14px;margin-bottom:14px;color:#f0d999;font-size:.86rem;'>"
        + "<br>".join(parts) + "</div>",
        unsafe_allow_html=True)


def concentration_line() -> str:
    """C7: the compare grid's honesty header. 25 accounts copy each other's
    homework -- the audit measured an effective sample of 2.09. Always
    rendered; a young store says so instead of going silently blank."""
    from live.compare import grid_concentration
    g = grid_concentration()
    if g["n_eff"] is None:
        return ("<div style='color:#e6b45a;font-size:.9rem;'>&#9888; too few "
                "observations to estimate account independence — do not read "
                "25 rows as 25 samples</div>")
    top = (f" · top underlying {g['top_ticker']} = "
           f"{g['top_premium_share']*100:.0f}% of premium collected"
           if g["top_ticker"] else "")
    return (f"<div style='color:#e6b45a;font-size:.9rem;'>&#9888; 25 accounts "
            f"&ne; 25 samples — effective N &asymp; {g['n_eff']:.1f} (mean "
            f"pairwise &rho; {g['rho_bar']:.2f}) · {g['n_decisions']} distinct "
            f"decisions replicated {g['replication']:.1f}&times;{top}</div>")


def benchmark_line(rows_hint=None) -> str | None:
    """C6: the buy-and-hold yardstick, with the MANDATORY capital-matching
    caveat -- the benchmark is 100% invested, the wheel sits mostly in cash."""
    from live.compare import benchmark_series
    b = benchmark_series()
    if not b:
        return None
    bits = [f"window {b['window'][0]} → {b['window'][1]}"]
    if "spy_pct" in b:
        bits.append(f"SPY buy-and-hold {b['spy_pct']:+.2f}%")
    if "ew_pct" in b:
        # F3: the basket is names present at BOTH window ends -- a ticker
        # first traded mid-window (or dropped from the pull) is not in it,
        # and the copy must say so rather than imply "everything traded".
        bits.append(f"equal-weight of {b['ew_names']} names held at both "
                    f"window ends {b['ew_pct']:+.2f}%")
    return ("<div style='color:#9db4d0;font-size:.9rem;'>Benchmark: "
            + " · ".join(bits)
            + " — <b>caution:</b> benchmark is 100% invested; the wheel sits "
              "mostly in cash (vol-unmatched, not like-for-like).</div>")


def mark_basis_line(snaps) -> str | None:
    """C11: the equity curve confesses its own pricing basis. Uniform
    stamped -> one quiet word. Unstamped (pre-C11) rows have an UNKNOWN
    basis -- they span the mid era AND the post-2026-07-31 ask era before
    stamping existed -- so the line says unknown, never guesses "mid"
    (skeptic F2: the old render claimed a mid-series changeover on stores
    where nothing changed, and mislabeled the boundary). A change between
    two STAMPED rows is named at the exact row it happened."""
    if not snaps:
        return None
    stamped = [s.get("mark_basis") for s in snaps if s.get("mark_basis")]
    n_unstamped = len(snaps) - len(stamped)
    if n_unstamped == 0 and len(set(stamped)) == 1:
        return f"equity marked at {stamped[0]}"
    bits = []
    if n_unstamped:
        bits.append(f"{n_unstamped} pre-stamp row(s) of unknown mark basis "
                    f"(the mid → ask changeover 2026-07-31 predates stamping)")
    prev, change = None, None
    for s in snaps:
        b = s.get("mark_basis")
        if b and prev and b != prev:
            change = (str(s.get("date"))[:10], prev, b)
            break
        if b:
            prev = b
    if change:
        bits.append(f"mark basis changed {change[0]}: "
                    f"{change[1]} → {change[2]}")
    if not bits:
        return f"equity marked at {stamped[0]}" if stamped else None
    return ("; ".join(bits)
            + " — early and late equity are not like-for-like")


def fmt_sharpe(r) -> str:
    """C4 cell: suppressed below MIN_SHARPE_OBS (em dash WITH the n, so the
    owner sees why), else value ± one standard error. pd.isna, never
    `is None` -- the compare grid's DataFrame coerces None to NaN (skeptic
    F1), and a ≥30-obs curve whose equity touched 0 yields NaN outright
    (F6): both must render as suppressed, never "nan ± nan"."""
    if pd.isna(r["sharpe"]) or r["sharpe_suppressed"]:
        return f"— (n={r['sharpe_n']})"
    return f"{r['sharpe']:.2f} ± {r['sharpe_se']:.2f}"


def fmt_win(r) -> str:
    """C5 cell: realized-cash record, wins/losses shown. pd.isna for the
    same F1 reason -- a young account's None win rate arrives here as NaN
    whenever any other account has closed campaigns."""
    if pd.isna(r["win_rate"]):
        return "—"
    return f"{r['win_rate']*100:.0f}% ({r['wins']}W/{r['losses']}L)"


def dedupe_snaps(snaps):
    """C12: keep the LAST snapshot per date. The retry-window bug era left
    duplicate 2026-07-24 rows in every account; a duplicated index double-
    counts sessions in every downstream stat and kinks the equity curve."""
    latest = {}
    for s in snaps:
        if isinstance(s, dict) and "date" in s:
            latest[str(s["date"])[:10]] = s
    return [latest[k] for k in sorted(latest)]


def board(paths, capital, n, label, refresh="15s"):
    state = _load_json(Path(paths["state"]), {"cash": float(capital), "positions": []})
    snaps = dedupe_snaps(_load_jsonl(Path(paths["snapshots"])))   # C12
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
    gap_banner()
    st.markdown(f"### {cap_label(capital)} account · N={n} &nbsp; {tag}", unsafe_allow_html=True)
    st.caption(f"${baseline:,.0f} capital · up to {n} positions · "
               f"{len(positions)} open · updates every {refresh}")
    # C8: two clocks, both stamped -- board reads state.json (17:00 write,
    # possibly re-marked intraday), compare reads snapshots; a difference
    # between the pages is timing, not contradiction.
    try:
        import datetime as _dt
        mt = _dt.datetime.fromtimestamp(Path(paths["state"]).stat().st_mtime)
        state_stamp = mt.strftime("%Y-%m-%d %H:%M")
    except OSError:
        state_stamp = "—"
    last_snap = str(snaps[-1].get("date"))[:10] if snaps else "—"
    basis = mark_basis_line(snaps)   # C11
    st.caption(f"state.json written {state_stamp} · last snapshot {last_snap}"
               + (f" · {basis}" if basis else ""))
    naked = naked_days_line(state)
    if naked:
        st.warning(naked)   # C16

    total_premium = sum(r["_premium"] for r in rows)
    c = st.columns(5)
    c[0].metric("Equity", _fmt(equity))
    c[1].metric("Total P&L", _fmt(total_pnl), delta=f"{total_pnl/baseline*100:+.2f}%")
    c[2].metric("Premium collected", _fmt(total_premium))
    c[3].metric("Open unrealized", _fmt(total_unreal))
    c[4].metric("Cash", _fmt(state.get("cash", 0.0)))

    st.markdown("#### Positions")
    if rows:
        df = pd.DataFrame(rows)

        def _dist(v):
            return "—" if v is None else f'<span class="{"pos" if v >= 0 else "neg"}">{v:+.1%}</span>'
        disp = pd.DataFrame({
            "Ticker": df["Ticker"], "Phase": df["Phase"],
            "Strike": df["Strike"], "Right": df["Right"], "Contracts": df["Contracts"],
            "Premium": df["_premium"].map(_fmt),
            "DTE": df["DTE"],
            "vs strike": df["_dist"].map(_dist),
            "Mark": df["_mark"].map(lambda v: f"{v:.2f}"),
            # C1: the carried-mark discriminator, rendered. A mark whose
            # as-of predates today is a CARRIED price (the $11.30-TMO class);
            # legs marked today (or live-pulled) show clean.
            "Mark as-of": df.apply(
                lambda r: ('<span class="tag live">live</span>' if r["_live"]
                           else "—" if r["_asof"] in (None, "")
                           or str(r["_asof"]) >= str(pd.Timestamp.now().date())
                           else f'<span class="neg">carried {r["_asof"]}</span>'),
                axis=1),
            "": df["_live"].map(lambda b: "live" if b else "stale"),
            "Unreal P&L": df["Unreal P&L"].map(_pnl_html),
        })
        # TOTAL footer row — premium + unrealized P&L summed (recomputes each render)
        total_row = {c: "" for c in disp.columns}
        total_row["Ticker"] = "<b>TOTAL</b>"
        total_row["Premium"] = f"<b>{_fmt(total_premium)}</b>"
        total_row["Unreal P&L"] = f"<b>{_pnl_html(total_unreal)}</b>"
        disp = pd.concat([disp, pd.DataFrame([total_row])], ignore_index=True)
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
        # EOD trades are midnight-normalized; intraday closes carry microseconds --
        # ISO8601 parses both (a bare to_datetime infers one format and crashes on the other)
        td["date"] = pd.to_datetime(td["date"], format="ISO8601").dt.strftime("%Y-%m-%d")
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


def _return_html(x):
    """Signed % return, green/red, matching the board's P&L colouring."""
    cls = "pos" if x >= 0 else "neg"
    return f'<span class="{cls}">{x:+.2f}%</span>'


def compare_page():
    """One table across ALL 25 accounts (5 capitals x N 1-5) from the on-disk
    stores -- equity, return, trades, open positions, drawdown, Sharpe, win rate.
    Pure disk read (account_metrics); no network. Empty accounts show at baseline
    (equity == capital) until data accumulates."""
    gap_banner()
    st.markdown("### Compare · all 25 accounts")
    st.markdown(concentration_line(), unsafe_allow_html=True)   # C7
    bench = benchmark_line(rows_hint=None)                      # C6
    if bench:
        st.markdown(bench, unsafe_allow_html=True)
    st.caption("Capital x N grid. Equity/return from the last daily snapshot "
               "(baseline = starting capital until snapshots accrue). Win = "
               "campaign's realized cash > $0, all fills, commissions and "
               "fees, from the trades ledger; open campaigns excluded and "
               "counted separately (C5). Sharpe hidden below "
               "30 observations; shown ± one standard error, rf 4.0%/yr "
               "subtracted, annualised by actual observation frequency (C4). "
               "Drawdown measured from starting capital (C10).")

    rows = account_metrics()
    df = pd.DataFrame(rows)
    snap_dates = [r["last_snap_date"] for r in rows if r["last_snap_date"]]
    if snap_dates:
        # C8: when these numbers were taken -- a board page reads state.json
        # and may be newer; a difference is two clocks, not a contradiction
        st.caption(f"Snapshots as-of {max(snap_dates)} (oldest account "
                   f"{min(snap_dates)}) — board pages read state.json and "
                   f"may be newer.")

    disp = pd.DataFrame({
        "Account": df["label"],
        "Capital": df["capital"].map(lambda v: f"${v:,.0f}"),
        "N": df["n"],
        "Equity": df["equity"].map(lambda v: _fmt(v)),
        "Return": df["total_return_pct"].map(_return_html),
        "Trades": df["n_trades"],
        "Open": df["open_positions"],
        "Max DD": df["max_drawdown_pct"].map(lambda v: f"{v:.2f}%"),
        # C4/C5 cells go through the NaN-safe formatters (skeptic F1: the
        # DataFrame coerces None -> NaN in a float64 column, so an `is None`
        # guard here is dead code and the real store rendered "nan% (0W/0L)")
        "Sharpe": df.apply(fmt_sharpe, axis=1),
        "Win rate": df.apply(fmt_win, axis=1),
        "Camp. open": df["open_campaigns"],
    })
    st.markdown(disp.to_html(escape=False, index=False), unsafe_allow_html=True)


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
    pages.append(st.Page(compare_page, title="Compare", url_path="compare"))
    st.navigation(pages).run()


# Direct entry: `streamlit run dashboard/live.py`. When app.py is the entry it
# imports and calls main() itself. A plain `import` (unit tests) does neither,
# so the money-math helpers above import without a Streamlit runtime.
if __name__ == "__main__":
    main()
