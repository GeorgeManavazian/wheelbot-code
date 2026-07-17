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
