# Dashboard Visual Overhaul — Implementation Plan (Stage 1: Foundation + Run page)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Run page's default Streamlit charts with a themed dark Plotly dashboard, restore human-readable column names, and add the underwater / monthly-heatmap / vs-buy-hold / cost-sensitivity panels.

**Architecture:** Three new dashboard-layer modules with one responsibility each — `labels.py` (raw→English names), `theme.py` (one Plotly template + palette), `charts.py` (figure builders). Views compose them; views never hardcode a color or a column header. One new engine metric (`monthly_returns`) lands in `metrics_simple` beside the metrics it resembles. No engine behavior changes.

**Tech Stack:** Python, Streamlit (`>=1.37`), Plotly (`>=5.20`, already a declared dependency, currently imported zero times), pandas, pytest + `streamlit.testing.v1.AppTest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-dashboard-visual-overhaul-design.md`
- Branch: `dashboard-visual-overhaul` (already created, spec committed at `62ed8fe`)
- Palette, exact values — Background `#0e1117`, Panel `#161b24`, Border `#232a36`, Text `#e6edf3`, Muted `#7d8798`, Accent `#4f9dfd`, Positive `#3fb950`, Negative `#f0836c`, Warning `#e3b341`
- Run tests with `.venv/bin/python -m pytest`. Baseline: **180 tests passing**. Never finish a task with fewer passing.
- **No new strategy knobs, verdicts, scores, or gates.** The July 10 decision parked those deliberately.
- Views must not hardcode chart colors or column headers. They call `theme` and `labels`.
- Streamlit app entry: `.venv/bin/python -m streamlit run dashboard/app.py` from the repo root.

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/labels.py` | **create** — single raw→display name map + `humanize()` |
| `dashboard/theme.py` | **create** — palette constants + one registered Plotly template |
| `dashboard/charts.py` | **create** — figure builders (equity, underwater, yearly bars, monthly heatmap) |
| `dashboard/sensitivity.py` | **create** — cost sweep (re-runs backtest across spread levels) |
| `src/engine_v2/backtest/metrics_simple.py` | **modify** — add `monthly_returns()` |
| `.streamlit/config.toml` | **modify** — light → dark palette |
| `dashboard/views/run.py` | **modify** — rewire to the above |
| `tests/test_labels.py` | **create** |
| `tests/test_theme.py` | **create** |
| `tests/test_charts.py` | **create** |
| `tests/test_sensitivity.py` | **create** |
| `tests/engine_v2/test_metrics_simple.py` | **modify** — `monthly_returns` cases |
| `tests/test_workbench_dashboard.py` | **modify** — new-panel assertions |

`drawdown_series(equity)` **already exists** in `metrics_simple` — the underwater panel needs no new math.

---

### Task 1: `labels.py` — the single raw→English map

The 2026-07-06 friendly names died because they lived scattered in view code and a view rewrite ate them. One module, so the next rewrite has one file to notice.

**Files:**
- Create: `dashboard/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: nothing
- Produces: `COLUMN_LABELS: dict[str, str]`; `label(name: str) -> str` (returns a prettified fallback — `str.replace("_", " ").capitalize()` — for unmapped names, never raises); `humanize(df: pd.DataFrame) -> pd.DataFrame` (returns a **copy** with renamed columns; never mutates its argument)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_labels.py
import pandas as pd
from dashboard import labels

def test_known_columns_map_to_english():
    assert labels.label("cost_to_close") == "Cost to close"
    assert labels.label("pct_of_credit") == "Credit kept"
    assert labels.label("realized_pnl") == "Realized P&L"
    assert labels.label("days_held") == "Days held"
    assert labels.label("qty") == "Contracts"
    assert labels.label("mean_ret") == "Mean return"
    assert labels.label("60_40") == "60/40"

def test_unknown_column_falls_back_to_prettified_name():
    assert labels.label("some_new_column") == "Some new column"

def test_humanize_renames_and_does_not_mutate():
    df = pd.DataFrame({"realized_pnl": [1.0], "days_held": [3]})
    out = labels.humanize(df)
    assert list(out.columns) == ["Realized P&L", "Days held"]
    assert list(df.columns) == ["realized_pnl", "days_held"]  # original untouched

