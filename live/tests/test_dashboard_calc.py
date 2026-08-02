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


def test_missing_spot_renders_no_distance_not_minus_100pct(tmp_path):
    """C17: HAL/WBD carried last_spot=0.0 and the dashboard printed -100%
    "ITM" -- a fabricated max-risk signal from a missing input. No spot ->
    no distance (None, the same sentinel bare-shares rows already use),
    never an arithmetic artifact."""
    for spot_variant in ({"last_spot": 0.0}, {}):
        state = {"cash": 100_000.0, "positions": [
            {"ticker": "HAL", "phase": "PUT", **spot_variant, "short": {
                "contracts": 1, "credit": 0.50, "last_mid": 0.50,
                "contract": {"root": "HAL", "expiry": "2026-08-15",
                             "strike": 20.0, "right": "P"}}}]}
        rows, _e, _u = _rows_and_equity(state, marks={})
        assert rows[0]["_dist"] is None, \
            f"C17: {spot_variant} rendered dist {rows[0]['_dist']}, not None"


def test_covered_position_adds_share_value():
    """C9 (CRIT): a covered position -- shares AND a short call -- must value
    the shares. The short branch subtracted the leg liability and dropped
    spot*shares entirely: a $10k covered lot rendered as a bare -$150
    liability. Engine convention (E1 pin): equity = cash + shares*spot -
    ask*100*k."""
    state = {"cash": 5_000.0, "positions": [
        {"ticker": "GDX", "phase": "CALL", "shares": 100, "basis": 30.0,
         "last_spot": 32.0, "short": {
             "contracts": 1, "credit": 0.80, "last_mid": 1.40, "last_ask": 1.50,
             "contract": {"root": "GDX", "expiry": "2026-08-21",
                          "strike": 33.0, "right": "C"}}}]}
    rows, equity, _u = _rows_and_equity(state, marks={})
    assert equity == 5_000.0 + 100 * 32.0 - 1.50 * 100, \
        "C9: covered shares are missing from dashboard equity"


def test_covered_position_without_spot_falls_back_to_basis():
    """C9 x C17: covered lot with no stored spot values shares at basis
    (the bare-shares branch's existing convention), never at zero."""
    state = {"cash": 5_000.0, "positions": [
        {"ticker": "WBD", "phase": "CALL", "shares": 100, "basis": 11.0,
         "last_spot": 0.0, "short": {
             "contracts": 1, "credit": 0.30, "last_mid": 0.30, "last_ask": 0.35,
             "contract": {"root": "WBD", "expiry": "2026-08-21",
                          "strike": 12.0, "right": "C"}}}]}
    rows, equity, _u = _rows_and_equity(state, marks={})
    assert equity == 5_000.0 + 100 * 11.0 - 0.35 * 100


def test_naked_days_line_surfaces_only_when_nonzero():
    """C16: the counter had zero live readers. Nonzero -> a line naming the
    count; zero/absent -> None (no noise on healthy accounts)."""
    from dashboard.monitor import naked_days_line
    assert naked_days_line({"days_shares_uncovered": 0}) is None
    assert naked_days_line({}) is None
    line = naked_days_line({"days_shares_uncovered": 7})
    assert line is not None and "7" in line and "UNCOVERED" in line


def test_board_actually_renders_the_naked_days_line():
    """Wiring pin (streamlit board() needs a browser harness): the board must
    call naked_days_line and pass a nonzero result to st.warning."""
    import re
    src = open("dashboard/monitor.py").read()
    assert re.search(r'naked = naked_days_line\(state\)\s+if naked:\s+'
                     r'st\.warning\(naked\)', src), \
        "C16: naked_days_line is not wired into board()"


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
