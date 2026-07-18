"""Intraday EXIT-ONLY manager. Given live option marks for the held short legs,
close any that hit take-profit -- at the live ask, exactly as step_one_day's EOD
close does (mark.ask <= (1-TP)*credit; cost via buy_cost). This is the ONLY thing
that runs intraday: no new entries, no assignment, no expiry, no covered-call
selling -- those depend on the prior-day regime and stay in the 5pm EOD run."""
from __future__ import annotations
import pandas as pd

from src.engine_v2.options.wheel import Trade, buy_cost


def _contract_field(c, name):
    """Held short contracts are Contract dataclasses after load_state; tolerate a
    dict too (defensive)."""
    return getattr(c, name) if hasattr(c, name) else c[name]


def manage_intraday(state, quotes, cfg, now):
    """Close held short legs at take-profit using live `quotes` = {ticker: Mark}.
    Mutates `state` (cash, positions) and returns the list[Trade] booked. Mirrors
    the EOD TP-close: trigger on mark.ask <= (1-TP)*credit, book at mark.ask via
    buy_cost, drop the emptied PUT position. Only touches positions whose live
    quote is present and whose expiry is still ahead (expiry stays EOD)."""
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
        if mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]:
            cost = buy_cost(mark, n, cfg)
            state.cash -= cost
            pos["premium"] -= cost
            action = "CLOSE_PUT" if _contract_field(c, "right") == "P" else "CLOSE_CALL"
            trades.append(Trade(today, action, c, n, mark.ask, state.cash, pos["campaign"]))
            pos["short"] = None
    # drop positions emptied down to a bare PUT slot (same rule as step_one_day)
    state.positions[:] = [p for p in state.positions
                          if not (p["short"] is None and p.get("shares", 0) == 0
                                  and p["phase"] == "PUT")]
    return trades
