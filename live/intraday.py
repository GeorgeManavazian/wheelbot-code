"""Intraday EXIT-ONLY manager. Given live option marks for the held short legs,
close any that hit take-profit -- at the live ask, exactly as step_one_day's EOD
close does (both call fills.try_take_profit in quote mode). This is the ONLY thing
that runs intraday: no new entries, no assignment, no expiry, no covered-call
selling -- those depend on the prior-day regime and stay in the 5pm EOD run."""
from __future__ import annotations
import pandas as pd

from src.engine_v2.options.fills import try_take_profit
from src.engine_v2.options.wheel import Trade


def _contract_field(c, name):
    """Held short contracts are Contract dataclasses after load_state; tolerate a
    dict too (defensive)."""
    return getattr(c, name) if hasattr(c, name) else c[name]


def manage_intraday(state, quotes, cfg, now):
    """Close held short legs at take-profit using live `quotes` = {ticker: Mark}.
    Mutates `state` (cash, positions) and returns the list[Trade] booked. Mirrors
    the EOD TP-close exactly -- both call fills.try_take_profit in quote mode
    (trigger on mark.ask <= (1-TP)*credit, book at the ask) -- then drops the
    emptied PUT position. Only touches positions whose live quote is present and
    whose expiry is still ahead (expiry stays EOD)."""
    if cfg.take_profit_pct is None or cfg.take_profit_pct >= 1.0:
        return []
    today = pd.Timestamp(now).normalize()
    trades = []
    for pos in state.positions:
        short = pos.get("short")
        if short is None:
            continue
        c, n = short["contract"], short["contracts"]
        if today >= pd.Timestamp(_contract_field(c, "expiry")):
            continue                                   # expiry-day handling is EOD
        mark = quotes.get(pos["ticker"])
        if mark is None:
            continue
        dec = try_take_profit(mark=mark, credit=short["credit"], contracts=n,
                              cfg=cfg, day=today,
                              expiry=pd.Timestamp(_contract_field(c, "expiry")),
                              day_stamp=pd.Timestamp(now))
        if dec.filled:
            state.cash -= dec.cost
            pos["premium"] -= dec.cost
            action = "CLOSE_PUT" if _contract_field(c, "right") == "P" else "CLOSE_CALL"
            trades.append(Trade(dec.stamp, action, c, n, dec.price,
                                state.cash, pos["campaign"]))
            pos["short"] = None
    # drop positions emptied down to a bare PUT slot (same rule as step_one_day)
    state.positions[:] = [p for p in state.positions
                          if not (p["short"] is None and p.get("shares", 0) == 0
                                  and p["phase"] == "PUT")]
    return trades
