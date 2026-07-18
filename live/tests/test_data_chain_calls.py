"""C1 fix: chain_from_json must include CALLS (covered-call leg), not just puts.
Uses the ALL fixture (both put + call maps). Plus the null-underlying guard."""
import json
import pandas as pd
import pytest
from live.data import chain_from_json

ALL = json.load(open("live/fixtures/option_chain_gdx_all.json"))
OBS = pd.Timestamp("2026-07-17")


def test_chain_has_both_puts_and_calls():
    df = chain_from_json(ALL, OBS)
    rights = set(df["right"])
    assert rights == {"P", "C"}, f"wheel needs both legs; got {rights}"
    assert (df[df["right"] == "P"]["delta"] < 0).all()    # puts negative
    assert (df[df["right"] == "C"]["delta"] > 0).all()    # calls positive
    assert (df["underlying"] == 71.32).all()
    assert (df["bid"] > 0).all() and (df["mid"] > 0).all()


def test_null_underlying_raises_so_caller_skips_ticker():
    bad = {"underlyingPrice": None, "putExpDateMap": {}, "callExpDateMap": {}}
    with pytest.raises(ValueError, match="underlyingPrice"):
        chain_from_json(bad, OBS)
