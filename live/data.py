"""Schwab JSON -> engine-native shapes. Pure mapping functions (offline-testable)
plus thin client-fetch wrappers. Data-only; no order code. py3.12 (.venv-live)."""
from __future__ import annotations
import datetime as dt
import time
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")

_CHAIN_COLS = ["date", "expiry", "strike", "right", "dte",
               "delta", "bid", "ask", "mid", "underlying",
               # A19: liquidity fields, captured not consumed -- no gate reads
               # them yet (that is A2, a deferred owner decision). They accrue
               # from the day this ships because Schwab has no historical chain
               # endpoint: a day not captured is unmeasurable forever.
               "open_interest", "volume", "bid_size", "ask_size"]


def closes_from_json(payload: dict) -> pd.Series:
    """price_history JSON -> daily close Series (normalized-date index, sorted,
    de-duplicated keep-last). Feeds regime_series()."""
    candles = payload.get("candles", []) or []
    if not candles:
        return pd.Series([], dtype=float, name="close")
    df = pd.DataFrame(candles)
    idx = pd.to_datetime(df["datetime"], unit="ms").dt.normalize()
    s = pd.Series(df["close"].astype(float).to_numpy(), index=idx, name="close")
    # B1: a 0.0/negative/NaN/inf close is not a price -- one such row poisons
    # every SMA/regime computation downstream. Dropped, and said out loud.
    ok = (s > 0) & (s != float("inf"))
    if (~ok).sum():
        print(f"closes: dropped {int((~ok).sum())} non-positive/non-finite row(s)")
    s = s[ok]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def daily_closes(client, ticker: str) -> pd.Series:
    r = throttle(client.get_price_history_every_day, ticker)   # transient 429/502 retry
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} price_history -> HTTP {r.status_code}")
    s = closes_from_json(r.json())
    if s.empty:
        # B2: an empty (or all-garbage) payload is a FAILED pull, not a quiet
        # ticker -- raising routes it into skipped_closes where the zombie
        # gate can judge it; returning an empty series made an outage look
        # like a holiday.
        raise RuntimeError(f"{ticker} price_history returned no usable closes")
    return s


def _num(v):
    """float or None. Handles Schwab's 'NaN' string and actual NaN."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def chain_from_json(payload: dict, obs_date) -> pd.DataFrame:
    """Bounded PUT option-chain JSON -> engine chain rows. One row per contract;
    skips illiquid placeholders (missing/NaN delta, or bid<=0, or ask<=0)."""
    obs = pd.Timestamp(obs_date).normalize()
    und = _num(payload.get("underlyingPrice"))
    # B9: a non-positive underlying is not a market either
    if und is not None and und <= 0:
        raise ValueError(f"option chain payload has underlyingPrice {und}")
    if und is None:
        # Without a usable underlying price the whole chain is unmarkable; raise
        # so the caller (LiveMarket) skips this ONE ticker instead of producing
        # rows with underlying=None that crash step_one_day for the whole run.
        raise ValueError("option chain payload has no usable underlyingPrice")
    rows = (_rows_for(payload.get("putExpDateMap"), "P", obs, und)
            + _rows_for(payload.get("callExpDateMap"), "C", obs, und))
    return pd.DataFrame(rows, columns=_CHAIN_COLS)


def _rows_for(exp_map, right, obs, und):
    """Flatten one side (put or call) of Schwab's expDateMap to engine rows."""
    rows = []
    for exp_key, strikes in (exp_map or {}).items():
        # B3 amendment (group skeptic F1): a garbage expiry KEY or a non-dict
        # strikes group must kill only THIS group, not the ticker
        try:
            expiry = pd.Timestamp(exp_key.split(":")[0]).normalize()
            strike_items = list(strikes.items())
        except Exception as e:
            print(f"chain expiry group skipped ({exp_key!r}: {type(e).__name__})")
            continue
        for _strike_key, contracts in strike_items:
            try:
                ct = contracts[0]
                delta = _num(ct.get("delta"))
                bid, ask, mid = _num(ct.get("bid")), _num(ct.get("ask")), _num(ct.get("mark"))
                # skip illiquid/placeholder contracts (missing delta/mark or no market)
                if delta is None or bid is None or ask is None or mid is None \
                        or bid <= 0 or ask <= 0:
                    continue
                # B10: adjusted/non-standard contracts and multiplier != 100
                # break every x100 cost computation downstream. A10c: say so
                # -- this is the ONE place a contract-side corporate action
                # (OCC-adjusted deliverable after a split/special dividend)
                # becomes visible, and it used to drop in silence.
                m_ = _num(ct.get("multiplier"))
                if ct.get("nonStandard") is True or (m_ is not None and m_ != 100):
                    print(f"nonStandard/multiplier skip: "
                          f"{ct.get('symbol', ct.get('strikePrice'))} "
                          f"(multiplier={ct.get('multiplier')}, "
                          f"nonStandard={ct.get('nonStandard')}) -- possible "
                          f"corporate action on this underlying (A10c)")
                    continue
                rows.append({
                    "date": obs, "expiry": expiry,
                    "strike": float(ct["strikePrice"]), "right": right,
                    "dte": int(ct["daysToExpiration"]), "delta": delta,
                    "bid": bid, "ask": ask, "mid": mid, "underlying": und,
                    # A19: _num-coerced so a missing/"NaN" field lands as
                    # None/NaN, never a string that flips the column dtype
                    "open_interest": _num(ct.get("openInterest")),
                    "volume": _num(ct.get("totalVolume")),
                    "bid_size": _num(ct.get("bidSize")),
                    "ask_size": _num(ct.get("askSize")),
                })
            except (KeyError, TypeError, ValueError, IndexError) as e:
                # B3: ONE malformed contract used to raise out of the whole
                # chain and discard the TICKER for the day. Skip the row.
                print(f"chain row skipped ({type(e).__name__}: {e})")
                continue
    return rows


