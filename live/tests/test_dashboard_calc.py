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


def test_mark_basis_line_uniform_vs_mixed():
    """C11 render (re-pinned after skeptic F2: the first version claimed a
    mid-series changeover on all-unstamped stores and mislabeled the
    boundary): uniform stamped -> one quiet word; unstamped rows -> UNKNOWN
    basis, never a guessed "mid"."""
    from dashboard.monitor import mark_basis_line
    uniform = [{"date": "2026-08-03", "mark_basis": "ask"},
               {"date": "2026-08-04", "mark_basis": "ask"}]
    assert mark_basis_line(uniform) == "equity marked at ask"
    mixed = [{"date": "2026-07-25"},                       # pre-stamp era
             {"date": "2026-08-03", "mark_basis": "ask"}]
    line = mark_basis_line(mixed)
    assert "not like-for-like" in line and "unknown" in line
    assert "changed" not in line, \
        "F2: one unstamped + one stamped row is not an observed change"
    assert mark_basis_line([]) is None


def test_mark_basis_line_f2_boundary_cases():
    """Skeptic F2 receipts, pinned. (1) The REAL store shape today -- every
    row unstamped -- must not claim anything 'changed mid-series'; nothing
    did. (2) A change between two STAMPED rows is named at the exact row it
    happened, with both bases. (3) Stamped-then-unstamped must not point the
    warning at the wrong side."""
    from dashboard.monitor import mark_basis_line
    all_unstamped = [{"date": f"2026-07-{d}"} for d in (20, 21, 22)]
    line = mark_basis_line(all_unstamped)
    assert "unknown" in line and "changed" not in line
    stamped_change = [{"date": "2026-07-20", "mark_basis": "mid"},
                      {"date": "2026-07-21", "mark_basis": "mid"},
                      {"date": "2026-08-01", "mark_basis": "ask"}]
    line = mark_basis_line(stamped_change)
    assert "changed 2026-08-01" in line and "mid → ask" in line
    assert "2026-07-20" not in line, \
        "F2: the changeover is at the first DIFFERENT stamped row"
    tail_unstamped = [{"date": "2026-08-01", "mark_basis": "ask"},
                      {"date": "2026-08-02"}]
    line = mark_basis_line(tail_unstamped)
    assert "unknown" in line and "changed" not in line


def test_f1_formatters_survive_dataframe_nan_coercion():
    """Skeptic F1 (HIGH): pd.DataFrame coerces win_rate=None to NaN in a
    float64 column whenever any other account has closed campaigns, so an
    `is None` guard in the render is dead code -- the real store rendered
    'nan% (0W/0L)'. The formatters must judge with pd.isna ACROSS the
    DataFrame boundary the unit tests previously never crossed."""
    import pandas as pd
    from dashboard.monitor import fmt_sharpe, fmt_win
    rows = [{"sharpe": None, "sharpe_se": None, "sharpe_n": 7,
             "sharpe_suppressed": True, "win_rate": None, "wins": 0,
             "losses": 0},
            {"sharpe": 1.5, "sharpe_se": 0.4, "sharpe_n": 40,
             "sharpe_suppressed": False, "win_rate": 0.5, "wins": 1,
             "losses": 1}]
    df = pd.DataFrame(rows)
    assert float != type(None) and df["win_rate"].dtype == "float64"
    assert fmt_win(df.iloc[0]) == "—", "F1: the nan% (0W/0L) render is back"
    assert fmt_sharpe(df.iloc[0]) == "— (n=7)"
    assert fmt_win(df.iloc[1]) == "50% (1W/1L)"
    assert fmt_sharpe(df.iloc[1]) == "1.50 ± 0.40"
    # F6 leg: >=30-obs curve whose equity touched 0 -> NaN sharpe; must
    # render suppressed, never "nan ± nan"
    df2 = pd.DataFrame([{**rows[1], "sharpe": float("nan"),
                         "sharpe_se": float("nan")}])
    assert fmt_sharpe(df2.iloc[0]) == "— (n=40)"


def test_concentration_and_benchmark_lines_render_defensively():
    """C7: a young store must SAY it cannot estimate independence, never go
    silently blank. C6: the benchmark line must always carry the
    capital-matching caveat."""
    from dashboard.monitor import concentration_line, benchmark_line
    import live.compare as lc
    import dashboard.monitor as dm
    orig_gc, orig_bs = lc.grid_concentration, lc.benchmark_series
    try:
        lc.grid_concentration = lambda: {"n_eff": None, "rho_bar": None,
                                         "n_decisions": 0, "n_sell_rows": 0,
                                         "replication": None,
                                         "top_ticker": None,
                                         "top_premium_share": None}
        assert "too few observations" in concentration_line()
        lc.grid_concentration = lambda: {"n_eff": 1.3, "rho_bar": 0.74,
                                         "n_decisions": 12, "n_sell_rows": 90,
                                         "replication": 7.5,
                                         "top_ticker": "DOW",
                                         "top_premium_share": 0.43}
        line = concentration_line()
        assert "1.3" in line and "12 distinct" in line and "DOW" in line
        lc.benchmark_series = lambda: {"window": ("2026-07-20", "2026-07-27"),
                                       "spy_pct": -0.05, "ew_pct": 0.56,
                                       "ew_names": 12}
        b = benchmark_line()
        assert "100% invested" in b and "SPY" in b, \
            "C6: the capital-matching caveat is mandatory copy"
    finally:
        lc.grid_concentration, lc.benchmark_series = orig_gc, orig_bs


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
