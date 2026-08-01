"""Live mark-to-market for open paper positions. Pulls REAL Schwab option
quotes for each held short leg so the dashboard shows moving unrealized P&L
during market hours. Data-only (quotes) -- NO order code.

Everything here is defensive: any pull/parse failure (bad token, market closed,
missing symbol) yields no mark for that position, and the caller falls back to
the entry credit + flags the quote stale. It NEVER guesses a price."""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")

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


def _num(v):
    """Coerce one Schwab JSON field to a float, or None. Rejects NaN and anything
    non-numeric. Lives here (not in `held_legs`) because `held_legs` imports from
    this module and the reverse would be circular; it is the ONE coercion both
    quote-parsing paths share, so they cannot silently disagree about the same
    contract. (A6, audit 2026-07-31.)"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


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

    Does not require a live bid: a deep-OTM leg quoted 0.00 x 0.01 is worthless,
    and worthless is exactly the state the display must be able to show. It does
    require a real ask, because an ask of zero is a halted or empty book and
    would display the liability as nil.

    KNOWN DIVERGENCE from `contract_quotes`, which since A6 (2026-07-31) shares
    the zero-bid rule but is stricter: this function still admits a MISSING and a
    NEGATIVE bid, and does not reject NaN. So the dashboard can price a liability
    the intraday manager will refuse to act on. Left alone deliberately — folding
    the four admission rules into one is A18, not A6."""
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
        bid, ask = _num(node.get("bidPrice")), _num(node.get("askPrice"))
        # Require a real ASK; a zero BID is a price, not an absence.
        #
        # This used to demand bid>0 too, which made the intraday manager blind to
        # exactly the leg it exists to close: a fully-decayed put quoted
        # 0.00 x 0.01 is worthless, and worthless is the winning outcome. 45% of
        # this bot's own 0.30-delta/11-DTE picks reach ask <= $0.05 before expiry.
        # The sibling EOD path (held_legs.rows_from_quotes) requires only a real
        # ask, so the two functions disagreed about the same contract. (A6,
        # audit 2026-07-31)
        #
        # ask<=0 is still refused, and that guard is load-bearing: an ask of zero
        # satisfies `ask <= (1-TP)*credit` for ANY credit, so a halted or
        # pre-open 0.00 x 0.00 book would "close" the leg for free and drop it
        # (defect C1, audit 2026-07-18). A negative bid is not a price either.
        #
        # `_num` is what makes the clause order safe: with raw JSON, testing
        # `ask <= 0` BEFORE `bid <= 0` meant a non-numeric ask (previously
        # short-circuited away by the bid test) raised TypeError out of the whole
        # per-account tick, suppressing take-profit on that account's other legs.
        # It also rejects NaN, which the old `bid <= 0` clause caught only by
        # accident. Same coercion as held_legs.rows_from_quotes.
        if bid is None or ask is None or ask <= 0 or bid < 0:
            continue
        # A7: a quote stamped on a PREVIOUS session's ET date is not a price
        # you can trade on today. market_is_open knows no holidays by design,
        # so on Thanksgiving Schwab serves Wednesday's book and the bot once
        # booked CLOSE_PUT @ 0.39 against it. Parameter-free (no calendar to
        # rot): same-ET-date or refused; a missing timestamp is refused too
        # (a book whose freshness cannot be judged is not tradeable).
        # Minutes-scale halt staleness within a session is C1's row.
        qt = _num(node.get("quoteTimeInLong"))
        if qt is None:
            print(f"stale-quote gate: {sym} has no quoteTimeInLong -- refused")
            continue
        try:
            # skeptic F1: inf / out-of-Timestamp-range / unit-drifted values
            # must refuse THIS leg, not raise out of the whole account's tick
            # (the module contract is skip-per-leg, {}-on-total-failure)
            qdate = pd.Timestamp(int(qt), unit="ms", tz="UTC").tz_convert(_ET).date()
        except (OverflowError, ValueError, OSError, NotImplementedError,
                pd.errors.OutOfBoundsDatetime):
            print(f"stale-quote gate: {sym} quoteTimeInLong={qt!r} is not a "
                  f"convertible timestamp -- refused")
            continue
        today_et = dt.datetime.now(_ET).date()
        if qdate != today_et:
            print(f"stale-quote gate: {sym} quoted {qdate}, today is "
                  f"{today_et} -- prior-session book refused (holiday?)")
            continue
        mk = _num(node.get("mark"))
        mid = mk if (mk is not None and mk > 0) else (bid + ask) / 2.0
        out[tk] = Mark(bid, ask, mid)
    return out
