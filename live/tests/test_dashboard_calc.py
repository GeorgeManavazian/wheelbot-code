"""Money-math for the live dashboard: _rows_and_equity must reproduce the same
short-put equity the engine records (cash minus the short-leg liability)."""
from dashboard.monitor import _rows_and_equity


def test_short_put_equity_matches_engine_convention():
    # sold a put for 0.20 credit x10; now marked 0.30 -> liability grew, equity down
    state = {"cash": 100_200.0, "positions": [
        {"ticker": "GDX", "phase": "PUT", "short": {
            "contracts": 10, "credit": 0.20, "last_mid": 0.30,
            "contract": {"root": "GDX", "expiry": "2026-08-15", "strike": 40.0, "right": "P"}}}]}
    rows, equity, unreal = _rows_and_equity(state, marks={})   # no live -> uses last_mid 0.30
    # liability = 0.30*100*10 = 300 ; equity = cash - liability
    assert equity == 100_200.0 - 300.0
    # unrealized = (credit - mark)*100*contracts = (0.20-0.30)*1000 = -100
    assert round(unreal, 2) == -100.0
    assert rows[0]["_live"] is False


def test_live_mark_overrides_last_mid_and_flags_live():
    state = {"cash": 100_000.0, "positions": [
        {"ticker": "BA", "phase": "PUT", "short": {
            "contracts": 1, "credit": 3.45, "last_mid": 3.45,
            "contract": {"root": "BA", "expiry": "2026-08-01", "strike": 205.0, "right": "P"}}}]}
    rows, equity, unreal = _rows_and_equity(state, marks={"BA": 2.00})
    assert rows[0]["_live"] is True
    assert equity == 100_000.0 - 2.00 * 100          # live mark used, not last_mid
    assert round(unreal, 2) == round((3.45 - 2.00) * 100, 2)   # +145 profit


def test_assigned_shares_use_spot():
    state = {"cash": 50_000.0, "positions": [
        {"ticker": "F", "phase": "SHARES", "shares": 500, "basis": 12.0,
         "last_spot": 13.0, "short": None}]}
    rows, equity, unreal = _rows_and_equity(state, marks={})
    assert equity == 50_000.0 + 13.0 * 500           # cash + share value
    assert unreal == (13.0 - 12.0) * 500


def test_stored_fallback_prefers_the_ask_over_the_mid():
    """The engine marks equity at the ask (owner decision B). If the dashboard
    kept falling back to last_mid, the page would show a more flattering number
    than the account it is displaying -- the exact gap the change removed."""
    state = {"cash": 100_000.0, "positions": [
        {"ticker": "GDX", "phase": "PUT", "short": {
            "contracts": 10, "credit": 0.20, "last_mid": 0.30, "last_ask": 0.34,
            "contract": {"root": "GDX", "expiry": "2026-08-15", "strike": 40.0, "right": "P"}}}]}
    rows, equity, unreal = _rows_and_equity(state, marks={})
    assert equity == 100_000.0 - 0.34 * 100 * 10
    assert round(unreal, 2) == round((0.20 - 0.34) * 1000, 2)
