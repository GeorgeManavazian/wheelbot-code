import datetime as dt
from zoneinfo import ZoneInfo

from live.run_chain_snapshot import snapshot_window_open, save_still_rth

ET = ZoneInfo("America/New_York")


def _at(day, h, m):
    return dt.datetime(2026, 7, day, h, m, tzinfo=ET)


def test_window_open_inside_rth():
    assert snapshot_window_open(_at(17, 15, 20))      # Friday, open edge
    assert snapshot_window_open(_at(17, 15, 45))
    assert snapshot_window_open(_at(17, 15, 50))      # close edge


def test_window_shut_outside():
    assert not snapshot_window_open(_at(17, 15, 19))
    # 15:51: a start here plus the measured ~7-min pull would finish past the
    # 16:00 close (skeptic F4) -- shut.
    assert not snapshot_window_open(_at(17, 15, 51))
    assert not snapshot_window_open(_at(17, 9, 30))


def test_save_recheck_refuses_post_close_finish():
    """Skeptic F4: the start gate alone let a slow pull bless post-close
    quotes as RTH; the save must re-check the clock, with a small grace for a
    pull that read the closing book seconds late."""
    assert save_still_rth(_at(17, 15, 59))
    assert save_still_rth(_at(17, 16, 5))     # grace edge
    assert not save_still_rth(_at(17, 16, 6))
    assert not save_still_rth(_at(17, 17, 0))


def test_seventeen_hundred_is_the_defect_not_the_window():
    """A16 regression pin: 17:00 ET is after the options close; a snapshot
    then is exactly the ghost book this fix removes."""
    assert not snapshot_window_open(_at(17, 17, 0))


def test_weekend_shut():
    assert not snapshot_window_open(_at(18, 15, 45))  # Saturday
    assert not snapshot_window_open(_at(19, 15, 45))  # Sunday


def _frame(rows):
    import pandas as pd
    COLS = ["date", "expiry", "dte", "strike", "right", "bid", "ask", "mid",
            "close", "delta", "iv", "underlying"]
    df = pd.DataFrame(rows, columns=COLS)
    df["date"] = pd.to_datetime(df["date"])
    df["expiry"] = pd.to_datetime(df["expiry"])
    return df


def test_additive_call_rows_strictly_above_existing_window():
    """A4 splice filter: only strikes strictly above the primary chain's
    per-expiry max call strike, only for expiries the primary already has
    call rows for -- purely additive at the top, provable non-regression."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1, E2 = D + pd.Timedelta(days=11), D + pd.Timedelta(days=25)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.5, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0],
        [D, E1, 11, 69.0, "P", 1.0, 1.1, 1.05, 1.05, -0.4, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0],   # dup of top
        [D, E1, 11, 75.0, "C", 0.4, 0.5, 0.45, 0.45, 0.2, 0.2, 70.0],   # above -> keep
        [D, E1, 11, 80.0, "C", 0.1, 0.2, 0.15, 0.15, 0.1, 0.2, 70.0],   # above -> keep
        [D, E2, 25, 75.0, "C", 0.6, 0.7, 0.65, 0.65, 0.2, 0.2, 70.0]])  # NEW expiry -> drop
    rows = additive_call_rows(primary, wide)
    assert [(r["strike"], pd.Timestamp(r["expiry"])) for r in rows] == \
        [(75.0, E1), (80.0, E1)]


def test_additive_call_rows_empty_cases():
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1 = D + pd.Timedelta(days=11)
    puts_only = _frame([[D, E1, 11, 69.0, "P", 1.0, 1.1, 1.05, 1.05, -0.4,
                         0.2, 70.0]])
    wide = _frame([[D, E1, 11, 80.0, "C", 0.1, 0.2, 0.15, 0.15, 0.1, 0.2, 70.0]])
    assert additive_call_rows(puts_only, wide) == []     # no primary calls
    assert additive_call_rows(wide, None) == []
    assert additive_call_rows(wide, wide.iloc[0:0]) == []


def test_spliced_rows_reach_the_floor_through_selection():
    """End-to-end through the real market seam: spliced OTM rows must be
    TRADEABLE (no held_only flag) so select_contract(min_strike=floor) can
    finally return the floor-reaching strike."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    from live.market_live import LiveMarket
    from live.tests.test_run_daily import PH   # GDX price history fixture
    from live.data import closes_from_json
    from src.engine_v2.options.select import select_contract
    D = pd.Timestamp("2026-07-17")
    E1 = D + pd.Timedelta(days=11)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.5, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.4, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 79.0, "C", 0.1, 0.2, 0.15, 0.15, 0.08, 0.2, 70.0],
        [D, E1, 11, 80.0, "C", 0.05, 0.15, 0.1, 0.1, 0.05, 0.2, 70.0]])
    m = LiveMarket(["GDX"], {"GDX"}, D,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: primary)
    added = m.add_chain_rows("GDX", additive_call_rows(primary, wide))
    assert added == 2
    c = select_contract(m.chain("GDX", D), D, "C", 0.50, 11, "GDX",
                        min_strike=79.0)
    assert c is not None and c.strike == 79.0


def test_additive_call_rows_rejects_garbage_delta():
    """Skeptic F1: a plausible-but-wrong delta (~0.49) on a far-OTM spliced
    row would WIN |delta-0.50| selection and silently move the sold strike.
    Monotonicity guard: spliced |delta| must sit strictly below the primary's
    minimum call |delta| for that expiry."""
    import pandas as pd
    from live.run_chain_snapshot import additive_call_rows
    D = pd.Timestamp("2026-07-21")
    E1 = D + pd.Timedelta(days=11)
    primary = _frame([
        [D, E1, 11, 70.0, "C", 1.0, 1.1, 1.05, 1.05, 0.50, 0.2, 70.0],
        [D, E1, 11, 72.0, "C", 0.8, 0.9, 0.85, 0.85, 0.40, 0.2, 70.0]])
    wide = _frame([
        [D, E1, 11, 85.0, "C", 0.05, 0.10, 0.07, 0.07, 0.49, 0.2, 70.0],  # liar
        [D, E1, 11, 86.0, "C", 0.05, 0.10, 0.07, 0.07, 0.12, 0.2, 70.0]]) # honest
    rows = additive_call_rows(primary, wide)
    assert [r["strike"] for r in rows] == [86.0], \
        "a far-OTM row claiming near-the-money delta must be dropped"
