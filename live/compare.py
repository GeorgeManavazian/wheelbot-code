"""Cross-account comparison for the 25 paper accounts (5 capitals x N 1-5).

`account_metrics` reads each account's on-disk store (state.json / trades.jsonl /
snapshots.jsonl under data/live/accounts/<label>/) and returns one plain dict of
summary numbers per account, in `all_accounts()` order. It is PURE and import-safe
(no Streamlit, no network): the dashboard's Compare page renders whatever it
returns, and the unit tests call it directly against a tmp store.

Everything degrades gracefully: a missing account dir, a missing file, or an empty
file yields the account's baseline (equity == its capital, 0 trades, no drawdown),
never an exception.
"""
from __future__ import annotations

from live.paths import in_state
from live.accounts import ACCOUNTS_ROOT

import json
import os

import pandas as pd

from live.accounts import all_accounts, account_label
# Reuse the backtest's drawdown; Sharpe is computed HERE (C4) because the
# backtest helper hardcodes ppy=252 on any daily index and subtracts no rf --
# right for dense backtest curves, wrong for the live store's gapped one.
from src.engine_v2.backtest.metrics_simple import max_drawdown

# C4: Sharpe honesty. 11.39-from-7-observations (SE 6.7, every CI straddling
# zero) is noise wearing a suit -- suppressed below MIN_SHARPE_OBS, shown with
# +/- one standard error (Lo 2002), risk-free subtracted, annualised by the
# ACTUAL observation frequency of the (deduped, capital-prepended) index.
MIN_SHARPE_OBS = 30           # owner D-C4a default
RISK_FREE_ANNUAL = 0.04       # owner D-C4b default; 0.0 => information ratio