def test_every_blotter_and_regime_column_is_mapped():
    # Columns the app actually renders. None may fall through to the fallback.
    blotter = ["opened", "closed", "instrument", "strike", "expiry", "qty", "credit",
               "outcome", "cost_to_close", "realized_pnl", "pct_of_credit", "days_held"]
    regime = ["dimension", "regime", "sharpe", "mean_ret", "bars"]
    for col in blotter + regime:
        assert col in labels.COLUMN_LABELS, f"{col} has no explicit label"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.labels'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/labels.py
"""Single source of display names. Views must never hardcode a column header.

The 2026-07-06 friendly names were lost in the 2026-07-10 view rewrite because
they were scattered across view code. Keeping them here means a future rewrite
has exactly one file to notice.
"""
import pandas as pd

COLUMN_LABELS: dict[str, str] = {
    # Blotter
    "opened": "Opened",
    "closed": "Closed",
    "instrument": "Instrument",
    "strike": "Strike",
    "expiry": "Expiry",
    "qty": "Contracts",
    "credit": "Credit taken",
    "outcome": "Outcome",
    "cost_to_close": "Cost to close",
    "realized_pnl": "Realized P&L",
    "pct_of_credit": "Credit kept",
    "days_held": "Days held",
    # Regime table
    "dimension": "Dimension",
    "regime": "Regime",
    "sharpe": "Sharpe",
    "mean_ret": "Mean return",
    "bars": "Bars",
    # Benchmarks
    "SPY": "SPY buy & hold",
    "60_40": "60/40",
    "strategy": "Strategy",
    # Metrics
    "cagr": "CAGR",
    "max_drawdown": "Max drawdown",
    "total_return": "Total return",
    "spread_bps": "Spread (bps)",
}


def label(name: str) -> str:
    """Display name for a raw column. Unmapped names get a prettified fallback."""
    if name in COLUMN_LABELS:
        return COLUMN_LABELS[name]
    return str(name).replace("_", " ").capitalize()


