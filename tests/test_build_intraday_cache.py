import pandas as pd
import pytest
from scripts.build_intraday_cache import normalize_ticker

def _synthetic_one_day():
    # 04:00 -> 20:00 ET, 1-min, tz-aware; lowercase ohlcv
    idx = pd.date_range("2021-07-01 04:00", "2021-07-01 20:00", freq="min",
                        tz="America/New_York")
    n = len(idx)
    return pd.DataFrame(
        {"open": range(n), "high": range(n), "low": range(n),
         "close": range(n), "volume": range(n)}, index=idx)

def test_normalize_rth_only_390_bars_capitalized_tznaive():
    out = normalize_ticker(_synthetic_one_day(), "SPY", include_extended=False)
    # RTH 09:30..15:59 inclusive = 390 minutes
    assert len(out) == 390
    assert list(out.columns) == [("SPY", "Open"), ("SPY", "High"), ("SPY", "Low"),
                                 ("SPY", "Close"), ("SPY", "Volume")]
    assert out.index.tz is None
    assert out.index.min().strftime("%H:%M") == "09:30"
    assert out.index.max().strftime("%H:%M") == "15:59"

def test_normalize_include_extended_keeps_premarket():
    out = normalize_ticker(_synthetic_one_day(), "QQQ", include_extended=True)
    assert out.index.min().strftime("%H:%M") == "04:00"
    assert len(out) > 390
