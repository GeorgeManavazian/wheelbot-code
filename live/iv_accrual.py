"""Chain frame + config grid -> IV observation records. Pure; data-only.

WHY A GRID. The pull already downloads every OTM put on the ticker. Recording
only the contract FROZEN would sell throws away the other ~59 already in hand,
and Schwab has NO historical chain endpoint -- live/data.py says it plainly, a
day not captured is unmeasurable forever. So a later put_delta/target_dte change
would mean a 150-observation rebuild it is impossible to backfill.

Recording the grid instead costs zero API calls (six select_contract passes over
a frame already in memory) and turns that rebuild into a column switch: every
cell accrues in parallel from day 1 and warms up on the same schedule.

The cells are not arbitrary -- 0.30/11 is FROZEN, 0.20 is the delta the owner
described the strategy as on 2026-08-03, and 0.40/7 is the sweep's top arm.

BACKTESTS ARE UNAFFECTED by any of this: IVHistory.from_chains rebuilds in
memory from raw stored chains on every run, sets no stamp, and never calls
append. Only the live forward path needs a lock, because only the live forward
path cannot re-derive.
"""
from __future__ import annotations

import pandas as pd

from src.engine_v2.options.iv_solve import implied_vol_put
from src.engine_v2.options.select import select_contract

# (put_delta, target_dte) cells recorded every day. Adding a cell is cheap
# (no API cost) but only accrues FORWARD -- it cannot be backfilled.
ACCRUAL_GRID = [(0.20, 7), (0.20, 11),
                (0.30, 7), (0.30, 11),
                (0.40, 7), (0.40, 11)]

SOURCE = "schwab-rth"
SOLVER_VERSION = "bs-v1"

# implied_vol_put bisects on [1e-6, 5.0]. An unsolvable quote (a no-arb
# violation, a stale crossed book) does not raise -- it returns the ceiling. A
# recorded 500% observation would dominate that ticker's percentile for a full
# 252-day window, so a pinned solve is DROPPED rather than stored.
MAX_SOLVED_IV = 4.9


def stamp(put_delta: float, target_dte: int) -> str:
    """The provenance scale for one grid cell.

    The cell is IN the stamp because changing put_delta or target_dte is a scale
    change exactly as changing the solver is. IVHistory.append refuses to extend
    a series whose stamp differs; if the cell were not encoded, that refusal
    could not see a config change and the series would silently hold two scales.
    """
    return f"{SOURCE}/{SOLVER_VERSION}/d{int(round(put_delta * 100))}/dte{target_dte}"


def observations_for(ticker: str, frame, obs_date, grid=ACCRUAL_GRID) -> list:
    """One record per grid cell that resolves to a solvable contract.

    A cell with no in-band expiry, an unusable quote, or a missing solver input
    contributes NOTHING -- never a NaN. Gaps shorten a history; NaNs poison a
    percentile. Cells are independent: a ticker can legitimately produce a DTE-11
    row and no DTE-7 row on the same day."""
    if frame is None or len(frame) == 0:
        return []
    obs = pd.Timestamp(obs_date).normalize()
    out = []
    for put_delta, target_dte in grid:
        c = select_contract(frame, obs, "P", put_delta, target_dte, ticker)
        if c is None:
            continue
        sel = frame[(frame["expiry"] == c.expiry)
                    & (frame["strike"] == c.strike)
                    & (frame["right"] == "P")]
        if sel.empty:
            continue
        row = sel.iloc[0]
        mid, und = float(row["mid"]), float(row["underlying"])
        strike, dte = float(row["strike"]), int(row["dte"])
        rate, q = row["rate"], row["div_yield"]
        # A missing r/q is not a zero r/q. Skipping keeps the series on one
        # scale; substituting a default would put an unmarked second scale in
        # it, which is the failure the stamp exists to make impossible.
        if pd.isna(rate) or pd.isna(q) or mid <= 0 or und <= 0 or dte <= 0:
            continue
        try:
            iv = implied_vol_put(price=mid, underlying=und, strike=strike,
                                 dte=dte, rate=float(rate), div_yield=float(q))
        except ValueError:
            # implied_vol_put refuses ITM puts. An OTM-only pull should never
            # produce one; skip rather than propagate, so an unexpected row
            # cannot kill the whole day.
            continue
        if not (0.0 < iv < MAX_SOLVED_IV):
            continue
        out.append({
            "ticker": ticker,
            "put_delta": put_delta, "target_dte": target_dte,
            "expiry": str(pd.Timestamp(row["expiry"]).date()),
            "strike": strike, "dte": dte, "delta": float(row["delta"]),
            "bid": float(row["bid"]), "ask": float(row["ask"]), "mid": mid,
            "underlying": und, "rate": float(rate), "div_yield": float(q),
            "iv": float(iv), "source": stamp(put_delta, target_dte),
        })
    return out
