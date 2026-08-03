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
    # C5: these rows carry no cash_after (pre-ledger-era shape) -> campaign
    # P&L is unjudgeable and the win rate declares itself unknown rather
    # than inventing the old action-name 100%.
    assert r["win_rate"] is None


def test_win_rate_definition_close_and_expired_vs_assigned(tmp_path):
    # C5 (2026-08-02): the old rule -- "closed without assignment = win" --
    # was structurally 100% under instant fills and branded ASSIGNED a
    # permanent loss despite the basis floor. This fixture re-pinned to the
    # REALIZED-CASH definition: campaign A nets +$60 (win), campaign B nets
    # -$110 (a CLOSE_PUT at a LOSS -- a win under the old rule, red then),
    # campaign C still open in state (neither), campaign D zero rows with
    # cash_after would be skipped (declared pre-ledger-era behavior).
    trades = [
        {"action": "SELL_PUT", "ticker": "A", "campaign": 1, "cash_after": 50_100.0},
        {"action": "CLOSE_PUT", "ticker": "A", "campaign": 1, "cash_after": 50_060.0},
        {"action": "SELL_PUT", "ticker": "B", "campaign": 2, "cash_after": 50_140.0},
        {"action": "CLOSE_PUT", "ticker": "B", "campaign": 2, "cash_after": 49_950.0},
        {"action": "SELL_PUT", "ticker": "E", "campaign": 3, "cash_after": 50_010.0},
    ]
    positions = [{"ticker": "E", "phase": "PUT", "campaign": 3}]
    _write_account(tmp_path, 50_000, 4, snaps=[], trades=trades,
                   positions=positions)
    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["50k_N4"]
    assert (r["wins"], r["losses"], r["open_campaigns"]) == (1, 1, 1)
    assert r["win_rate"] == 0.5
    assert r["n_trades"] == 5
    # No snapshots -> equity falls back to capital, no drawdown/sharpe.
    assert r["equity"] == 50_000.0
    assert r["max_drawdown_pct"] == 0.0
    assert r["sharpe"] is None


def test_c4_sharpe_suppressed_below_30_obs_and_se_matches_lo(tmp_path):
    """C4: 61.14-from-3-returns rendered as a real number at HEAD. Below
    MIN_SHARPE_OBS the row says suppressed; the SE matches Lo (2002)."""
    from live.compare import _sharpe_stats, MIN_SHARPE_OBS
    import pandas as pd
    idx = pd.date_range("2026-07-20", periods=4, freq="D")
    eq = pd.Series([100_000, 101_500, 103_000, 105_000.0], index=idx)
    ss = _sharpe_stats(eq)
    assert ss["sharpe_suppressed"] is True and ss["sharpe_n"] == 3
    # 35 obs: not suppressed; SE = sqrt((1+0.5*sr_p^2)/n)*sqrt(ppy)
    idx = pd.date_range("2026-01-01", periods=36, freq="D")
    eq = pd.Series([100_000 * (1.001 + 0.002 * (i % 2)) ** i
                    for i in range(36)], index=idx)
    ss = _sharpe_stats(eq)
    assert ss["sharpe_suppressed"] is False and ss["sharpe_n"] == 35
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    ppy = 35 / years
    rf_p = 1.04 ** (1 / ppy) - 1
    ex = r - rf_p
    sr_p = float(ex.mean() / ex.std())
    assert ss["sharpe_se"] == ((1 + 0.5 * sr_p ** 2) / 35) ** 0.5 * ppy ** 0.5
    assert ss["sharpe"] == sr_p * ppy ** 0.5


