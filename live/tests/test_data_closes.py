import json
import pandas as pd
from live.data import closes_from_json

FIX = "live/fixtures/price_history_gdx.json"


def test_closes_mapping_shape_and_values():
    s = closes_from_json(json.load(open(FIX)))
    assert isinstance(s, pd.Series)
    assert isinstance(s.index, pd.DatetimeIndex)
    assert s.index.is_monotonic_increasing      # sorted by date
    assert not s.index.has_duplicates           # de-duplicated
    # spot-check the fixture's known first + last (dates normalized to midnight)
    assert s.loc[pd.Timestamp("2006-05-23")] == 37.96
    assert s.loc[pd.Timestamp("2026-07-17")] == 71.32
    assert s.name == "close"


def test_closes_empty_candles():
    s = closes_from_json({"candles": []})
    assert isinstance(s, pd.Series) and len(s) == 0 and s.name == "close"