def _sharpe_stats(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    n = len(r)
    if n < 2:
        return {"sharpe": None, "sharpe_se": None, "sharpe_n": n,
                "sharpe_suppressed": True}
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    ppy = n / years if years > 0 else 252.0
    rf_p = (1.0 + RISK_FREE_ANNUAL) ** (1.0 / ppy) - 1.0
    ex = r - rf_p
    sd = float(ex.std())
    sr_p = 0.0 if sd < 1e-10 else float(ex.mean() / sd)
    return {"sharpe": sr_p * ppy ** 0.5,
            "sharpe_se": ((1.0 + 0.5 * sr_p ** 2) / n) ** 0.5 * ppy ** 0.5,
            "sharpe_n": n,
            "sharpe_suppressed": n < MIN_SHARPE_OBS}


def _read_jsonl(path: str) -> list[dict]:
    """Every JSON line in a .jsonl file; [] if the file is missing or empty."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        return json.loads(open(path).read())
    except (json.JSONDecodeError, ValueError):
        return default


def _equity_series(snaps: list[dict], capital=None) -> pd.Series:
    """Chronological equity curve indexed by snapshot date. Empty if no snaps.
    C12: LAST row per date wins -- the retry-window era left duplicate
    2026-07-24 rows in every account, and a duplicated index double-counts
    that session in every stat downstream (returns, drawdown, Sharpe ppy).
    C10: with `capital`, a synthetic day-0 row at the starting capital is
    prepended (strictly BEFORE the first real date, or C12's dedupe would eat
    the real row) -- day-one mark losses were invisible to drawdown (first
    snapshot 99,671.90 on 100k capital rendered Max DD 0.00%) and to Sharpe."""
    if not snaps:
        return pd.Series(dtype=float)
    dates = pd.to_datetime([s.get("date") for s in snaps])
    eq = pd.Series([float(s.get("equity", 0.0)) for s in snaps], index=dates)
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()
    if capital is not None and len(eq):
        day0 = pd.Series([float(capital)],
                         index=[eq.index[0] - pd.Timedelta(days=1)])
        eq = pd.concat([day0, eq])
    return eq


def _campaign_stats(trades: list[dict], state: dict, capital: float) -> dict:
    """C5: win rate on REALIZED CASH, per (ticker, campaign). The old rule
    ("closed without assignment = win") was structurally 100% under instant
    fills -- CLOSE_PUT at a loss counted as a win. Realized cash = sum of
    cash_after deltas (rows are append-ordered; cash_yield=0 live, so a delta
    is exactly that trade's cash incl. commission and fees, assignment debits
    and called-away credits included -- they are ledger rows too). A campaign
    with a position still in state is OPEN: neither win nor loss (an ASSIGNED
    campaign holding shares under the basis floor is unfinished, not lost)."""
    open_keys = {(p["ticker"], p.get("campaign"))
                 for p in state.get("positions", [])}
    pnl, prev = {}, float(capital)
    for t in trades:
        ca = t.get("cash_after")
        if ca is None:
            continue    # pre-ledger-era row: unjudgeable, skipped (declared)
        k = (t.get("ticker"), t.get("campaign"))
        pnl[k] = pnl.get(k, 0.0) + (float(ca) - prev)
        prev = float(ca)
    closed = {k: v for k, v in pnl.items() if k not in open_keys}
    wins = sum(1 for v in closed.values() if v > 0.0)
    return {"wins": wins, "losses": len(closed) - wins,
            "open_campaigns": len(open_keys),
            "win_rate": (wins / len(closed)) if closed else None}


def _one_account(capital: int, n: int, accounts_root: str) -> dict:
    label = account_label(capital, n)
    d = os.path.join(accounts_root, label)
    state = _read_json(os.path.join(d, "state.json"), {}) or {}
    trades = _read_jsonl(os.path.join(d, "trades.jsonl"))
    snaps = _read_jsonl(os.path.join(d, "snapshots.jsonl"))

    eq = _equity_series(snaps, capital=capital)   # C10: day 0 = capital
    equity = float(eq.iloc[-1]) if len(eq) else float(capital)
    total_return_pct = (equity / capital - 1.0) * 100.0 if capital else 0.0

    # Drawdown / Sharpe need >= 2 points to mean anything.
    if len(eq) >= 2:
        mdd = max_drawdown(eq)
        max_drawdown_pct = 0.0 if pd.isna(mdd) else float(mdd) * 100.0
        ss = _sharpe_stats(eq)                     # C4
    else:
        max_drawdown_pct = 0.0
        ss = {"sharpe": None, "sharpe_se": None, "sharpe_n": 0,
              "sharpe_suppressed": True}

    cs = _campaign_stats(trades, state, float(capital))   # C5
    return {
        "label": label,
        "capital": capital,
        "n": n,
        "equity": equity,
        "total_return_pct": total_return_pct,
        "n_trades": len(trades),
        "open_positions": len(state.get("positions", [])),
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe": ss["sharpe"], "sharpe_se": ss["sharpe_se"],
        "sharpe_n": ss["sharpe_n"],
        "sharpe_suppressed": ss["sharpe_suppressed"],
        "win_rate": cs["win_rate"], "wins": cs["wins"],
        "losses": cs["losses"], "open_campaigns": cs["open_campaigns"],
        # C8: when this account's numbers were taken (board pages may be newer)
        "last_snap_date": (str(eq.index[-1].date()) if len(eq) else None),
    }


def account_metrics(accounts_root: str = None) -> list[dict]:
    """One summary dict per account, all 25, in `all_accounts()` order.

    `accounts_root` locates the per-account stores; it is joined with each
    account's label (`<accounts_root>/<label>/{state.json,trades.jsonl,
    snapshots.jsonl}`). Missing dirs/files yield baseline defaults, never a crash.

    Each dict has: label, capital, n, equity, total_return_pct, n_trades,
    open_positions, max_drawdown_pct, sharpe, win_rate. See `_win_rate` for the
    win-rate definition.
    """
    if accounts_root is None:
        accounts_root = ACCOUNTS_ROOT
    return [_one_account(capital, n, accounts_root)
            for capital, n in all_accounts()]


def grid_concentration(accounts_root: str = None) -> dict:
    """C7: the three honesty numbers for the compare-grid header. 25 accounts
    are ~19 correlated positions, not 25 samples: the audit measured an
    effective sample of 2.09 (mean pairwise rho 0.458), 145 SELL rows folding
    to 20 distinct decisions, 55.4% of realized P&L on one underlying."""
    if accounts_root is None:
        accounts_root = ACCOUNTS_ROOT
    decisions, n_sell = set(), 0
    premium = {}
    curves = []
    for capital, n in all_accounts():
        d = os.path.join(accounts_root, account_label(capital, n))
        trades = _read_jsonl(os.path.join(d, "trades.jsonl"))
        for t in trades:
            if str(t.get("action", "")).startswith("SELL_"):
                n_sell += 1
                decisions.add((str(t.get("date"))[:10], t.get("ticker"),
                               t.get("action"), t.get("strike"),
                               str(t.get("expiry"))[:10]))
                prem = (float(t.get("price", 0.0)) * 100.0
                        * float(t.get("contracts", 0)))
                premium[t.get("ticker")] = premium.get(t.get("ticker"), 0.0) + prem
        eq = _equity_series(_read_jsonl(os.path.join(d, "snapshots.jsonl")))
        r = eq.pct_change().dropna()
        if len(r) >= 3:
            curves.append(r)
    n_eff, rho_bar = None, None
    if len(curves) >= 2:
        cors = []
        for i in range(len(curves)):
            for j in range(i + 1, len(curves)):
                a, b = curves[i].align(curves[j], join="inner")
                if len(a) >= 3 and a.std() > 0 and b.std() > 0:
                    cors.append(float(a.corr(b)))
        if cors:
            rho_bar = sum(cors) / len(cors)
            k = len(curves)
            # clamp: a negative rho_bar must not inflate N_eff above N
            n_eff = k / (1.0 + (k - 1) * max(rho_bar, 0.0))
    total_prem = sum(premium.values())
    top = max(premium, key=premium.get) if premium else None
    return {"n_decisions": len(decisions), "n_sell_rows": n_sell,
            "replication": (n_sell / len(decisions)) if decisions else None,
            "rho_bar": rho_bar, "n_eff": n_eff, "top_ticker": top,
            "top_premium_share": (premium[top] / total_prem
                                  if top and total_prem else None)}


def benchmark_series(root: str = None) -> dict:
    """C6: buy-and-hold yardsticks over the benchmark-store window (SPY +
    every name any account ever traded; one row per stepped day, written by
    run_daily from the same closes pull the decisions used). Coverage begins
    at the store's FIRST write -- day 1 post-reset -- there is no backfill.
    Returns window returns in PERCENT, or {} when the store is missing or
    holds fewer than two distinct days. The render MUST carry the
    capital-matching caveat: the benchmark is 100% invested, the wheel sits
    mostly in cash."""
    from live.paths import in_state
    path = os.path.join(root, "benchmark.jsonl") if root \
        else in_state("benchmark.jsonl")
    rows = _read_jsonl(path)
    if not rows:
        return {}
    latest = {}
    for r in rows:
        if isinstance(r, dict) and "date" in r:
            latest[str(r["date"])[:10]] = r.get("closes", {})
    days = sorted(latest)
    if len(days) < 2:
        return {}
    first, last = latest[days[0]], latest[days[-1]]
    out = {"window": (days[0], days[-1])}
    if first.get("SPY") and last.get("SPY"):
        out["spy_pct"] = (last["SPY"] / first["SPY"] - 1.0) * 100.0
    names = [t for t in first if t != "SPY"
             and first.get(t) and last.get(t)]
    if names:
        out["ew_pct"] = (sum(last[t] / first[t] for t in names) / len(names)
                        - 1.0) * 100.0
        out["ew_names"] = len(names)
    return out
