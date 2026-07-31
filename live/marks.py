"""Live mark-to-market for open paper positions. Pulls REAL Schwab option
quotes for each held short leg so the dashboard shows moving unrealized P&L
during market hours. Data-only (quotes) -- NO order code.

Everything here is defensive: any pull/parse failure (bad token, market closed,
missing symbol) yields no mark for that position, and the caller falls back to
the entry credit + flags the quote stale. It NEVER guesses a price."""
from __future__ import annotations
import pandas as pd

from src.engine_v2.options.chain import Mark


def occ_symbol(root: str, expiry, strike: float, right: str) -> str:
    """Schwab/OCC option symbol: root left-justified to 6 chars, YYMMDD expiry,
    C/P, then strike*1000 zero-padded to 8 digits.
      occ_symbol("AGNC", "2026-08-15", 11.0, "P") -> "AGNC  260815P00011000"
    """
    root = root.upper()
    exp = pd.Timestamp(expiry).strftime("%y%m%d")
    strike_milli = int(round(float(strike) * 1000))
    return f"{root:<6}{exp}{right}{strike_milli:08d}"


def _mark_from_quote(q: dict):
    """Pull a usable mark out of one Schwab quote entry. Prefer the exchange
    'mark'; else midpoint of bid/ask; else last. None if nothing usable."""
    node = q.get("quote", q) if isinstance(q, dict) else {}
    mark = node.get("mark")
    if mark is not None and mark > 0:
        return float(mark)
    bid, ask = node.get("bidPrice"), node.get("askPrice")
    if bid is not None and ask is not None and (bid + ask) > 0:
        return (float(bid) + float(ask)) / 2.0
    last = node.get("lastPrice")
    return float(last) if last else None


def live_marks(client, positions) -> dict:
    """{ticker: mark_per_share} for every position with a short leg, from a single
    batched Schwab quote pull. Returns {} on any failure (caller degrades)."""
    legs = {}  # symbol -> ticker
    for p in positions:
        short = p.get("short")
        if not short:
            continue
        c = short["contract"]
        sym = occ_symbol(_contract_field(c, "root"), _contract_field(c, "expiry"),
                         _contract_field(c, "strike"), _contract_field(c, "right"))
        legs[sym] = p["ticker"]
    if not legs:
        return {}
    try:
        resp = client.get_quotes(list(legs.keys()))
        data = resp.json() if hasattr(resp, "json") else resp
    except Exception:
        return {}
    out = {}
    if not isinstance(data, dict):
        return {}
    for sym, tk in legs.items():
        q = data.get(sym)
        if not q:
            continue
        m = _mark_from_quote(q)
        if m is not None:
            out[tk] = m
    return out


def live_asks(client, positions) -> dict:
    """{ticker: ask} for every held short leg -- what it would COST to close the
    book right now, which is what the dashboard displays and what the engine
    marks equity from (owner decision B, 2026-07-31).

    Unlike `contract_quotes` this does not require a live bid: a deep-OTM leg
    quoted 0.00 x 0.01 is worthless, and worthless is exactly the state the
    display must be able to show. It does require a real ask, because an ask of
    zero is a halted or empty book and would display the liability as nil."""
    legs = {}
    for p in positions:
        short = p.get("short")
        if not short:
            continue
        c = short["contract"]
        sym = occ_symbol(_contract_field(c, "root"), _contract_field(c, "expiry"),
                         _contract_field(c, "strike"), _contract_field(c, "right"))
        legs[sym] = p["ticker"]
    if not legs:
        return {}
    try:
        resp = client.get_quotes(list(legs))
        data = resp.json() if hasattr(resp, "json") else resp
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for sym, tk in legs.items():
        q = data.get(sym)
        if not isinstance(q, dict):
            continue
        ask = q.get("quote", q).get("askPrice")
        if ask is not None and float(ask) > 0:
            out[tk] = float(ask)
    return out


def _contract_field(c, name):
    return getattr(c, name) if hasattr(c, name) else c[name]


def contract_quotes(client, positions) -> dict:
    """{ticker: Mark(bid,ask,mid)} live for each held short leg -- the full bid/ask
    the intraday TP check + buy_cost need (live_marks returns only a single mark).
    One batched quote pull; {} on any failure; skips a leg missing bid or ask."""
    sym_to_tk = {}
    for p in positions:
        short = p.get("short")
        if not short:
            continue
        c = short["contract"]
        sym = occ_symbol(_contract_field(c, "root"), _contract_field(c, "expiry"),
                         _contract_field(c, "strike"), _contract_field(c, "right"))
        sym_to_tk[sym] = p["ticker"]
    if not sym_to_tk:
        return {}
    try:
        resp = client.get_quotes(list(sym_to_tk))
        data = resp.json() if hasattr(resp, "json") else resp
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for sym, tk in sym_to_tk.items():
        q = data.get(sym)
        if not q:
            continue
        node = q.get("quote", q) if isinstance(q, dict) else {}
        bid, ask = node.get("bidPrice"), node.get("askPrice")
        # require a real two-sided quote -- a 0/0 (halt, pre-open, thin option) would
        # otherwise mark ask=0, and the intraday TP check (ask <= (1-TP)*credit) would
        # fire and "close" the leg for free. Matches the EOD chain's bid>0 & ask>0 filter.
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            continue
        mk = node.get("mark")
        mid = float(mk) if mk else (float(bid) + float(ask)) / 2.0
        out[tk] = Mark(float(bid), float(ask), mid)
    return out
