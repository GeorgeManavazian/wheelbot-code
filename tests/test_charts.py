import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dashboard import charts, theme

def _equity(n=400):
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(np.linspace(100.0, 130.0, n), index=idx)

def test_equity_curve_has_strategy_plus_each_benchmark():
    eq = _equity()
    bm = {"SPY": eq * 0.9, "60_40": eq * 0.8}
    fig = charts.equity_curve(eq, bm)
    names = [t.name for t in fig.data]
    assert "Strategy" in names
    assert "SPY buy & hold" in names   # via labels.label
    assert "60/40" in names            # the raw "60_40" must never reach the legend
    assert fig.layout.paper_bgcolor == theme.BG

def test_equity_curve_survives_no_benchmarks():
    # SPY can be absent from the universe — this crashed before the July 10 merge.
    fig = charts.equity_curve(_equity(), {})
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1

def test_underwater_is_non_positive():
    fig = charts.underwater(_equity())
    ys = np.asarray(fig.data[0].y, dtype=float)
    assert np.nanmax(ys) <= 0.0 + 1e-9

def test_yearly_bars_colour_by_sign():
    s = pd.Series([0.2, -0.1], index=[2023, 2024])
    fig = charts.yearly_bars(s, percent=True)
    colors = list(fig.data[0].marker.color)
    assert colors == [theme.POSITIVE, theme.NEGATIVE]

def test_monthly_heatmap_is_years_by_twelve_months():
    fig = charts.monthly_heatmap(_equity())
    z = np.asarray(fig.data[0].z)
    assert z.shape[1] == 12
