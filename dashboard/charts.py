"""Themed Plotly figure builders. Views compose these; views never style charts."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from dashboard import labels, theme
from src.engine_v2.backtest import metrics_simple as m


def equity_curve(equity: pd.Series, benchmarks: dict) -> go.Figure:
    fig = go.Figure()
    for name, series in (benchmarks or {}).items():
        fig.add_trace(go.Scatter(
            x=series.index, y=series.to_numpy(), name=labels.label(name),
            mode="lines", line=dict(color=theme.MUTED, width=1.3, dash="dash"),
            hovertemplate="%{y:,.0f}<extra>" + labels.label(name) + "</extra>",
        ))
    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.to_numpy(), name="Strategy", mode="lines",
        line=dict(color=theme.ACCENT, width=2),
        fill="tozeroy", fillcolor="rgba(79,157,253,0.10)",
        hovertemplate="%{y:,.0f}<extra>Strategy</extra>",
    ))
    fig.update_layout(height=320, hovermode="x unified",
                      yaxis_title=None, xaxis_title=None)
    return theme.apply(fig)


def underwater(equity: pd.Series) -> go.Figure:
    dd = m.drawdown_series(equity)
    fig = go.Figure(go.Scatter(
        x=dd.index, y=dd.to_numpy(), name="Drawdown", mode="lines",
        line=dict(color=theme.NEGATIVE, width=1.4),
        fill="tozeroy", fillcolor="rgba(240,131,108,0.22)",
        hovertemplate="%{y:.1%}<extra>Drawdown</extra>",
    ))
    fig.update_layout(height=160, yaxis_tickformat=".0%",
                      yaxis_title=None, xaxis_title=None)
    return theme.apply(fig)


def yearly_bars(series: pd.Series, *, percent: bool) -> go.Figure:
    vals = series.to_numpy(dtype=float)
    colors = [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in vals]
    fig = go.Figure(go.Bar(
        x=[str(i) for i in series.index], y=vals, marker_color=colors,
        hovertemplate=("%{y:.1%}" if percent else "%{y:.2f}") + "<extra>%{x}</extra>",
    ))
    fig.update_layout(height=220, xaxis_title=None, yaxis_title=None)
    if percent:
        fig.update_layout(yaxis_tickformat=".0%")
    return theme.apply(fig)


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def monthly_heatmap(equity: pd.Series) -> go.Figure:
    grid = m.monthly_returns(equity)
    z = grid.to_numpy(dtype=float)
    lim = float(np.nanmax(np.abs(z))) if z.size and not np.isnan(z).all() else 0.01
    fig = go.Figure(go.Heatmap(
        z=z, x=_MONTHS, y=[str(y) for y in grid.index],
        colorscale=[[0.0, theme.NEGATIVE], [0.5, theme.PANEL], [1.0, theme.POSITIVE]],
        zmid=0, zmin=-lim, zmax=lim,
        xgap=2, ygap=2,
        hovertemplate="%{y} %{x}: %{z:.1%}<extra></extra>",
        colorbar=dict(tickformat=".0%", outlinewidth=0,
                      tickfont=dict(color=theme.MUTED)),
    ))
    fig.update_layout(height=60 + 26 * max(len(grid.index), 1),
                      xaxis_title=None, yaxis_title=None)
    return theme.apply(fig)