def test_c4_rf_and_calendar_annualisation():
    """A curve earning exactly rf must Sharpe ~0; the same per-period returns
    stretched over twice the calendar shrink the annualised value ~sqrt(2)
    (kills any flat-252 regression)."""
    from live.compare import _sharpe_stats, RISK_FREE_ANNUAL
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=41, freq="D")
    ppy = 40 / ((idx[-1] - idx[0]).days / 365.25)
    rf_p = (1 + RISK_FREE_ANNUAL) ** (1 / ppy) - 1
    # 40 returns alternating rf±0.001: mean excess exactly 0 -> sharpe ~0
    vals, v = [100_000.0], 100_000.0
    for i in range(40):
        v *= (1 + rf_p + (0.001 if i % 2 else -0.001))
        vals.append(v)
    ss = _sharpe_stats(pd.Series(vals, index=idx))
    assert abs(ss["sharpe"]) < 0.2, "C4: rf is not being subtracted"
    vals, v = [100_000.0], 100_000.0
    for i in range(40):
        v *= 1.002 + (0.0005 if i % 2 else -0.0005)
        vals.append(v)
    dense = pd.Series(vals,
                      index=pd.date_range("2026-01-01", periods=41, freq="D"))
    sparse = pd.Series(vals,
                       index=pd.date_range("2026-01-01", periods=41, freq="2D"))
    sd, ssp = _sharpe_stats(dense), _sharpe_stats(sparse)
    assert 0 < ssp["sharpe"] < sd["sharpe"] * 0.8, \
        "C4: annualisation ignores the actual calendar span (flat-252 bug)"


def test_c10_day_one_dip_visible_in_drawdown(tmp_path):
    """C10: an account whose first snapshot sits below capital and only ever
    rises rendered Max DD 0.00% at HEAD -- the day-one mark loss vanished."""
    snaps = [{"date": "2026-07-20T00:00:00", "equity": 99_671.90},
             {"date": "2026-07-21T00:00:00", "equity": 99_800.00},
             {"date": "2026-07-22T00:00:00", "equity": 99_900.00}]
    _write_account(tmp_path, 100_000, 3, snaps=snaps, trades=[], positions=[])
    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["100k_N3"]
    assert r["max_drawdown_pct"] < -0.3, \
        "C10: the day-one dip below capital is invisible to drawdown"
    assert r["last_snap_date"] == "2026-07-22"     # C8 stamp
    # mutant B6: the synthetic day-0 row must sit STRICTLY BEFORE the first
    # real date -- a same-date prepend leaves a duplicated index that
    # double-counts a session in ppy/Sharpe n
    from live.compare import _equity_series
    eq = _equity_series(snaps, capital=100_000)
    assert eq.index.is_unique and eq.index.is_monotonic_increasing
    assert len(eq) == 4


def test_c5_commissions_flip_a_gross_winner(tmp_path):
    """A campaign +$5 gross but negative after commissions must be a LOSS --
    cash_after already nets them, pin it stays that way."""
    from live.compare import _campaign_stats
    trades = [
        {"action": "SELL_PUT", "ticker": "R", "campaign": 1, "cash_after": 10_004.35},
        {"action": "CLOSE_PUT", "ticker": "R", "campaign": 1, "cash_after": 9_999.70},
    ]
    cs = _campaign_stats(trades, {"positions": []}, 10_000.0)
    assert (cs["wins"], cs["losses"]) == (0, 1)


def test_c5_first_campaign_delta_seeds_from_capital():
    """Mutant B5 survived the batch fixtures: seeding prev at 0 makes the
    FIRST campaign absorb the whole starting capital as phantom profit. A
    first campaign that realized -$50 must be a loss."""
    from live.compare import _campaign_stats
    trades = [
        {"action": "SELL_PUT", "ticker": "R", "campaign": 1, "cash_after": 10_100.0},
        {"action": "CLOSE_PUT", "ticker": "R", "campaign": 1, "cash_after": 9_950.0},
    ]
    cs = _campaign_stats(trades, {"positions": []}, 10_000.0)
    assert (cs["wins"], cs["losses"]) == (0, 1), \
        "C5: the first campaign's P&L must seed from CAPITAL, not zero"


