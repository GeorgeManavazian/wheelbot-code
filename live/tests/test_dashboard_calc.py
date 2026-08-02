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


def test_split_gap_records_two_disclosure_classes():
    """C2/C3: the banner asserted 'never traded' for every gap date -- false
    for 45.5% of realized P&L (07-23 closed DOW for $25,616 while labelled
    no_run in the pre-correction era). Plain no_run = a real hole; corrected
    records and any other reason = the bot DID act, reason rendered."""
    from dashboard.monitor import split_gap_records
    records = [
        {"date": "2026-07-27", "reason": "no_run"},
        {"date": "2026-07-23", "reason": "eod_missed_intraday_ran",
         "correction": True},
        {"date": "2026-07-24", "reason": "stale_price_catchup",
         "correction": True},
    ]
    full_miss, degraded = split_gap_records(records)
    assert full_miss == [("2026-07-27", "no_run")]
    assert ("2026-07-23", "eod_missed_intraday_ran") in degraded
    assert ("2026-07-24", "stale_price_catchup") in degraded
    assert len(degraded) == 2


def test_gap_banner_renders_records_not_just_dates():
    """C3 wiring pin: the banner must consume RECORDS (reasons) and the two
    classes, not g['dates']."""
    import re
    src = open("dashboard/monitor.py").read()
    assert "split_gap_records(g[\"records\"])" in src, \
        "C3: banner does not consume the folded records"
    assert not re.search(r'gap_banner[\s\S]{0,900}g\["dates"\]', src), \
        "C3: banner still renders bare dates"


def test_dedupe_snaps_keeps_last_row_per_date():
    """C12: duplicate 2026-07-24 rows exist in every real account store."""
    from dashboard.monitor import dedupe_snaps
    snaps = [{"date": "2026-07-24", "equity": 100.0},
             {"date": "2026-07-24", "equity": 105.0},
             {"date": "2026-07-25", "equity": 110.0}]
    out = dedupe_snaps(snaps)
    assert [s["equity"] for s in out] == [105.0, 110.0]


def test_board_snapshot_read_is_deduped():
    """C12 wiring pin (mutant X3 survived without it): board() must read
    snapshots THROUGH dedupe_snaps, or the per-account page double-counts
    the duplicated dates the compare page dedupes."""
    import re
    src = open("dashboard/monitor.py").read()
    assert re.search(r'snaps = dedupe_snaps\(_load_jsonl\(', src), \
        "C12: board() reads snapshots without dedupe"


def test_equity_series_dedupes_the_index():
    from live.compare import _equity_series
    eq = _equity_series([{"date": "2026-07-24", "equity": 100.0},
                         {"date": "2026-07-24", "equity": 105.0},
                         {"date": "2026-07-25", "equity": 110.0}])
    assert len(eq) == 2 and eq.iloc[0] == 105.0, \
        "C12: duplicated snapshot dates double-count sessions in every stat"


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
