from src.engine.universe import TICKERS

def test_universe_has_20_unique_tickers():
    assert len(TICKERS) == 20
    assert len(set(TICKERS)) == 20
    assert "SPY" in TICKERS and "TLT" in TICKERS
