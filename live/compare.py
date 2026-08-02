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
# Reuse the backtest's frequency-aware diagnostics -- one definition of drawdown /
# Sharpe across the whole project rather than a second, drifting copy here.
from src.engine_v2.backtest.metrics_simple import (
    max_drawdown,
    sharpe,
    infer_periods_per_year,
)

# Terminal outcomes of a short-PUT campaign. A campaign "wins" when the put we
# sold is closed for profit (CLOSE_PUT, a take-profit buy-back) or expires
# worthless (PUT_EXPIRED); it "loses" when the stock is put to us (ASSIGNED).
_WIN_ACTIONS = {"CLOSE_PUT", "PUT_EXPIRED"}
_LOSS_ACTIONS = {"ASSIGNED"}


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


def _equity_series(snaps: list[dict]) -> pd.Series:
    """Chronological equity curve indexed by snapshot date. Empty if no snaps.
    C12: LAST row per date wins -- the retry-window era left duplicate
    2026-07-24 rows in every account, and a duplicated index double-counts
    that session in every stat downstream (returns, drawdown, Sharpe ppy)."""
    if not snaps:
        return pd.Series(dtype=float)
    dates = pd.to_datetime([s.get("date") for s in snaps])
    eq = pd.Series([float(s.get("equity", 0.0)) for s in snaps], index=dates)
    eq = eq[~eq.index.duplicated(keep="last")]
    return eq.sort_index()


def _win_rate(trades: list[dict]):
    """Campaign-level win rate = closed put campaigns that ended WITHOUT
    assignment / all closed put campaigns.

        wins   = # CLOSE_PUT  +  # PUT_EXPIRED   (bought back for profit, or expired worthless)
        losses = # ASSIGNED                       (stock put to us)
        win_rate = wins / (wins + losses)

    A put campaign that is still open (only a SELL_PUT so far) is NOT counted.
    Returns None when no put campaign has closed yet, so an empty/young account
    shows "no data" rather than a misleading 0% or 100%.
    """
    wins = sum(1 for t in trades if t.get("action") in _WIN_ACTIONS)
    losses = sum(1 for t in trades if t.get("action") in _LOSS_ACTIONS)
    closed = wins + losses
    if closed == 0:
        return None
    return wins / closed


def _one_account(capital: int, n: int, accounts_root: str) -> dict:
    label = account_label(capital, n)
    d = os.path.join(accounts_root, label)
    state = _read_json(os.path.join(d, "state.json"), {}) or {}
    trades = _read_jsonl(os.path.join(d, "trades.jsonl"))
    snaps = _read_jsonl(os.path.join(d, "snapshots.jsonl"))

    eq = _equity_series(snaps)
    equity = float(eq.iloc[-1]) if len(eq) else float(capital)
    total_return_pct = (equity / capital - 1.0) * 100.0 if capital else 0.0

    # Drawdown / Sharpe need >= 2 points to mean anything.
    if len(eq) >= 2:
        mdd = max_drawdown(eq)
        max_drawdown_pct = 0.0 if pd.isna(mdd) else float(mdd) * 100.0
        ppy = infer_periods_per_year(eq.index)
        shp = sharpe(eq.pct_change(), ppy)
        sharpe_val = None if pd.isna(shp) else float(shp)
    else:
        max_drawdown_pct = 0.0
        sharpe_val = None

    return {
        "label": label,
        "capital": capital,
        "n": n,
        "equity": equity,
        "total_return_pct": total_return_pct,
        "n_trades": len(trades),
        "open_positions": len(state.get("positions", [])),
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe": sharpe_val,
        "win_rate": _win_rate(trades),
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
