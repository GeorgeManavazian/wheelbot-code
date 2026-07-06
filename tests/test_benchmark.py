"""SPY buy-hold benchmark math (dashboard-side, temporary until engine rows)."""
import pandas as pd
import pytest

from dashboard.benchmark import FRIENDLY, LABEL, benchmark_row, spy_equity


def _playground(tickers=("SPY", "QQQ")):
    dates = pd.date_range("2020-01-01", periods=5, freq="B")
    rows = []
    for t in tickers:
        for i, d in enumerate(dates):
            px = 100.0 + i if t == "SPY" else 50.0
            rows.append({"date": d, "open": px, "high": px, "low": px,
                         "close": px, "volume": 1000, "ticker": t})
    return pd.DataFrame(rows)


def test_spy_equity_starts_at_100k_and_tracks_close():
    eq = spy_equity(_playground())
    assert eq.iloc[0] == pytest.approx(100_000)
    assert eq.iloc[-1] == pytest.approx(100_000 * 104 / 100)
    assert eq.index.is_monotonic_increasing


def test_spy_missing_returns_none():
    assert spy_equity(_playground(tickers=("QQQ",))) is None


def test_benchmark_row_fields():
    row = benchmark_row(spy_equity(_playground()))
    assert row["label"] == LABEL and row["name"] == "benchmark"
    assert row["sample_flag"] == "—"
    assert row["turnover"] == 0.0 and row["exposure"] == 1.0
    for key in ("sharpe", "cagr", "max_dd", "positive_years", "total_years",
                "best_year", "worst_year", "top2_share", "n_trades"):
        assert key in row
    assert FRIENDLY.startswith("S&P 500")
