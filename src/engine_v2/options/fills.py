"""ONE shared take-profit fill rule for the four engines (A18).

Given a quote (and, for the hourly engines, a day of trade prints) and an open
short, decide: does the take-profit fill, at what price, at what cash cost,
stamped with what date. The four call sites and the mode each uses:

  portfolio.step_one_day   (EOD decision engine)   quote only (mark.ask)
  live.intraday            (live exit manager)     quote only, wall-clock stamp
  wheel.run_wheel          (solo backtest)         print-next-bar, then quote
  regime_router            (router backtest)       print-next-bar, then quote

This is a SEAM, not a model choice: every path reproduces the rule its engine
already had, verbatim. The divergence it finally names instead of hiding: the
print rule fires on 81.5% of campaigns and the quote rule on 76.7% -- 159
campaigns (5.3%) the backtest takes profit on that live can never close
(audit A18). WHICH rule is right is the deferred strategy question -- do not
resolve it here, and do not add a new mode without an owner decision.

Admission (which quotes are valid at all) deliberately stays UPSTREAM in the
chain builders (`chain.py`, `live/data.py`, `live/held_legs.py`,
`live/marks.py`): folding those rules together changes the population the
backtest sees, which is exactly what A20 exists to size first.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FillDecision:
    filled: bool
    price: float = 0.0     # per-share, as booked on Trade.price_per_contract
    cost: float = 0.0      # UNSIGNED cash out (buy_cost convention; caller does cash -=)
    stamp: object = None   # what goes on Trade.date (day, bar timestamp, or wall clock)
    via: str = ""          # filled: "print" | "quote"; not filled: the refusal reason
    # A17: how many contracts actually filled. Both current modes are
    # instant-and-whole, so they always set this to the full size; a future
    # model that partially fills has somewhere to say so, and the close
    # bookkeeping (portfolio.close_short_fill) already honors it. Trailing
    # with a default so refusal-path equality and positional construction
    # are unchanged (A18 test pins).
    filled_contracts: int = 0


def try_take_profit(*, mark, credit, contracts, cfg, day, expiry,
                    bars=None, day_stamp=None) -> FillDecision:
    """The one answer to "does this short's take-profit fill today, and how".

    mark      Mark or None, already admitted upstream; never a chain.
    bars      hourly trade prints for THIS contract (or None): mode selector --
              None = quote-only engines, a frame = the print-next-bar cascade.
    day_stamp overrides the quote-path Trade stamp (live manager stamps the
              wall clock, not the normalized day it compares expiry against).

    Guard order is load-bearing (the A6 amendment lesson): take_profit_pct is
    tested before the threshold arithmetic touches it, and mark presence
    before mark.ask.
    """
    if cfg.take_profit_pct is None or cfg.take_profit_pct >= 1.0:
        return FillDecision(False, via="tp_disabled")   # >= 1.0 = hold to expiry
    if not (day < expiry):
        return FillDecision(False, via="expiry_day")    # expiry is settlement's job
    thresh = (1 - cfg.take_profit_pct) * credit
    if bars is not None:
        # close > 0 only: hourly bars are trade prints, and hours with no trade
        # arrive as close=0 -- not a price. Treating a 0 as a price lets any
        # losing put "TP" at a phantom fill (XOP 2020: +2,582% fantasy).
        # Trigger and fill both use valid prints only; decide on bar i, fill at
        # bar i+1's close (no same-bar fills). A cross on the day's LAST bar
        # has no next bar -> fall through to the quote check below.
        day_bars = (bars[(bars["timestamp"].dt.normalize() == day)
                         & (bars["close"] > 0)]
                    .sort_values("timestamp").reset_index(drop=True))
        for i in range(len(day_bars) - 1):
            if day_bars.iloc[i]["close"] <= thresh:
                fill = day_bars.iloc[i + 1]
                # arithmetic mirrors wheel.buy_cost term-for-term; fill["close"]
                # stays the numpy scalar it always was (cash dtype contamination
                # is pre-existing, pinned by test, and NOT changed by this seam)
                cost = (fill["close"] * cfg.contract_multiplier * contracts
                        + cfg.commission_per_contract * contracts)
                return FillDecision(True, float(fill["close"]), cost,
                                    fill["timestamp"], "print", contracts)
    if mark is not None and mark.ask <= thresh:
        cost = (mark.ask * cfg.contract_multiplier * contracts
                + cfg.commission_per_contract * contracts)
        return FillDecision(True, mark.ask, cost,
                            day if day_stamp is None else day_stamp, "quote",
                            contracts)
    return FillDecision(False, via="no_fill")