def test_c7_identical_accounts_have_effective_n_of_one(tmp_path):
    from live.compare import grid_concentration
    snaps = [{"date": f"2026-07-{d}T00:00:00", "equity": e}
             for d, e in [(20, 100_000.0), (21, 100_400.0), (22, 100_100.0),
                          (23, 100_900.0)]]
    trades = [{"date": "2026-07-20T00:00:00", "action": "SELL_PUT",
               "ticker": "DOW", "strike": 28.0, "expiry": "2026-08-15",
               "price": 0.54, "contracts": 10, "campaign": 1,
               "cash_after": 100_540.0}]
    for n in (1, 2):
        _write_account(tmp_path, 100_000, n, snaps=snaps, trades=trades,
                       positions=[])
    g = grid_concentration(str(tmp_path))
    assert g["n_decisions"] == 1 and g["n_sell_rows"] == 2
    assert g["replication"] == 2.0
    assert g["n_eff"] is not None and abs(g["n_eff"] - 1.0) < 0.05, \
        "C7: two accounts copying homework must count as ~1 sample"
    assert g["top_ticker"] == "DOW" and g["top_premium_share"] == 1.0


def test_c6_benchmark_series_window_returns(tmp_path):
    from live.compare import benchmark_series
    import json as _json
    p = tmp_path / "benchmark.jsonl"
    rows = [{"date": "2026-07-20", "closes": {"SPY": 700.0, "DOW": 30.0}},
            {"date": "2026-07-20", "closes": {"SPY": 701.0, "DOW": 30.1}},
            {"date": "2026-07-27", "closes": {"SPY": 715.0, "DOW": 29.0}}]
    p.write_text("\n".join(_json.dumps(r) for r in rows) + "\n")
    b = benchmark_series(str(tmp_path))
    # C12 idiom inside: the duplicated 07-20 row keeps the LAST close (701)
    assert abs(b["spy_pct"] - (715.0 / 701.0 - 1.0) * 100.0) < 1e-9
    assert abs(b["ew_pct"] - (29.0 / 30.1 - 1.0) * 100.0) < 1e-9
    assert b["window"] == ("2026-07-20", "2026-07-27")
    assert benchmark_series(str(tmp_path / "empty")) == {}


def test_single_snapshot_no_drawdown_or_sharpe(tmp_path):
    snaps = [{"date": "2026-07-15T00:00:00", "equity": 5_200.0, "cash": 5_200.0}]
    _write_account(tmp_path, 5_000, 1, snaps=snaps, trades=[], positions=[])
    r = {row["label"]: row for row in account_metrics(str(tmp_path))}["5k_N1"]
    assert r["equity"] == 5_200.0
    assert abs(r["total_return_pct"] - 4.0) < 1e-9
    assert r["max_drawdown_pct"] == 0.0   # need >= 2 points
    assert r["sharpe"] is None
    assert r["win_rate"] is None


def test_c7_negative_correlation_cannot_inflate_effective_n(tmp_path):
    """Clamp pin: anti-correlated curves give a negative mean pairwise rho;
    unclamped, N_eff = k/(1+(k-1)*rho_bar) explodes past k (or divides by
    zero at rho=-1). Independence can cap at k samples, never exceed it."""
    from live.compare import grid_concentration
    up = [100_000.0, 101_000.0, 100_000.0, 101_000.0, 100_000.0]
    down = [100_000.0, 99_000.0, 100_000.0, 99_000.0, 100_000.0]
    for n, eqs in ((1, up), (2, down)):
        snaps = [{"date": f"2026-07-2{i}T00:00:00", "equity": e}
                 for i, e in enumerate(eqs)]
        _write_account(tmp_path, 100_000, n, snaps=snaps, trades=[],
                       positions=[])
    g = grid_concentration(str(tmp_path))
    assert g["rho_bar"] is not None and g["rho_bar"] < 0
    assert g["n_eff"] is not None and g["n_eff"] <= 2.0 + 1e-9, \
        "C7: negative rho_bar inflated effective N above the account count"
