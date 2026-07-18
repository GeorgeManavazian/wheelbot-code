"""Live mark-to-market for open paper positions. Pulls REAL Schwab option
quotes for each held short leg so the dashboard shows moving unrealized P&L
during market hours. Data-only (quotes) -- NO order code.

Everything here is defensive: any pull/parse failure (bad token, market closed,
missing symbol) yields no mark for that position, and the caller falls back to
the entry credit + flags the quote stale. It NEVER guesses a price."""
from __future__ import annotations
import pandas as pd


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
        sym = occ_symbol(c["root"], c["expiry"], c["strike"], c["right"])
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