def chain_frame(client, ticker: str, target_dte: int, strike_count: int = 12,
                obs_date=None) -> pd.DataFrame:
    from schwab.client import Client
    # A22/B7: the request window must be stamped with the ET date. The VPS
    # clock is UTC, so dt.date.today() is already TOMORROW from 19:00-20:00 ET
    # onward -- a retry then asks Schwab for the wrong expiry window and
    # silently shifts the DTE band the selector uses.
    today = dt.datetime.now(_ET).date()
    # ALL: the wheel needs PUTS (entry) AND CALLS (covered-call leg after
    # assignment). Bounded by strike_count + date window (the full chain 502s).
    # Wrapped in throttle for transient 429/502 retry.
    r = throttle(client.get_option_chain, ticker,
                 contract_type=Client.Options.ContractType.ALL,
                 strike_count=strike_count, from_date=today,
                 to_date=today + dt.timedelta(days=target_dte + 20))
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} option_chain -> HTTP {r.status_code}")
    return chain_from_json(r.json(), obs_date or today)


def otm_call_frame(client, ticker: str, target_dte: int, obs_date=None) -> pd.DataFrame:
    """A4: every listed OUT-OF-THE-MONEY CALL strike for the same expiry
    window as chain_frame -- no strike_count guess. The 12-strike spot-centred
    window reaches ~+3.8% above spot; after the drawdown that caused an
    assignment the basis floor sits above that, and the covered call was
    unreachable on 31% of real 10%-drawdown ticker-days (audit A4). Measured
    alternative: a count-based fix needs strike_count~100 (8.3x the payload,
    on the endpoint documented to 502 at full width); strike_range=OTM gets
    exactly what the exchange lists in ~48 KB.

    CALL-only is load-bearing: ALL+OTM would add far-OTM PUTS as tradeable
    entry candidates for every account sharing the market."""
    from schwab.client import Client
    today = dt.datetime.now(_ET).date()   # A22/B7: ET, never the box clock
    r = throttle(client.get_option_chain, ticker,
                 contract_type=Client.Options.ContractType.CALL,
                 strike_range=Client.Options.StrikeRange.OUT_OF_THE_MONEY,
                 from_date=today, to_date=today + dt.timedelta(days=target_dte + 20))
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} otm_call_chain -> HTTP {r.status_code}")
    return chain_from_json(r.json(), obs_date or today)


def throttle(fn, *args, retries: int = 2, backoff: float = 1.0, **kwargs):
    """Call fn(*args, **kwargs); on a transient 429/502 Response, sleep and retry
    up to `retries` times. Returns the final Response (caller checks status)."""
    r = fn(*args, **kwargs)
    attempts = 0
    while getattr(r, "status_code", None) in (429, 502) and attempts < retries:
        time.sleep(backoff)
        r = fn(*args, **kwargs)
        attempts += 1
    return r
