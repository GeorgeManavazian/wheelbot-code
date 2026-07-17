import pandas as pd
from dashboard.charts import posture_bands


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
