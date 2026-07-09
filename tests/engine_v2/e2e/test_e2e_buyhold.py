import pandas as pd
from src.engine_v2.data.loader import load_bars

def test_buyhold_sharpe_stable():
    bars = load_bars(["SPY"], "2007-01-01", "2010-12-31")
    r = bars["SPY"]["Close"].pct_change().dropna()
    sr = r.mean() / r.std() * (252 ** 0.5)
    assert -2 < sr < 2  # sanity
