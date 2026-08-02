import json
import pandas as pd
from live.data import chain_from_json, _CHAIN_COLS

FIX = "live/fixtures/option_chain_gdx_puts.json"
OBS = pd.Timestamp("2026-07-17")


def _payload():
    return json.load(open(FIX))


def test_chain_exact_columns_and_types():
    df = chain_from_json(_payload(), OBS)
    assert list(df.columns) == _CHAIN_COLS
    assert (df["right"] == "P").all()
    assert (df["date"] == OBS).all()
    assert (df["underlying"] == 71.32).all()
    assert (df["delta"] < 0).all()              # puts are negative
    assert (df["bid"] <= df["ask"]).all()
    assert (df["bid"] > 0).all() and (df["ask"] > 0).all()   # placeholders skipped


def test_chain_captures_per_leg_quote_times():
    """C1: the chain payload carries per-contract quoteTimeInLong /
    tradeTimeInLong (proven on the real captured GDX payload) and both were
    discarded -- the pre-A19 class. Captured, not consumed: no gate reads
    them yet; they accrue from ship day (no historical chain endpoint)."""
    payload = _payload()
    df = chain_from_json(payload, OBS)
    for col in ("quote_time", "trade_time"):
        assert col in df.columns, f"C1: {col} discarded from the chain"
    row = df.iloc[0]
    raw = None
    for exp_key, strikes in payload["putExpDateMap"].items():
        for _sk, cts in strikes.items():
            ct = cts[0]
            if (float(ct["strikePrice"]) == row["strike"]
                    and int(ct["daysToExpiration"]) == row["dte"]):
                raw = ct
    assert raw is not None
    if raw.get("quoteTimeInLong") is not None:
        assert row["quote_time"] == float(raw["quoteTimeInLong"])
    if raw.get("tradeTimeInLong") is not None:
        assert row["trade_time"] == float(raw["tradeTimeInLong"])


def test_chain_captures_liquidity_fields():
    """A19: openInterest/totalVolume/bidSize/askSize must be captured, not
    discarded -- they are the prerequisite for any size-aware fill model (A2)
    and replace the ADV proxy chain (x3.6 typical error) with measurement.
    Schwab has no historical chain endpoint, so every day they are dropped is
    data lost forever."""
    payload = _payload()
    df = chain_from_json(payload, OBS)
    for col in ("open_interest", "volume", "bid_size", "ask_size"):
        assert col in df.columns, f"A19: {col} discarded from the chain"
    # values must come from the payload, not be invented: cross-check one
    # admitted contract against its raw source record
    row = df.iloc[0]
    raw = None
    for exp_key, strikes in payload["putExpDateMap"].items():
        for _sk, cts in strikes.items():
            ct = cts[0]
            if (float(ct["strikePrice"]) == row["strike"]
                    and int(ct["daysToExpiration"]) == row["dte"]):
                raw = ct
    assert raw is not None
    assert row["open_interest"] == float(raw["openInterest"])
    assert row["volume"] == float(raw["totalVolume"])
    assert row["bid_size"] == float(raw["bidSize"])
    assert row["ask_size"] == float(raw["askSize"])


def test_chain_skips_zero_bid_placeholder():
    # fixture's first contract (strike 66.0) has bid 0.0 -> must be absent
    df = chain_from_json(_payload(), OBS)
    zero_bid_66 = df[(df["strike"] == 66.0) & (df["dte"] == 0)]
    assert len(zero_bid_66) == 0


def test_chain_spot_check_a_live_contract():
    # find the first contract in the fixture with bid>0 and verify its row maps 1:1
    payload = _payload()
    exp_key, strikes = next(iter(payload["putExpDateMap"].items()))
    picked = None
    for strike_key, contracts in strikes.items():
        ct = contracts[0]
        if ct["bid"] > 0 and ct["ask"] > 0 and ct["delta"] == ct["delta"]:
            picked = (exp_key, ct); break
    assert picked is not None, "fixture should contain a positive-bid put"
    exp_key, ct = picked
    df = chain_from_json(payload, OBS)
    row = df[(df["expiry"] == pd.Timestamp(exp_key.split(":")[0]))
             & (df["strike"] == float(ct["strikePrice"]))].iloc[0]
    assert row["bid"] == ct["bid"]
    assert row["ask"] == ct["ask"]
    assert row["mid"] == ct["mark"]
    assert row["delta"] == ct["delta"]
    assert row["dte"] == ct["daysToExpiration"]


def test_chain_empty_map():
    df = chain_from_json({"underlyingPrice": 10.0, "putExpDateMap": {}}, OBS)
    assert list(df.columns) == _CHAIN_COLS and len(df) == 0
