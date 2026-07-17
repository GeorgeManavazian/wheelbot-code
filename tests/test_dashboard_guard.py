import os
import pandas as pd
from dashboard import guard


def _touch_chain(data_dir, ticker):
    p = os.path.join(data_dir, f"{ticker.lower()}_greeks_eod_all.parquet")
    pd.DataFrame({"date": [pd.Timestamp("2020-01-02")], "underlying": [1.0]}).to_parquet(p)


def test_seen_sources_is_allow_list(tmp_path):
    d = str(tmp_path)
    for t in ["SPY", "GDX", "SLV", "XOP", "XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ", "ZZZ"]:
        _touch_chain(d, t)
    out = guard.seen_sources(data_dir=d)
    assert set(out) == {"SPY", "GDX", "SLV", "XOP"}          # only SEEN
    for unseen in ["XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"]:
        assert unseen not in out
    assert "ZZZ" not in out                                   # novel ticker excluded by default


def test_seen_sources_adds_fixture_when_present(tmp_path):
    d = str(tmp_path)
    _touch_chain(d, "SPY")
    fx = os.path.join(d, "fixture.parquet")
    pd.DataFrame({"date": [pd.Timestamp("2020-01-02")]}).to_parquet(fx)
    out = guard.seen_sources(data_dir=d, include_fixture_name="SPY (fixture)", fixture_path=fx)
    assert out["SPY (fixture)"] == fx


def test_seen_sources_skips_missing_fixture(tmp_path):
    d = str(tmp_path)
    _touch_chain(d, "SPY")
    out = guard.seen_sources(data_dir=d, include_fixture_name="X", fixture_path=os.path.join(d, "nope.parquet"))
    assert "X" not in out
