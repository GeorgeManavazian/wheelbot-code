import pandas as pd
from dashboard.charts import posture_bands, candles_with_trades


def _log(pairs):
    # pairs: list of (date_str, posture) -> route_log tuples (trend/vol unused here)
    return [(pd.Timestamp(d), "n/a", "n/a", p) for d, p in pairs]


def test_posture_bands_merges_consecutive():
    log = _log([("2020-01-02", "TREND"), ("2020-01-03", "TREND"),
                ("2020-01-06", "WHEEL"), ("2020-01-07", "CASH")])
    bands = posture_bands(log)
    assert bands == [
        (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), "TREND"),
        (pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-06"), "WHEEL"),
        (pd.Timestamp("2020-01-07"), pd.Timestamp("2020-01-07"), "CASH"),
    ]


def test_posture_bands_single_posture():
    log = _log([("2020-01-02", "WHEEL"), ("2020-01-03", "WHEEL")])
    assert posture_bands(log) == [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), "WHEEL")]


def test_posture_bands_empty():
    assert posture_bands([]) == []


def _bars():
    idx = pd.date_range("2020-01-02", periods=4, freq="D")
    return pd.DataFrame({"Open": [1, 1, 1, 1], "High": [2, 2, 2, 2],
                         "Low": [0.5, 0.5, 0.5, 0.5], "Close": [1.5, 1.5, 1.5, 1.5]}, index=idx)


def _empty_overlay():
    return pd.DataFrame(columns=["group", "open_ended", "x0", "x1", "y", "hover"])


def test_candles_no_posture_has_no_vrect_shapes():
    fig = candles_with_trades(_bars(), _empty_overlay())
    assert len([s for s in fig.layout.shapes if s.type == "rect"]) == 0


def test_candles_with_posture_draws_one_rect_per_span():
    spans = [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), "TREND"),
             (pd.Timestamp("2020-01-04"), pd.Timestamp("2020-01-05"), "WHEEL")]
    fig = candles_with_trades(_bars(), _empty_overlay(), posture=spans)
    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rects) == 2
