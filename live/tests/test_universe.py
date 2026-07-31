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


def test_reserved_out_of_sample_tickers_are_not_in_the_live_universe():
    """XBI/EEM/EWZ/TLT/ARKK are held back as a genuinely unseen final read.
    The engine's refusal (portfolio.py RESERVED_TICKERS) only runs on the
    9-ticker default path, and the live bot passes an explicit 547-name
    universe into LiveMarket -- so nothing whatsoever stopped these from being
    traded. Audit 2026-07-31: on 07-20 the bot traded RIG at rank #20 while
    ARKK sat at #19, and on 07-31 EWZ ranked #7 of 12 gate passers. The reserve
    was one chain pull from being spent, silently and unrecoverably."""
    from src.engine_v2.options.portfolio import RESERVED_TICKERS
    leaked = [t for t in RESERVED_TICKERS if t in UNIVERSE]
    assert leaked == [], f"reserved out-of-sample tickers are live: {leaked}"