def humanize(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of `df` with display column names. Never mutates the argument."""
    return df.rename(columns={c: label(c) for c in df.columns})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_labels.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add dashboard/labels.py tests/test_labels.py
git commit -m "feat: central raw-to-display label map for dashboard tables"
```

---

### Task 2: `theme.py` + dark Streamlit config

**Files:**
- Create: `dashboard/theme.py`
- Modify: `.streamlit/config.toml` (whole file replaced)
- Test: `tests/test_theme.py`

**Interfaces:**
- Consumes: nothing
- Produces: colour constants `BG`, `PANEL`, `BORDER`, `TEXT`, `MUTED`, `ACCENT`, `POSITIVE`, `NEGATIVE`, `WARNING` (all `str` hex); `TEMPLATE_NAME: str = "quant_dark"`; `register() -> None` (idempotent — registers the Plotly template; safe to call on every Streamlit rerun); `apply(fig: go.Figure) -> go.Figure` (sets the template + shared layout, returns the same figure)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_theme.py
import plotly.graph_objects as go
import plotly.io as pio
from dashboard import theme

def test_register_is_idempotent():
    theme.register()
    theme.register()  # a Streamlit rerun calls this again; must not raise
    assert theme.TEMPLATE_NAME in pio.templates

def test_apply_sets_the_template():
    theme.register()
    fig = theme.apply(go.Figure())
    assert fig.layout.template is not None
    assert fig.layout.paper_bgcolor == theme.BG

def test_palette_matches_the_spec():
    assert theme.BG == "#0e1117"
    assert theme.PANEL == "#161b24"
    assert theme.ACCENT == "#4f9dfd"
    assert theme.POSITIVE == "#3fb950"
    assert theme.NEGATIVE == "#f0836c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.theme'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/theme.py
"""One Plotly template + the palette. Views must never hardcode a colour."""
import plotly.graph_objects as go
import plotly.io as pio

BG = "#0e1117"
PANEL = "#161b24"
BORDER = "#232a36"
TEXT = "#e6edf3"
MUTED = "#7d8798"
ACCENT = "#4f9dfd"
POSITIVE = "#3fb950"
NEGATIVE = "#f0836c"
WARNING = "#e3b341"

TEMPLATE_NAME = "quant_dark"


def register() -> None:
    """Register the Plotly template. Idempotent — Streamlit reruns call this often."""
    if TEMPLATE_NAME in pio.templates:
        return
    pio.templates[TEMPLATE_NAME] = go.layout.Template(
        layout=go.Layout(
            paper_bgcolor=BG,
            plot_bgcolor=BG,
            font=dict(color=TEXT, family="Inter, system-ui, sans-serif", size=12),
            xaxis=dict(gridcolor=BORDER, linecolor=BORDER, zerolinecolor=BORDER,
                       tickfont=dict(color=MUTED)),
            yaxis=dict(gridcolor=BORDER, linecolor=BORDER, zerolinecolor=BORDER,
                       tickfont=dict(color=MUTED)),
            colorway=[ACCENT, MUTED, WARNING, POSITIVE, NEGATIVE],
            margin=dict(l=10, r=10, t=30, b=10),
            hoverlabel=dict(bgcolor=PANEL, bordercolor=BORDER,
                            font=dict(color=TEXT)),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED)),
        )
    )


def apply(fig: go.Figure) -> go.Figure:
    """Apply the template to a figure. Returns the same figure for chaining."""
    register()
    fig.update_layout(template=TEMPLATE_NAME, paper_bgcolor=BG, plot_bgcolor=BG)
    return fig
```

- [ ] **Step 4: Replace `.streamlit/config.toml`**

```toml
[theme]
base = "dark"
primaryColor = "#4f9dfd"
backgroundColor = "#0e1117"
secondaryBackgroundColor = "#161b24"
textColor = "#e6edf3"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_theme.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Commit**

```bash
git add dashboard/theme.py .streamlit/config.toml tests/test_theme.py
git commit -m "feat: dark quant Plotly template and Streamlit theme"
```

---

### Task 3: `monthly_returns()` in `metrics_simple`

**Files:**
- Modify: `src/engine_v2/backtest/metrics_simple.py` (append)
- Test: `tests/engine_v2/test_metrics_simple.py` (append)

**Interfaces:**
- Consumes: nothing
- Produces: `monthly_returns(equity: pd.Series) -> pd.DataFrame` — index is `int` year, columns are `int` 1..12 (always all twelve, in order), values are that month's fractional return, `NaN` where the month has no data. Computed from month-end equity, so a month's return is `last/prev_last - 1`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/engine_v2/test_metrics_simple.py
import numpy as np
import pandas as pd
from src.engine_v2.backtest import metrics_simple as m

def test_monthly_returns_shape_and_values():
    # 100 -> 110 in Jan, 110 -> 99 in Feb
    idx = pd.to_datetime(["2024-01-15", "2024-01-31", "2024-02-15", "2024-02-29"])
    eq = pd.Series([100.0, 110.0, 105.0, 99.0], index=idx)
    out = m.monthly_returns(eq)
    assert list(out.columns) == list(range(1, 13))
    assert out.index.tolist() == [2024]
    # Jan is the first month: return measured off the opening equity (100 -> 110)
    assert out.loc[2024, 1] == pytest.approx(0.10)
    # Feb: 110 -> 99
    assert out.loc[2024, 2] == pytest.approx(-0.10)
    assert np.isnan(out.loc[2024, 5])  # no data in May

def test_monthly_returns_spans_years():
    idx = pd.date_range("2023-11-01", "2024-02-29", freq="D")
    eq = pd.Series(np.linspace(100.0, 120.0, len(idx)), index=idx)
    out = m.monthly_returns(eq)
    assert out.index.tolist() == [2023, 2024]
    assert not np.isnan(out.loc[2023, 12])
    assert np.isnan(out.loc[2023, 1])

def test_monthly_returns_empty_series_returns_empty_frame():
    out = m.monthly_returns(pd.Series(dtype=float))
    assert out.empty
```

Add `import pytest` at the top of the test file if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/engine_v2/test_metrics_simple.py -k monthly -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'monthly_returns'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/engine_v2/backtest/metrics_simple.py

def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    """Month-by-month fractional returns as a year x month grid (columns 1..12).

    The first month's return is measured off the opening equity, so a backtest
    that starts mid-month still reports that month. NaN where no data.
    """
    if equity.empty:
        return pd.DataFrame()
    month_end = equity.resample("ME").last()
    prev = month_end.shift(1)
    prev.iloc[0] = equity.iloc[0]  # first month: measure off opening equity
    rets = month_end / prev - 1.0
    grid = pd.DataFrame({
        "year": rets.index.year,
        "month": rets.index.month,
        "ret": rets.to_numpy(),
    })
    out = grid.pivot(index="year", columns="month", values="ret")
    return out.reindex(columns=range(1, 13))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/engine_v2/test_metrics_simple.py -v`
Expected: PASS — the three new tests plus all pre-existing ones

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/backtest/metrics_simple.py tests/engine_v2/test_metrics_simple.py
git commit -m "feat: monthly_returns year-by-month grid metric"
```

---

### Task 4: `charts.py` — the figure builders

**Files:**
- Create: `dashboard/charts.py`
- Test: `tests/test_charts.py`

**Interfaces:**
- Consumes: `dashboard.theme` (`apply`, colour constants), `dashboard.labels` (`label`), `src.engine_v2.backtest.metrics_simple` (`drawdown_series`, `monthly_returns`)
- Produces, all returning a themed `go.Figure`:
  - `equity_curve(equity: pd.Series, benchmarks: dict[str, pd.Series]) -> go.Figure` — strategy in `ACCENT` with a filled area; each benchmark a dashed muted line, legend-named via `labels.label` (so `60_40` renders as "60/40"). Tolerates an **empty** `benchmarks` dict.
  - `underwater(equity: pd.Series) -> go.Figure` — filled drawdown in `NEGATIVE`, y as percent
  - `yearly_bars(series: pd.Series, *, percent: bool) -> go.Figure` — one bar per year, `POSITIVE` when >= 0 and `NEGATIVE` when < 0. `percent=True` formats the y-axis and hover as percent (returns); `percent=False` leaves raw (Sharpe).
  - `monthly_heatmap(equity: pd.Series) -> go.Figure` — years as rows, Jan..Dec as columns, red→green diverging scale centred on zero

- [ ] **Step 1: Write the failing test**

```python
# tests/test_charts.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.charts'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/charts.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add dashboard/charts.py tests/test_charts.py
git commit -m "feat: themed Plotly figure builders (equity, underwater, yearly, heatmap)"
```

---

### Task 5: `sensitivity.py` — the cost sweep

**Files:**
- Create: `dashboard/sensitivity.py`
- Test: `tests/test_sensitivity.py`

**Interfaces:**
- Consumes: `src.engine_v2.backtest.simple.run_simple`, `BacktestConfig`
- Produces: `SPREAD_LEVELS: tuple = (0.0, 1.0, 2.0, 5.0, 10.0)`; `cost_sweep(strategy_cls, bars, base_config, benchmark_bars=None, spreads=SPREAD_LEVELS) -> pd.DataFrame` with columns `spread_bps`, `cagr`, `sharpe` — one row per spread level, in the given order. Re-runs the backtest once per level, holding every other knob (including `borrow_bps_annual`) at `base_config`'s value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sensitivity.py
import pandas as pd
from dashboard import sensitivity
from src.engine_v2.backtest.orchestrator import BacktestConfig
from src.engine_v2.data.source import default_source
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy

def test_cost_sweep_returns_one_row_per_spread_level():
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    out = sensitivity.cost_sweep(strat, bars, BacktestConfig(),
                                 spreads=(0.0, 5.0))
    assert list(out.columns) == ["spread_bps", "cagr", "sharpe"]
    assert out["spread_bps"].tolist() == [0.0, 5.0]
    assert out["cagr"].notna().all()

def test_higher_spread_never_helps():
    # More friction cannot raise CAGR. If it does, costs are not being charged.
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    out = sensitivity.cost_sweep(strat, bars, BacktestConfig(),
                                 spreads=(0.0, 25.0))
    assert out.loc[1, "cagr"] <= out.loc[0, "cagr"] + 1e-9

def test_borrow_is_held_at_the_base_config_value():
    src = default_source()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "TLT"], lo, hi)
    strat = get_strategy(list(STRATEGIES)[0])
    base = BacktestConfig(borrow_bps_annual=500.0)
    out = sensitivity.cost_sweep(strat, bars, base, spreads=(1.0,))
    # Same spread + same borrow as a direct run => identical CAGR.
    from src.engine_v2.backtest.simple import run_simple
    direct = run_simple(strat, bars,
                        config=BacktestConfig(spread_bps_per_side=1.0,
                                              borrow_bps_annual=500.0))
    assert out.loc[0, "cagr"] == direct.cagr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sensitivity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.sensitivity'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/sensitivity.py
"""Cost sensitivity: where does the edge die? Re-runs the backtest per spread level.

This is the only expensive panel in the dashboard — it runs the backtest once per
level, so the Run page puts it behind an explicit button, never on every run.
"""
import dataclasses
import pandas as pd

from src.engine_v2.backtest.orchestrator import BacktestConfig
from src.engine_v2.backtest.simple import run_simple

SPREAD_LEVELS: tuple = (0.0, 1.0, 2.0, 5.0, 10.0)


def cost_sweep(strategy_cls, bars, base_config: BacktestConfig,
               benchmark_bars=None, spreads=SPREAD_LEVELS) -> pd.DataFrame:
    rows = []
    for spread in spreads:
        cfg = dataclasses.replace(base_config, spread_bps_per_side=float(spread))
        res = run_simple(strategy_cls, bars, config=cfg,
                         benchmark_bars=benchmark_bars)
        rows.append({"spread_bps": float(spread),
                     "cagr": res.cagr,
                     "sharpe": res.sharpe})
    return pd.DataFrame(rows, columns=["spread_bps", "cagr", "sharpe"])
```

`BacktestConfig` is a dataclass (verified — fields: `target_risk`, `n_folds`, `cpcv_k`,
`starting_equity`, `htb`, `spread_bps_per_side`, `spread_bps_by_ticker`, `borrow_bps_annual`,
`borrow_bps_by_ticker`), so `dataclasses.replace` is safe and copies every other knob untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sensitivity.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add dashboard/sensitivity.py tests/test_sensitivity.py
git commit -m "feat: cost sensitivity sweep across spread levels"
```

---

### Task 6: Rewire the Run page

The visible payoff. Every `st.line_chart` / `st.bar_chart` in `views/run.py` dies here.

**Files:**
- Modify: `dashboard/views/run.py:53-72` (the whole results block below `st.button("Run")`)
- Modify: `tests/test_workbench_dashboard.py` (append)

**Interfaces:**
- Consumes: `charts.equity_curve`, `charts.underwater`, `charts.yearly_bars`, `charts.monthly_heatmap`, `labels.humanize`, `sensitivity.cost_sweep`, `sensitivity.SPREAD_LEVELS`, `metrics_simple.cagr`
- Produces: nothing (leaf view)

**The vs-Buy-&-Hold tile — read this before writing it.** `res.benchmarks` is `{}` when SPY is
absent from the universe. Deselecting SPY caused a `KeyError` crash that the whole-branch review
caught before the July 10 merge. The tile must render `"—"` in that case, not raise. There is a
test for exactly this.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_workbench_dashboard.py
from streamlit.testing.v1 import AppTest

def test_run_page_shows_four_hero_tiles_including_vs_buy_hold():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert len(at.metric) >= 4
    assert any("Buy" in m.label for m in at.metric)

def test_run_page_renders_plotly_charts_not_default_streamlit_charts():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    # equity, underwater, yearly sharpe, yearly return, monthly heatmap
    assert len(at.get("plotly_chart")) >= 5

def test_vs_buy_hold_tile_degrades_when_spy_absent():
    # No SPY in the universe => res.benchmarks is {}. Must not crash.
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    vs = [m for m in at.metric if "Buy" in m.label]
    assert vs and vs[0].value == "—"

def test_cost_sweep_is_behind_a_button_and_does_not_run_automatically():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert any(b.key == "cost_sweep" for b in at.button)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workbench_dashboard.py -v`
Expected: the four new tests FAIL (only 3 metrics today; zero plotly charts; no `cost_sweep` button)

- [ ] **Step 3: Rewrite the results block**

Replace `dashboard/views/run.py` lines 53-72 (everything from `st.subheader(f"Headline …")` to the
end of the function) with:

```python
        st.subheader(f"Headline — recent since {res.recent['start']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CAGR", f"{res.recent['cagr']:.2%}")
        c2.metric("Sharpe", f"{res.recent['sharpe']:.2f}")
        c3.metric("Max drawdown", f"{res.recent['max_drawdown']:.2%}")

        # vs buy & hold: benchmarks is {} when SPY isn't in the universe.
        spy = res.benchmarks.get("SPY")
        if spy is None:
            c4.metric("vs Buy & Hold", "—", help="SPY is not in the selected universe.")
        else:
            spy_recent = spy[spy.index >= res.recent["start"]]
            spy_cagr = mx.cagr(spy_recent, res.periods_per_year)
            gap = res.recent["cagr"] - spy_cagr
            c4.metric("vs Buy & Hold", f"{gap:+.2%}",
                      help=f"SPY buy & hold did {spy_cagr:.2%} over the same window.")

        st.caption(f"Full history: CAGR {res.cagr:.2%} · Sharpe {res.sharpe:.2f} · "
                   f"maxDD {res.max_drawdown:.2%} · trades {res.trades}")

        st.subheader("Equity curve")
        st.plotly_chart(charts.equity_curve(res.equity, res.benchmarks),
                        use_container_width=True)
        st.subheader("Underwater — how deep, and how long")
        st.plotly_chart(charts.underwater(res.equity), use_container_width=True)

        left, right = st.columns(2)
        with left:
            st.subheader("Year-by-year Sharpe")
            st.caption("The decay curve. Never headline one blended number.")
            st.plotly_chart(charts.yearly_bars(res.yearly_sharpe, percent=False),
                            use_container_width=True)
        with right:
            st.subheader("Year-by-year return")
            st.plotly_chart(charts.yearly_bars(res.yearly, percent=True),
                            use_container_width=True)

        st.subheader("Monthly returns")
        st.plotly_chart(charts.monthly_heatmap(res.equity), use_container_width=True)

        st.subheader("By regime")
        st.dataframe(labels.humanize(res.regime), use_container_width=True)
        st.caption(f"bars/yr ≈ {res.periods_per_year:.0f}")

        st.session_state["_sweep_args"] = (name, tickers, start, end, spread, borrow)

    # Cost sensitivity: re-runs the backtest once per spread level, so it sits
    # behind its own button and never fires on a normal run.
    if st.session_state.get("_sweep_args"):
        st.subheader("Cost sensitivity")
        st.caption(f"Re-runs at spreads {list(sensitivity.SPREAD_LEVELS)} bps/side "
                   "to find where the edge dies. Slow — one backtest per level.")
        if st.button("Run cost sweep", key="cost_sweep"):
            s_name, s_tickers, s_start, s_end, _s_spread, s_borrow = \
                st.session_state["_sweep_args"]
            s_end = pd.Timestamp(s_end).normalize() + pd.Timedelta(hours=23, minutes=59)
            s_bars = src.load(s_tickers, s_start, s_end)
            s_bench_t = [t for t in ("SPY", "TLT") if t in tickers_all]
            s_bench = src.load(s_bench_t, s_start, s_end) if s_bench_t else None
            sweep = sensitivity.cost_sweep(
                get_strategy(s_name), s_bars,
                BacktestConfig(borrow_bps_annual=s_borrow),
                benchmark_bars=s_bench)
            st.dataframe(labels.humanize(sweep), use_container_width=True)
```

Update the imports at the top of `dashboard/views/run.py` to:

```python
import os
import pandas as pd
import streamlit as st
from dashboard import charts, labels, sensitivity
from src.engine_v2.backtest import metrics_simple as mx
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy
from src.engine_v2.data.source import default_source, intraday_source, INTRADAY_PATH
from src.engine_v2.backtest.simple import run_simple
from src.engine_v2.backtest.orchestrator import BacktestConfig
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **at least 180 + 15 = 195 tests**, zero failures. If any pre-existing test broke,
fix the cause; do not edit the test to match new behavior without saying so.

- [ ] **Step 5: Look at it**

Run: `.venv/bin/python -m streamlit run dashboard/app.py`
Confirm by eye: page is dark; five Plotly charts render; four hero tiles; the heatmap shows red and
green months; the cost-sweep button exists and only runs when pressed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/views/run.py tests/test_workbench_dashboard.py
git commit -m "feat: Run page on themed Plotly — underwater, heatmap, vs-buy-hold tile, cost sweep"
```

---

## Stop here

**Stage 1 ends at Task 6.** The owner reviews the running dashboard before Stage 2 (Wheel page)
and Stage 3 (Leaderboard) are planned. His words: *"I just need to see it first before I tell you
what else to change or not."* Stages 2 and 3 inherit this shell, so his feedback changes them —
writing their tasks now would be waste.

Stages 2 and 3 remain specified in
`docs/superpowers/specs/2026-07-12-dashboard-visual-overhaul-design.md`.
