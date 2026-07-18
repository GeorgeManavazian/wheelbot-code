from live.universe import UNIVERSE


def test_universe_is_clean_and_sized():
    assert 400 <= len(UNIVERSE) <= 700
    assert all(isinstance(t, str) and t.isupper() and t.strip() == t for t in UNIVERSE)
    assert len(UNIVERSE) == len(set(UNIVERSE)), "no duplicate tickers"


def test_universe_spans_price_tiers_and_sectors():
    # cheap names a ~$5k account can trade (one put < ~$5k collateral)
    for cheap in ("GDX", "SLV", "F", "SOFI"):
        assert cheap in UNIVERSE, f"{cheap} (low-price inventory) missing"
    # expensive names only larger accounts reach
    for pricey in ("SPY", "QQQ"):
        assert pricey in UNIVERSE, f"{pricey} (high-price) missing"
