"""Tests for the cross-account comparison view (live/compare.py).

Builds a few fake account stores under a tmp dir, leaves the rest missing, and
checks that account_metrics returns all 25 rows with correct numbers for the
populated ones and graceful baseline defaults for the missing ones.
"""
import json
import os

from live.accounts import all_accounts, account_label
from live.compare import account_metrics


def _write_account(root, capital, n, *, snaps, trades, positions):
    d = os.path.join(root, account_label(capital, n))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "state.json"), "w") as fh:
        json.dump({"cash": float(capital), "positions": positions}, fh)
    with open(os.path.join(d, "snapshots.jsonl"), "w") as fh:
        for s in snaps:
            fh.write(json.dumps(s) + "\n")
    with open(os.path.join(d, "trades.jsonl"), "w") as fh:
        for t in trades:
            fh.write(json.dumps(t) + "\n")


def test_all_25_rows_and_order(tmp_path):
    rows = account_metrics(str(tmp_path))
    assert len(rows) == 25
    assert [r["label"] for r in rows] == [account_label(c, n) for c, n in all_accounts()]


def test_missing_accounts_get_graceful_defaults(tmp_path):
    rows = {r["label"]: r for r in account_metrics(str(tmp_path))}
    # Nothing on disk -> every account is at baseline.
    r = rows["100k_N3"]
    assert r["equity"] == 100_000.0
    assert r["capital"] == 100_000
    assert r["total_return_pct"] == 0.0
    assert r["n_trades"] == 0
    assert r["open_positions"] == 0
    assert r["max_drawdown_pct"] == 0.0
    assert r["sharpe"] is None
    assert r["win_rate"] is None


def test_populated_account_equity_return_trades_drawdown(tmp_path):
    # Equity 100k -> 90k (dip) -> 110k. Last snapshot 110k => +10% return.
    snaps = [
        {"date": "2026-07-13T00:00:00", "equity": 100_000.0, "cash": 100_000.0},
        {"date": "2026-07-14T00:00:00", "equity": 90_000.0, "cash": 90_000.0},
        {"date": "2026-07-15T00:00:00", "equity": 110_000.0, "cash": 110_000.0},
    ]
    trades = [
        {"date": "2026-07-13T00:00:00", "action": "SELL_PUT", "ticker": "BA"},
        {"date": "2026-07-14T00:00:00", "action": "CLOSE_PUT", "ticker": "BA"},
        {"date": "2026-07-15T00:00:00", "action": "SELL_PUT", "ticker": "WFC"},
    ]
    positions = [{"ticker": "WFC", "phase": "PUT"}]
    _write_account(tmp_path, 100_000, 2, snaps=snaps, trades=trades, positions=positions)

    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["100k_N2"]
    assert r["equity"] == 110_000.0
    assert abs(r["total_return_pct"] - 10.0) < 1e-9
    assert r["n_trades"] == 3
    assert r["open_positions"] == 1
    # Peak 100k -> trough 90k = -10% max drawdown.
    assert abs(r["max_drawdown_pct"] - (-10.0)) < 1e-9
    assert r["sharpe"] is not None
    # 1 CLOSE_PUT win, 0 losses -> 100% win rate.
    assert r["win_rate"] == 1.0


def test_win_rate_definition_close_and_expired_vs_assigned(tmp_path):
    # 1 CLOSE_PUT + 1 PUT_EXPIRED (wins) vs 2 ASSIGNED (losses) => 2/4 = 0.5.
    # An open SELL_PUT is ignored (not a closed campaign).
    trades = [
        {"action": "SELL_PUT", "ticker": "A"},
        {"action": "CLOSE_PUT", "ticker": "A"},
        {"action": "SELL_PUT", "ticker": "B"},
        {"action": "PUT_EXPIRED", "ticker": "B"},
        {"action": "SELL_PUT", "ticker": "C"},
        {"action": "ASSIGNED", "ticker": "C"},
        {"action": "SELL_PUT", "ticker": "D"},
        {"action": "ASSIGNED", "ticker": "D"},
        {"action": "SELL_PUT", "ticker": "E"},  # still open, uncounted
    ]
    _write_account(tmp_path, 50_000, 4, snaps=[], trades=trades, positions=[])
    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["50k_N4"]
    assert r["win_rate"] == 0.5
    assert r["n_trades"] == 9
    # No snapshots -> equity falls back to capital, no drawdown/sharpe.
    assert r["equity"] == 50_000.0
    assert r["max_drawdown_pct"] == 0.0
    assert r["sharpe"] is None


def test_single_snapshot_no_drawdown_or_sharpe(tmp_path):
    snaps = [{"date": "2026-07-15T00:00:00", "equity": 5_200.0, "cash": 5_200.0}]
    _write_account(tmp_path, 5_000, 1, snaps=snaps, trades=[], positions=[])
    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["5k_N1"]
    assert r["equity"] == 5_200.0
    assert abs(r["total_return_pct"] - 4.0) < 1e-9
    assert r["max_drawdown_pct"] == 0.0   # need >= 2 points
    assert r["sharpe"] is None
    assert r["win_rate"] is None
