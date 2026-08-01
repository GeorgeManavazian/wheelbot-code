"""Mark the legs the bounded option chain cannot see.

`chain_frame` pulls `strike_count=12` strikes around the money. That is the right
window for CHOOSING a contract, and the wrong window for MARKING one you already
hold: a short put whose underlying rallies drifts out of it, and from that moment
`option_mark` returns None for the leg. Every branch that could act on it —
take-profit, the equity mark — is guarded by `mark is not None`, so the position
silently freezes at whatever it was last worth.

Found live on 2026-07-29: TMO 512.5P carried at $11.30 with the stock at 576 and
the contract offered near $0.42, months past its 60% take-profit trigger. Not
only a reporting error; the strategy stopped executing on that position.

Fix: ask Schwab directly for each held leg by OCC symbol (one batched quote pull,
data-only) and splice the ones the chain is missing into it before `step_one_day`
reads it. Data-only; no order code.
"""
from __future__ import annotations

import pandas as pd

from live.data import _CHAIN_COLS
from live.marks import occ_symbol, _contract_field, _num


def held_contracts(position_lists) -> list:
    """Every distinct short leg across every account, as plain dicts.

    Deduped by (ticker, expiry, strike, right): the 25 accounts hold heavily
    overlapping legs — one underlying was 47% of realized P&L — and Schwab's
    quote endpoint should be asked once per contract, not once per account."""
    out = {}
    for positions in position_lists:
        for p in positions:
            short = p.get("short")
            if not short:
                continue
            c = short["contract"]
            expiry = pd.Timestamp(_contract_field(c, "expiry")).normalize()
            strike = float(_contract_field(c, "strike"))
            right = _contract_field(c, "right")
            key = (p["ticker"], expiry, strike, right)
            out.setdefault(key, {"ticker": p["ticker"], "root": _contract_field(c, "root"),
                                 "expiry": expiry, "strike": strike, "right": right,
                                 "symbol": occ_symbol(_contract_field(c, "root"),
                                                      expiry, strike, right)})
    return list(out.values())


def rows_from_quotes(quotes: dict, contracts, obs) -> dict:
    """{ticker: [chain row, ...]} from a Schwab quotes payload. Pure mapping.

    Deliberately looser than `chain_from_json`, in one direction only: a held leg
    is kept when bid is 0.00 as long as ask is a real offer. A 0.00 x 0.01 put is
    worthless, and worthless is precisely the state the take-profit exists to
    act on — dropping it (as the chain builder rightly does for contracts we
    might SELL) is what leaves the position frozen. A two-sided 0.00 x 0.00 is
    still refused: that is a halt or a pre-open book, not a price, and an ask of
    zero satisfies every take-profit test there is."""
    obs = pd.Timestamp(obs).normalize()
    out = {}
    for c in contracts:
        q = quotes.get(c["symbol"])
        if not isinstance(q, dict):
            continue
        node = q.get("quote", {})
        ref = q.get("reference", {})
        bid, ask = _num(node.get("bidPrice")), _num(node.get("askPrice"))
        if bid is None or ask is None or ask <= 0 or bid < 0:
            continue
        mark = _num(node.get("mark"))
        mid = mark if (mark is not None and mark > 0) else (bid + ask) / 2.0
        und = _num(node.get("underlyingPrice"))
        dte = ref.get("daysToExpiration")
        if dte is None:
            dte = max((c["expiry"] - obs).days, 0)
        out.setdefault(c["ticker"], []).append({
            "date": obs, "expiry": c["expiry"], "strike": c["strike"],
            "right": c["right"], "dte": int(dte),
            "delta": _num(node.get("delta")), "bid": bid, "ask": ask, "mid": mid,
            "underlying": und,
            # A19: same liquidity capture as the chain rows; the quote payload
            # names them at the top of its quote node. Absent -> None, never
            # invented.
            "open_interest": _num(node.get("openInterest")),
            "volume": _num(node.get("totalVolume")),
            "bid_size": _num(node.get("bidSize")),
            "ask_size": _num(node.get("askSize")),
            # MARK ONLY -- select_contract must never return this row. It was
            # pulled by OCC symbol because we already hold it, not because the
            # bot surveyed it, and run_daily shares one market across all 25
            # accounts, so without this flag the leg one account holds becomes a
            # candidate entry for the other 24. (audit 2026-07-31)
            "held_only": True,
        })
    return out


def merge_held_legs(market, client, position_lists, obs) -> dict:
    """Splice missing held legs into `market`'s chains. Returns run stats.

    Only ADDS rows the chain does not already carry — the EOD chain is the
    authoritative snapshot for the day and a quote pulled minutes later must not
    restate it. Never raises: a failed quote pull leaves the run exactly as it
    was before this fix existed (legs unmarked), which is worse but not wrong,
    and must not take the daily run down with it."""
    contracts = held_contracts(position_lists)
    # `no_chain` is separate from `unquoted` on purpose. A leg whose ticker's
    # chain pull failed quotes perfectly well, so it never looks unquoted — but
    # there is nothing to splice it into, so it goes unmarked and its take-profit
    # is suspended for the day. That is this module's own failure mode arriving
    # through a second door, and `merged: 0` is ALSO what a healthy run reports
    # when the leg is already inside the window. Without this the two are
    # indistinguishable and the run prints a success line either way.
    stats = {"requested": len(contracts), "merged": 0, "unquoted": [],
             "no_chain": [], "error": None, "answered": 0}
    if not contracts:
        return stats
    try:
        resp = client.get_quotes([c["symbol"] for c in contracts])
        data = resp.json() if hasattr(resp, "json") else resp
    except Exception as e:
        stats["error"] = f"{type(e).__name__}: {e}"
        return stats
    if not isinstance(data, dict):
        stats["error"] = f"quote payload was {type(data).__name__}, not a dict"
        return stats

    # B4 amendment (group skeptic F2): count legs the endpoint ANSWERED for
    # (a dict payload entry), separately from legs it could quote. A worthless
    # 0.00x0.00 book is ANSWERED-but-refused -- the endpoint is alive and the
    # book is real; only zero answers is a wholesale endpoint failure.
    stats["answered"] = sum(1 for c in contracts
                            if isinstance(data.get(c["symbol"]), dict))
    rows_by_ticker = rows_from_quotes(data, contracts, obs)
    quoted = {(r["ticker"], row["expiry"], row["strike"], row["right"])
              for r in contracts
              for row in rows_by_ticker.get(r["ticker"], [])}
    stats["unquoted"] = [c["symbol"] for c in contracts
                         if (c["ticker"], c["expiry"], c["strike"], c["right"])
                         not in quoted]
    for ticker, rows in rows_by_ticker.items():
        added = market.add_chain_rows(ticker, rows)
        stats["merged"] += added
        if added == 0 and market.chain(ticker, obs) is None:
            stats["no_chain"] += [c["symbol"] for c in contracts
                                  if c["ticker"] == ticker]
    return stats


# `_num` now lives in live.marks — one coercion shared with contract_quotes, so
# the two quote-parsing paths cannot disagree about the same contract. (A6.)
