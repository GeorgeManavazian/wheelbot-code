import pandas as pd
import pytest

from src.engine import qc


def make_df(closes, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes,
         "close": closes, "volume": [1000] * len(closes)},
        index=idx,
    )


def test_check_prices_flags_zero_and_negative():
    df = make_df([100.0, 0.0, -5.0, 100.0])
    violations = qc.check_prices(df)
    assert len(violations) == 2


def test_check_prices_flags_nan():
    df = make_df([100.0, float("nan"), 100.0])
    assert len(qc.check_prices(df)) == 1


def test_check_prices_passes_clean_data():
    assert qc.check_prices(make_df([100.0, 101.0, 102.0])) == []


def test_check_calendar_flags_missing_dates():
    ref = make_df([100.0] * 5)          # 5 business days
    df = make_df([100.0] * 5).drop(ref.index[2])  # missing day 3
    violations = qc.check_calendar(df, ref)
    assert len(violations) == 1
    assert str(ref.index[2].date()) in violations[0]


def test_check_calendar_ignores_pre_inception():
    ref = make_df([100.0] * 10)
    late = make_df([100.0] * 5, start=str(ref.index[5].date()))
    assert qc.check_calendar(late, ref) == []


def test_inception_report():
    frames = {"A": make_df([1.0] * 3), "B": make_df([1.0] * 3, start="2021-06-01")}
    rep = qc.inception_report(frames)
    assert rep.loc["B", "first_date"] > rep.loc["A", "first_date"]


def test_spy_adjustment_detects_identical_series():
    adj = make_df([100.0, 101.0, 102.0])
    violations = qc.check_spy_adjustment(adj, adj.copy())
    assert len(violations) == 1  # identical = adjustment never applied
