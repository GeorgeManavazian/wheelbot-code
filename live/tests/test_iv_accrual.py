"""Per-ticker IV observation building. Pure: no client, no clock, no files.

The one thing that must not drift: the recorded value is the IV of THE CONTRACT
select_contract WOULD SELL that day -- same function, same delta target, same
DTE band. IVHistory.from_chains states why in its own docstring: the ranks were
measured on exactly this quantity, so recording a chain-wide average or an ATM
proxy silently redefines the statistic while everything keeps running.
"""
import json

import pandas as pd
import pytest

import live.data as data
from live.iv_accrual import (ACCRUAL_GRID, MAX_SOLVED_IV, observations_for,
                             stamp)
from src.engine_v2.options.select import select_contract

PUTS = json.load(open("live/fixtures/option_chain_gdx_puts.json"))
OBS = pd.Timestamp("2026-07-17")


def _frame():
    """The shape otm_put_frame produces, built offline: puts only, plus the
    header's rate/div_yield already converted to decimals."""
    df = data.chain_from_json(PUTS, OBS)
    df = df[df["right"] == "P"].reset_index(drop=True)
    df["rate"] = data._pct(PUTS["interestRate"])
    df["div_yield"] = data._pct(PUTS["dividendYield"])
    return df


def test_grid_is_the_configs_actually_in_play():
    assert (0.30, 11) in ACCRUAL_GRID, "FROZEN"
    assert (0.20, 11) in ACCRUAL_GRID, "the delta the owner described 2026-08-03"
    assert (0.40, 7) in ACCRUAL_GRID, "the sweep's top arm"
    assert len(ACCRUAL_GRID) == len(set(ACCRUAL_GRID))


def test_stamp_encodes_source_solver_and_the_grid_cell():
    """A put_delta or target_dte change is a scale change exactly as a solver
    change is: a 0.20-delta put is further OTM and cheaper, so every post-change
    day would read as near-record-cheap on a series that never got cheap.
    IVHistory.append refuses on a stamp difference -- so the cell must be IN the
    stamp, or that refusal cannot see a config change."""
    assert stamp(0.30, 11) == "schwab-rth/bs-v1/d30/dte11"
    assert stamp(0.20, 7) == "schwab-rth/bs-v1/d20/dte7"
    assert stamp(0.30, 11) != stamp(0.20, 11)
    assert stamp(0.30, 11) != stamp(0.30, 7)


def test_records_the_contract_select_contract_would_actually_sell():
    frame = _frame()
    recs = {(r["put_delta"], r["target_dte"]): r
            for r in observations_for("GDX", frame, OBS)}
    r = recs[(0.30, 11)]
    c = select_contract(frame, OBS, "P", 0.30, 11, "GDX")
    assert c is not None
    assert r["strike"] == c.strike
    assert pd.Timestamp(r["expiry"]) == pd.Timestamp(c.expiry)


def test_both_dte_bands_produce_observations():
    """The regression that a request window sized for DTE 11 alone would cause,
    caught one layer down as well: derived_band(7) = (5,10) must resolve too."""
    recs = observations_for("GDX", _frame(), OBS)
    dtes = {r["target_dte"] for r in recs}
    assert dtes == {7, 11}, f"one band produced nothing: {dtes}"


def test_every_cell_carries_its_own_stamp_and_solver_inputs():
    for r in observations_for("GDX", _frame(), OBS):
        assert r["source"] == stamp(r["put_delta"], r["target_dte"])
        for k in ("ticker", "expiry", "strike", "dte", "delta", "bid", "ask",
                  "mid", "underlying", "rate", "div_yield", "iv"):
            assert k in r, k
        assert 0.0 < r["iv"] < MAX_SOLVED_IV
        assert r["strike"] < r["underlying"], "solver is OTM-only"
        assert isinstance(r["expiry"], str), "must be JSON-serialisable"


def test_a_cell_with_no_in_band_expiry_contributes_nothing_not_a_nan():
    """A gap must SHORTEN a history, never poison the percentile. Same rule as
    IVHistory.from_chains."""
    recs = observations_for("GDX", _frame(), OBS, grid=[(0.30, 99)])
    assert recs == []


def test_empty_frame_yields_no_records():
    empty = _frame().iloc[0:0]
    assert observations_for("GDX", empty, OBS) == []


def test_a_quote_that_pins_the_solver_is_dropped_not_recorded():
    """Bisection returns its ceiling for an unsolvable quote. Recording 500%
    vol would dominate that ticker's percentile for a full year."""
    frame = _frame().copy()
    frame["mid"] = frame["strike"] * 2.0     # no-arb violation on every row
    frame["bid"] = frame["mid"] - 0.01
    frame["ask"] = frame["mid"] + 0.01
    assert observations_for("GDX", frame, OBS) == []


def test_missing_rate_or_div_yield_skips_rather_than_guessing():
    frame = _frame().copy()
    frame["rate"] = float("nan")
    assert observations_for("GDX", frame, OBS) == []


def test_every_cell_selects_the_contract_its_own_delta_target_would_choose():
    """Generalises test_records_the_contract_select_contract_would_actually_sell
    across the WHOLE grid, not just (0.30, 11). On the GDX fixture, (0.30, 11)
    alone is invariant to a mutation that substitutes a different delta into the
    select_contract call for every cell -- three of the other five cells select
    a different strike under such a mutation. Added so the load-bearing rule
    ('call select_contract with THIS cell's own put_delta, never a
    substitute') is actually exercised on a cell where it can fail."""
    frame = _frame()
    recs = {(r["put_delta"], r["target_dte"]): r
            for r in observations_for("GDX", frame, OBS)}
    for put_delta, target_dte in ACCRUAL_GRID:
        if (put_delta, target_dte) not in recs:
            continue
        r = recs[(put_delta, target_dte)]
        c = select_contract(frame, OBS, "P", put_delta, target_dte, "GDX")
        assert c is not None
        assert r["strike"] == c.strike
        assert pd.Timestamp(r["expiry"]) == pd.Timestamp(c.expiry)
