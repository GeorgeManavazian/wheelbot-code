from src.engine_v2.regime.state import is_good_renting_weather


def _row(trend, vol):
    return {"trend": trend, "vol": vol}


def test_chop_normal_is_good():
    assert is_good_renting_weather(_row("chop", "normal")) is True


def test_chop_calm_is_good():
    assert is_good_renting_weather(_row("chop", "calm")) is True


def test_chop_stressed_is_not_good():
    assert is_good_renting_weather(_row("chop", "stressed")) is False


def test_uptrend_is_not_good():
    assert is_good_renting_weather(_row("uptrend", "normal")) is False


def test_downtrend_is_not_good():
    assert is_good_renting_weather(_row("downtrend", "normal")) is False


def test_none_row_is_not_good():
    assert is_good_renting_weather(None) is False


def _row2(trend, vol, ma50_vs_200):
    return {"trend": trend, "vol": vol, "ma50_vs_200": ma50_vs_200}


# --- ma-spread guard (2026-07-18): tight MAs = true chop; MAs pulling apart =
# a trend forming (the falling-knife / early-uptrend false positives). Opt-in ---
def test_ma_spread_guard_keeps_tight_chop():
    # BA-like: 50d only 1.7% from 200d, within a 3% band -> still good to rent
    assert is_good_renting_weather(_row2("chop", "normal", 0.017), max_ma_spread=0.03) is True


def test_ma_spread_guard_rejects_forming_downtrend():
    # VALE-like: MAs 6.7% apart -> reject
    assert is_good_renting_weather(_row2("chop", "normal", 0.067), max_ma_spread=0.03) is False


def test_ma_spread_guard_rejects_forming_uptrend():
    # WFC-like: 50d 3.9% below 200d (abs 3.9% > 3%) -> reject
    assert is_good_renting_weather(_row2("chop", "normal", -0.039), max_ma_spread=0.03) is False


def test_ma_spread_guard_default_off_is_unchanged():
    # no threshold -> old behavior; ma50_vs_200 is never consulted (may be absent)
    assert is_good_renting_weather(_row2("chop", "normal", 0.50)) is True
    assert is_good_renting_weather({"trend": "chop", "vol": "normal"}) is True
