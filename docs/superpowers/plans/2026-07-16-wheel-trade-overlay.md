# Wheel Trade Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Wheel page's score-first layout with a price-first one — daily candles for the traded underlying, every blotter position drawn as a horizontal segment at its strike — and strip the page from ten sections to five.

**Architecture:** Three new units with one job each. `dashboard/trades.py` is pure pandas (no streamlit import) and turns a `position_log` blotter into plottable rows — this is the renderer-agnostic piece. `dashboard/bars.py` is a cached adapter over the engine's existing `ParquetSource`. `charts.candles_with_trades` renders. `views/wheel.py` loses six sections and gains a five-line chart block, shrinking on net.

**Tech Stack:** Python, Streamlit, Plotly, pandas, pytest, `streamlit.testing.v1.AppTest`.

**Spec:** `docs/superpowers/specs/2026-07-16-wheel-trade-overlay-design.md`

## Global Constraints

- Branch: `wheel-trade-overlay` (already cut from `main`). Do not commit anything under `scripts/` — three pull scripts are intentionally left dirty in the working tree. Always `git add` by explicit path, never `git add -A` or `git add .`.
- `dashboard/trades.py` must NOT import streamlit. It is pure pandas so a renderer swap leaves it untouched.
- `dashboard/theme.py` docstring rule, verbatim: *"One Plotly template + the palette. Views must never hardcode a colour."* No hex literals outside `theme.py`.
- `dashboard/charts.py` docstring rule, verbatim: *"Themed Plotly figure builders. Views compose these; views never style charts."* Every builder ends `return theme.apply(fig)`.
- The dashboard is read-only: never write to `results/`, `data/`, or the vault.
- Full suite must be green after every task: `.venv/bin/python -m pytest -q`
- Run all commands from the repo root: `/Users/georgiemanavazian/Documents/Trading/code/etf-bot`

## Reference: the blotter contract

`position_log(res, cfg)` (`src/engine_v2/options/report.py:310`) returns these columns, in this order:

```
opened, closed, instrument, strike, expiry, qty, credit, outcome,
cost_to_close, realized_pnl, pct_of_credit, days_held, campaign_id
```

Facts the implementation depends on — all verified against report.py, do not re-derive:

- **Every row has a `strike`, including `SHARES` rows** (`:347`, `:360`, `:382` write `strike=astrike`, the assignment price). There is no NaN-strike case.
- **`closed` is `NaT` for exactly two outcomes:** `"Settled at mark"` (option live at window end, `:370`) and `"Open"` (shares still held, `:382`). Detect open-ended segments with `closed` being null — **never** by matching the outcome string, or `"Settled at mark"` segments get silently truncated.
- `outcome` is one of: `Took profit`, `Rolled`, `Stopped`, `Expired worthless`, `Assigned`, `Called away`, `Liquidated`, `Settled at mark`, `Open`.
- `realized_pnl` is null only on the `"Open"` shares row.

---

### Task 1: Shared outcome-colour rule

Extract the colour-group decision out of `_style_blotter` so the blotter and the chart cannot disagree about the same trade. Both will call `outcome_group` and index `theme.GROUP_COLORS` with its result, so they agree by construction.

**Files:**
- Create: `dashboard/trades.py`
- Create: `tests/test_trades_overlay.py`
- Modify: `dashboard/theme.py` (append `GROUP_COLORS`)
- Modify: `dashboard/views/wheel.py:282-320` (`_style_blotter` consumes the rule)

**Interfaces:**
- Consumes: `theme.NEGATIVE`, `theme.WARNING`, `theme.POSITIVE`, `theme.MUTED` (existing hex constants)
- Produces:
  - `trades.outcome_group(realized_pnl, outcome) -> str` — returns exactly one of `"Lost money"`, `"Assigned"`, `"Kept premium"`, `"Open"`
  - `theme.GROUP_COLORS: dict[str, str]` — those four keys → hex colours

- [ ] **Step 1: Write the failing test**

Create `tests/test_trades_overlay.py`:

```python
import pandas as pd
import pytest

from dashboard import theme, trades


@pytest.mark.parametrize("pnl, outcome, expected", [
    (-5.0, "Took profit", "Lost money"),
    (-5.0, "Assigned", "Lost money"),        # pnl sign wins — matches _style_blotter's branch order
    (100.0, "Assigned", "Assigned"),
    (100.0, "Took profit", "Kept premium"),
    (0.0, "Expired worthless", "Kept premium"),
    (float("nan"), "Open", "Open"),
])
def test_outcome_group(pnl, outcome, expected):
    assert trades.outcome_group(pnl, outcome) == expected


@pytest.mark.parametrize("pnl", [-5.0, 0.0, 100.0, float("nan")])
@pytest.mark.parametrize("outcome", [
    "Took profit", "Rolled", "Stopped", "Expired worthless", "Assigned",
    "Called away", "Liquidated", "Settled at mark", "Open",
])
def test_every_group_has_a_colour(pnl, outcome):
    """Guards the real failure mode: a group with no colour is a KeyError at render."""
    assert theme.GROUP_COLORS[trades.outcome_group(pnl, outcome)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: FAIL — `ImportError: cannot import name 'trades' from 'dashboard'`

- [ ] **Step 3: Write minimal implementation**

Create `dashboard/trades.py`:

```python
"""Blotter -> plottable overlay. Pure pandas: no streamlit, no plotly. The
renderer is swappable; this module is what survives a swap."""
import pandas as pd


def outcome_group(realized_pnl, outcome) -> str:
    """Colour group for a position log row.

    Branch order is lifted verbatim from _style_blotter so the chart and the
    blotter can never disagree about the same trade: lost money beats assigned,
    assigned beats kept-premium, an unrealized row is still open.
    """
    if pd.notna(realized_pnl) and realized_pnl < 0:
        return "Lost money"
    if outcome == "Assigned":
        return "Assigned"
    if pd.notna(realized_pnl):
        return "Kept premium"
    return "Open"
```

Append to `dashboard/theme.py`, after the `WARNING` constant:

```python
# Position outcome groups. trades.outcome_group() decides the group; this maps
# it to paint. Keys must stay in sync with that function's return values.
GROUP_COLORS = {
    "Lost money": NEGATIVE,
    "Assigned": WARNING,
    "Kept premium": POSITIVE,
    "Open": MUTED,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: PASS (42 passed)

- [ ] **Step 5: Point `_style_blotter` at the shared rule**

In `dashboard/views/wheel.py`, add `trades` to the dashboard import at line 7:

```python
from dashboard import charts, labels, theme, trades
```

Replace the body of `_style_blotter` (currently `wheel.py:288-301`) — from `lose = _hex_tint(...)` through the end of the `for` loop — with:

```python
    # Opacities tuned for the dark theme: fainter than this and the tint is
    # imperceptible against #0e1117 (first attempt used 0.06 — invisible).
    alpha = {"Lost money": 0.22, "Assigned": 0.20, "Kept premium": 0.14}
    tints = []
    for _, r in raw.iterrows():
        grp = trades.outcome_group(r["realized_pnl"], r["outcome"])
        tints.append(f"background-color: {_hex_tint(theme.GROUP_COLORS[grp], alpha[grp])}"
                     if grp in alpha else "")
```

Note: `"Open"` is deliberately absent from `alpha` and yields `""` — no tint — preserving the current behaviour exactly. Do not give it an alpha of 0.0; that renders `rgba(...,0.0)` rather than no rule at all.

Leave `_hex_tint` and `_pnl_colour` untouched.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, no regressions. `test_wheel_page_runs_and_renders_metrics` still green — the blotter renders identically, it just sources its groups from one place now.

- [ ] **Step 7: Commit**

```bash
git add dashboard/trades.py dashboard/theme.py dashboard/views/wheel.py tests/test_trades_overlay.py
git commit -m "refactor(dashboard): extract outcome_group; blotter and chart share one colour rule

The chart added in a later task needs the same red/amber/green the blotter
already shows. Two copies of the rule would eventually disagree about the same
trade, silently. Both now call trades.outcome_group() and index
theme.GROUP_COLORS with the result, so they agree by construction.

No visual change: _style_blotter's branch order and alphas are preserved, and
\"Open\" still yields no tint rather than a zero-alpha rule.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `overlay_frame` — the join

Turn a blotter into one drawable row per position. This is the actual work of the feature; everything else is plumbing or paint.

**Files:**
- Modify: `dashboard/trades.py`
- Modify: `tests/test_trades_overlay.py`

**Interfaces:**
- Consumes: `trades.outcome_group` (Task 1)
- Produces: `trades.overlay_frame(blotter: pd.DataFrame, window_end: pd.Timestamp) -> pd.DataFrame` with columns `x0, x1, y, group, open_ended, hover`. One row per input row, same order. Empty input → empty frame with those columns.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trades_overlay.py`:

```python
BLOTTER_COLS = ["opened", "closed", "instrument", "strike", "expiry", "qty",
                "credit", "outcome", "cost_to_close", "realized_pnl",
                "pct_of_credit", "days_held", "campaign_id"]

WINDOW_END = pd.Timestamp("2024-03-28")


def _row(**kw):
    base = dict(opened=pd.NaT, closed=pd.NaT, instrument="PUT", strike=450.0,
                expiry=pd.NaT, qty=1, credit=100.0, outcome="Took profit",
                cost_to_close=0.0, realized_pnl=100.0, pct_of_credit=1.0,
                days_held=14, campaign_id=0)
    base.update(kw)
    return base


def _blotter(rows):
    return pd.DataFrame(rows, columns=BLOTTER_COLS)


def test_option_row_draws_at_its_strike_between_open_and_close():
    b = _blotter([_row(opened=pd.Timestamp("2024-01-05"),
                       closed=pd.Timestamp("2024-01-19"), strike=450.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert len(out) == 1
    assert out.loc[0, "y"] == 450.0
    assert out.loc[0, "x0"] == pd.Timestamp("2024-01-05")
    assert out.loc[0, "x1"] == pd.Timestamp("2024-01-19")
    assert not out.loc[0, "open_ended"]


def test_shares_row_draws_at_its_assignment_basis_and_is_kept():
    """SHARES rows carry strike=assignment price (report.py:347). No NaN case."""
    b = _blotter([_row(opened=pd.Timestamp("2024-02-01"),
                       closed=pd.Timestamp("2024-02-20"), instrument="SHARES",
                       strike=440.0, outcome="Called away", realized_pnl=-50.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert len(out) == 1
    assert out.loc[0, "y"] == 440.0
    assert out.loc[0, "group"] == "Lost money"


def test_open_shares_run_to_window_end():
    b = _blotter([_row(opened=pd.Timestamp("2024-03-01"), closed=pd.NaT,
                       instrument="SHARES", strike=430.0, outcome="Open",
                       realized_pnl=float("nan"))])
    out = trades.overlay_frame(b, WINDOW_END)
    assert out.loc[0, "x1"] == WINDOW_END
    assert out.loc[0, "open_ended"]
    assert out.loc[0, "group"] == "Open"


def test_settled_at_mark_is_open_ended_too():
    """Regression guard: two outcomes leave closed=NaT. Matching the outcome
    string instead of closed.isna() truncates this one silently."""
    b = _blotter([_row(opened=pd.Timestamp("2024-03-05"), closed=pd.NaT,
                       strike=435.0, outcome="Settled at mark",
                       realized_pnl=80.0)])
    out = trades.overlay_frame(b, WINDOW_END)
    assert out.loc[0, "x1"] == WINDOW_END
    assert out.loc[0, "open_ended"]
    assert out.loc[0, "group"] == "Kept premium"


def test_empty_blotter_yields_empty_frame_with_columns():
    out = trades.overlay_frame(_blotter([]), WINDOW_END)
    assert out.empty
    assert list(out.columns) == ["x0", "x1", "y", "group", "open_ended", "hover"]


def test_hover_names_the_instrument_and_outcome():
    b = _blotter([_row(opened=pd.Timestamp("2024-01-05"),
                       closed=pd.Timestamp("2024-01-19"), outcome="Assigned",
                       realized_pnl=100.0, campaign_id=3)])
    hover = trades.overlay_frame(b, WINDOW_END).loc[0, "hover"]
    assert "PUT" in hover and "Assigned" in hover and "450" in hover
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: FAIL — `AttributeError: module 'dashboard.trades' has no attribute 'overlay_frame'`

- [ ] **Step 3: Write minimal implementation**

Append to `dashboard/trades.py`:

```python
OVERLAY_COLS = ["x0", "x1", "y", "group", "open_ended", "hover"]


def _hover(r) -> str:
    bits = [f"{r['instrument']} @ {r['strike']:,.2f}", str(r["outcome"])]
    if pd.notna(r["credit"]):
        bits.append(f"credit ${r['credit']:,.2f}")
    if pd.notna(r["realized_pnl"]):
        bits.append(f"P&L ${r['realized_pnl']:,.2f}")
    if pd.notna(r["days_held"]):
        bits.append(f"{int(r['days_held'])}d held")
    bits.append(f"campaign {int(r['campaign_id'])}")
    return "<br>".join(bits)


def overlay_frame(blotter: pd.DataFrame, window_end) -> pd.DataFrame:
    """One drawable segment per position: a horizontal line at the strike,
    running from open to close.

    Every row has a strike, SHARES included — it is the assignment price, i.e.
    the cost basis (report.py:347). So y is uniform across instruments.

    A position is open-ended when it has no closing date. Two outcomes do that,
    "Open" and "Settled at mark", which is why this tests closed rather than
    matching outcome text.
    """
    window_end = pd.Timestamp(window_end)
    rows = []
    for _, r in blotter.iterrows():
        open_ended = pd.isna(r["closed"])
        rows.append({
            "x0": r["opened"],
            "x1": window_end if open_ended else r["closed"],
            "y": r["strike"],
            "group": outcome_group(r["realized_pnl"], r["outcome"]),
            "open_ended": open_ended,
            "hover": _hover(r),
        })
    return pd.DataFrame(rows, columns=OVERLAY_COLS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/trades.py tests/test_trades_overlay.py
git commit -m "feat(dashboard): overlay_frame — blotter positions as drawable segments

One horizontal segment per position at its strike, open date to close date.
Pure pandas, no renderer dependency: this is the piece that survives if the
chart library is ever swapped.

Open-ended segments are detected via closed being NaT, not by matching the
outcome string — 'Open' and 'Settled at mark' both leave a position open, and
string-matching would truncate the latter with no visible symptom.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `bars.load_bars` — cached OHLC adapter

**Files:**
- Create: `dashboard/bars.py`
- Create: `tests/test_dashboard_bars.py`

**Interfaces:**
- Consumes: `src.engine_v2.data.source.ParquetSource`, `source.UNIVERSE_PATH`
- Produces:
  - `bars.fetch_bars(ticker: str, start, end) -> pd.DataFrame` — pure, single-ticker OHLCV indexed by date; **empty frame** when the ticker is absent
  - `bars.load_bars` — the `st.cache_data`-wrapped `fetch_bars`; this is what views call

**Why two names:** `engine_v2.data.loader.load_bars:20` raises `KeyError` for an unknown ticker rather than returning empty. `_sources()` (`wheel.py:28`) offers any ticker with an options chain on disk, but the bars parquet only holds 20 ETFs — XOP has a chain and no bars. Uncaught, that ticker throws the whole page instead of degrading. `fetch_bars` is the pure, tested translation of that; `load_bars` is the cached wrapper views use, since caching is Streamlit's concern and not something to unit-test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_bars.py`:

```python
import os
import pandas as pd
import pytest

from dashboard import bars
from src.engine_v2.data import source

pytestmark = pytest.mark.skipif(
    not os.path.exists(source.UNIVERSE_PATH),
    reason="ETF universe parquet not on disk")

START = pd.Timestamp("2024-01-02")
END = pd.Timestamp("2024-03-28")


def test_fetch_bars_returns_ohlc_for_a_known_ticker():
    df = bars.fetch_bars("SPY", START, END)
    assert not df.empty
    assert {"Open", "High", "Low", "Close"} <= set(df.columns)
    assert df.index.min() >= START and df.index.max() <= END


def test_fetch_bars_unknown_ticker_returns_empty_not_raises():
    """XOP has an options chain but no bars. The page must degrade, not throw."""
    df = bars.fetch_bars("XOP", START, END)
    assert df.empty


def test_fetch_bars_columns_are_flat_not_multiindex():
    df = bars.fetch_bars("SPY", START, END)
    assert not isinstance(df.columns, pd.MultiIndex)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dashboard_bars.py -q`
Expected: FAIL — `ImportError: cannot import name 'bars' from 'dashboard'`

- [ ] **Step 3: Write minimal implementation**

Create `dashboard/bars.py`:

```python
"""Daily OHLC for the dashboard, read through the engine's own bar source.

Not a second parquet reader: engine_v2.data.source owns that. This adds the two
things a view needs and the engine has no reason to — a cache, and a soft answer
for tickers with no bars."""
import pandas as pd
import streamlit as st

from src.engine_v2.data import source


def fetch_bars(ticker: str, start, end) -> pd.DataFrame:
    """Single-ticker OHLCV indexed by date. Empty frame if the ticker has no bars.

    The bars parquet holds 20 ETFs; the wheel page offers any ticker with an
    options chain on disk. XOP is the live example of the gap. The engine's
    loader raises KeyError there — a view wants to draw the rest of the page.
    """
    try:
        df = source.default_source().load([ticker], start, end)
    except KeyError:
        return pd.DataFrame()
    return df[ticker]


load_bars = st.cache_data(show_spinner=False)(fetch_bars)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dashboard_bars.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/bars.py tests/test_dashboard_bars.py
git commit -m "feat(dashboard): cached OHLC adapter over the engine bar source

Views need daily candles. engine_v2.data.source already reads the 20-ETF
parquet, so this wraps it rather than duplicating the read, and adds the two
view-only concerns: an st.cache_data layer, and returning an empty frame for
tickers with no bars.

That last one is load-bearing, not defensive padding: the wheel page offers any
ticker with an options chain, the parquet holds 20 ETFs, and XOP is already on
the wrong side of that gap. loader.load_bars raises KeyError there, which would
take the whole page down instead of just the chart.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `candles_with_trades` — the chart

**Files:**
- Modify: `dashboard/charts.py`
- Modify: `tests/test_trades_overlay.py`

**Interfaces:**
- Consumes: `trades.overlay_frame` output (Task 2), `theme.GROUP_COLORS` (Task 1), `theme.apply`
- Produces: `charts.candles_with_trades(bars: pd.DataFrame, overlay: pd.DataFrame) -> go.Figure`

**Design note — one trace per group, not per segment.** All of a group's segments go into a single `go.Scatter` with `None` separators between them. That makes the Plotly legend a working filter for free: click "Assigned" to isolate assignments. One trace per segment would produce an unusable legend of hundreds of entries.

**But group alone is not enough to key a trace.** `dash` is a per-trace property, and a group can hold both closed and open-ended segments at once — a `"Kept premium"` group can contain closed winners *and* a `"Settled at mark"` position. Keying on group alone would force one dash style on the whole group based on whichever row happened to come first. Key traces on `(group, open_ended)` instead: same colour either way, name suffixed `" (open)"`, at most two extra legend entries (a run can leave at most one option and one shares position open).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trades_overlay.py`:

```python
from dashboard import charts


def _bars(n=60):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series(range(440, 440 + n), index=idx, dtype=float)
    return pd.DataFrame({"Open": close - 1, "High": close + 2,
                         "Low": close - 2, "Close": close,
                         "Volume": 1_000_000}, index=idx)


def test_candles_with_trades_has_a_candlestick_trace():
    ov = trades.overlay_frame(_blotter([_row(
        opened=pd.Timestamp("2024-01-05"), closed=pd.Timestamp("2024-01-19"))]),
        WINDOW_END)
    fig = charts.candles_with_trades(_bars(), ov)
    assert "candlestick" in [t.type for t in fig.data]


def test_candles_with_trades_draws_one_trace_per_present_group():
    ov = trades.overlay_frame(_blotter([
        _row(opened=pd.Timestamp("2024-01-05"), closed=pd.Timestamp("2024-01-19"),
             realized_pnl=100.0),                                   # Kept premium
        _row(opened=pd.Timestamp("2024-01-22"), closed=pd.Timestamp("2024-02-02"),
             realized_pnl=-30.0),                                   # Lost money
        _row(opened=pd.Timestamp("2024-02-05"), closed=pd.Timestamp("2024-02-16"),
             outcome="Assigned", realized_pnl=80.0),                # Assigned
    ]), WINDOW_END)
    fig = charts.candles_with_trades(_bars(), ov)
    names = {t.name for t in fig.data if t.type == "scatter"}
    assert names == {"Kept premium", "Lost money", "Assigned"}


def test_candles_with_trades_groups_many_segments_into_one_trace():
    """Legend-as-filter only works if a group is one trace, not one per segment."""
    ov = trades.overlay_frame(_blotter([
        _row(opened=pd.Timestamp("2024-01-05"), closed=pd.Timestamp("2024-01-19")),
        _row(opened=pd.Timestamp("2024-01-22"), closed=pd.Timestamp("2024-02-02")),
    ]), WINDOW_END)
    fig = charts.candles_with_trades(_bars(), ov)
    scatters = [t for t in fig.data if t.type == "scatter"]
    assert len(scatters) == 1
    assert None in list(scatters[0].x)      # segments separated, not joined


def test_open_segment_splits_from_its_closed_groupmates():
    """dash is per-trace, and one group can hold both closed and open segments.
    Keying traces on group alone would dash the whole group off row 0."""
    ov = trades.overlay_frame(_blotter([
        _row(opened=pd.Timestamp("2024-01-05"), closed=pd.Timestamp("2024-01-19"),
             realized_pnl=100.0),                                   # Kept premium, closed
        _row(opened=pd.Timestamp("2024-03-05"), closed=pd.NaT,
             outcome="Settled at mark", realized_pnl=80.0),         # Kept premium, open
    ]), WINDOW_END)
    fig = charts.candles_with_trades(_bars(), ov)
    by_name = {t.name: t for t in fig.data if t.type == "scatter"}
    assert set(by_name) == {"Kept premium", "Kept premium (open)"}
    assert by_name["Kept premium"].line.dash == "solid"
    assert by_name["Kept premium (open)"].line.dash == "dot"
    # same group, same paint — only the dash differs
    assert by_name["Kept premium"].line.color == by_name["Kept premium (open)"].line.color


def test_candles_with_trades_empty_overlay_renders_candles_only():
    fig = charts.candles_with_trades(_bars(), trades.overlay_frame(_blotter([]), WINDOW_END))
    assert [t.type for t in fig.data] == ["candlestick"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: FAIL — `AttributeError: module 'dashboard.charts' has no attribute 'candles_with_trades'`

- [ ] **Step 3: Write minimal implementation**

Append to `dashboard/charts.py`:

```python
def candles_with_trades(bars: pd.DataFrame, overlay: pd.DataFrame) -> go.Figure:
    """Daily candles with every position drawn as a horizontal segment at its
    strike, coloured by outcome group.

    Segments are batched into one trace per (group, open_ended) with None
    separators inside — that makes the legend a filter (click "Assigned" to
    isolate assignments) at no cost. One trace per segment would give a legend
    with hundreds of entries.

    open_ended is part of the key, not just the group: dash is a per-trace
    property, and a group can hold both closed and open segments at once.
    """
    fig = go.Figure(go.Candlestick(
        x=bars.index, open=bars["Open"], high=bars["High"],
        low=bars["Low"], close=bars["Close"], name="Price",
        increasing_line_color=theme.POSITIVE, decreasing_line_color=theme.NEGATIVE,
        showlegend=False,
    ))
    for (group, open_ended), rows in overlay.groupby(["group", "open_ended"], sort=False):
        xs, ys, texts = [], [], []
        for _, r in rows.iterrows():
            xs += [r["x0"], r["x1"], None]
            ys += [r["y"], r["y"], None]
            texts += [r["hover"], r["hover"], None]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, name=f"{group} (open)" if open_ended else group, mode="lines",
            line=dict(color=theme.GROUP_COLORS[group], width=2,
                      dash="dot" if open_ended else "solid"),
            text=texts, hovertemplate="%{text}<extra></extra>",
            connectgaps=False,
        ))
    fig.update_layout(height=460, xaxis_rangeslider_visible=False,
                      hovermode="closest", xaxis_title=None, yaxis_title=None)
    return theme.apply(fig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trades_overlay.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/charts.py tests/test_trades_overlay.py
git commit -m "feat(dashboard): candles_with_trades — price with positions drawn on it

Daily candles plus one horizontal segment per position at its strike, coloured
by trades.outcome_group so the chart and the blotter always agree.

Segments are grouped into one trace per outcome with None separators, which
makes the Plotly legend a filter for free — click 'Assigned' and only
assignments remain. One trace per segment would mean a legend with hundreds of
entries and no way to use it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Strip six sections from the Wheel page

The page still works and still runs at the end of this task; it is simply shorter. The chart lands in Task 6.

**Files:**
- Modify: `dashboard/views/wheel.py` (delete six sections; fold the days-flat caveat into the vs-SPY tooltip)
- Modify: `tests/test_wheel_dashboard.py:70-79` (delete the test for the removed table)

**Interfaces:**
- Consumes: nothing new
- Produces: nothing new. `st.session_state["_wheel_result"]` keeps its six-tuple shape — `log_run` still consumes `rep_plain` inside the Run block (`:147`), and changing the tuple would touch History for no gain.

- [ ] **Step 1: Delete the six sections**

**Work bottom-up — delete the last section first.** The line numbers below are all pre-deletion; removing a section shifts every number under it. Going bottom-up keeps them valid, and the anchor strings disambiguate either way.

In `dashboard/views/wheel.py`, delete each of these in full:

| Anchor | Lines | Why it goes |
|---|---|---|
| `rows = {}` … the `st.dataframe(cmp_df…)` call, incl. `_arm_row` | 155–175 | `log_run` already writes `plain_total_return`; the comparison lives on the History page |
| `if s["n_days_flat"]:` … `st.warning(…)` | 196–199 | Folded into the vs-SPY tooltip in Step 2 |
| `if getattr(rep, "defense", None):` … the `liquidate_fill_note` caption | 203–223 | Re-derivable from the blotter's `campaign_id` + `outcome` |
| `st.subheader("Equity vs buy & hold")` … its `st.plotly_chart` | 225–233 | Superseded by the candle chart — this is the intended swap |
| `st.subheader("Wheel stats")` … the `t4.metric("Commission"…)` line | 239–249 | Counts of blotter rows |
| `if len(s["realized_dte"]):` … its `st.plotly_chart` | 253–258 | A proof chart; determinism is already established |

Then remove the now-dead import inside the render body:

```python
from src.engine_v2.options.report import buy_hold_curve, spy_curve   # line 227 — delete
```

Keep `pnl` (`:153`) — the metric row uses it. Do **not** touch `charts.equity_curve` / `underwater` / `monthly_heatmap` in `dashboard/charts.py`: `views/run.py` still calls all three.

**Move `s = rep.stats` up.** It currently sits at `:195`, *below* the metric row it is about to feed. Delete it from there and re-declare it immediately above `a, b, c, d = st.columns(4)` (`:177`):

```python
        s = rep.stats
        a, b, c, d = st.columns(4)
```

Left where it is, Step 2's tooltip references `s` before assignment and the page dies with `NameError` on the first run. It reads as a no-op line move; it isn't.

- [ ] **Step 2: Fold the days-flat caveat into the vs-SPY tooltip**

Replace the `if rep.benchmark_spy is not None:` / `else:` block (`:182-189`) with:

```python
        # Flat days change what the benchmark comparison means, so the caveat
        # rides on the tile it qualifies rather than sitting in its own banner:
        # a bot flat 30% of a window is being measured against a benchmark that
        # was invested for 100% of it.
        flat_note = ""
        if s["n_days_flat"]:
            lo, hi = derived_band(cfg.target_dte)
            flat_note = (f" Flat {s['n_days_flat']} days ({s['pct_days_flat']:.0%} of "
                         f"the window) — no expiry inside the {lo}–{hi} DTE band. The "
                         f"benchmark held {rep.ticker} on those days; the bot held cash.")
        if rep.benchmark_spy is not None:
            gap = rep.metrics["total_return"] - rep.benchmark_spy["total_return"]
            d.metric("vs SPY buy & hold", f"{gap:+.2%}",
                     help=f"Buy-hold real SPY returned "
                          f"{rep.benchmark_spy['total_return']:+.2%} over this window."
                          + flat_note)
        else:
            d.metric("vs SPY buy & hold", "—",
                     help="No SPY chain on disk to benchmark against." + flat_note)
```

Note the band comes from `derived_band(cfg.target_dte)` — the *stashed run's* config — not the module-level `band_lo`/`band_hi` computed from the live widget at `:89`. Those reflect whatever the knob says right now, which drifts from the displayed run the moment you touch the knob without re-running. `derived_band` is already imported at `:9`.

- [ ] **Step 3: Delete the test for the removed table**

In `tests/test_wheel_dashboard.py`, delete `test_wheel_run_shows_basis_vs_plain_table` (`:70-79`) entirely.

It asserts the basis-vs-plain table renders. That table is gone by design, so the test is **deleted, not weakened**. Do not soften it into something that passes — a green suite that guards nothing is worse than a missing test, because it lies.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `test_wheel_page_runs_and_renders_metrics` must still be green — its `len(at.metric) >= 4` guards the P&L / Sharpe / MaxDD / vs-SPY row, which is kept. If that test fails, a metric tile was deleted by mistake; restore it.

- [ ] **Step 5: Eyeball the page**

Run: `.venv/bin/python -m streamlit run dashboard/app.py`

Switch to the Wheel page, pick `SPY (2024 sample fixture)`, click Run. Confirm: knobs, then the four metric tiles, then Year-by-year, then the blotter — and nothing else. Hover the "vs SPY buy & hold" tile and confirm the flat-days sentence appears when the run has flat days. Ctrl-C when done.

- [ ] **Step 6: Commit**

```bash
git add dashboard/views/wheel.py tests/test_wheel_dashboard.py
git commit -m "refactor(dashboard): strip the wheel page to knobs, verdict, year-by-year, blotter

Six sections out: basis-vs-plain table, days-flat warning, defense stats, equity
curve, wheel stats grid, realized DTE. The page was a wall, and most of it was
recoverable elsewhere — log_run already persists plain_total_return to History,
and defense stats are re-derivable from the blotter's campaign_id and outcome.
The equity curve is superseded by the candle chart landing next.

The days-flat caveat is kept but moved into the vs-SPY tile's tooltip: it
qualifies that number specifically (a bot flat 30% of a window is measured
against a benchmark invested 100% of it), so it belongs attached to it rather
than in a banner that can be scrolled past. It now reads the band from the
stashed cfg instead of the live widget, which drifted from the displayed run
whenever the knob moved without a re-run.

test_wheel_run_shows_basis_vs_plain_table is deleted, not weakened — the table
it guarded is intentionally gone.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wire the candle chart into the Wheel page

**Files:**
- Modify: `dashboard/views/wheel.py`
- Modify: `tests/test_wheel_dashboard.py`

**Interfaces:**
- Consumes: `bars.load_bars` (Task 3), `trades.overlay_frame` (Task 2), `charts.candles_with_trades` (Task 4)
- Produces: nothing downstream

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wheel_dashboard.py`:

```python
@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel fixture not built")
def test_wheel_run_renders_the_candle_chart():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    at.selectbox(key="wheel_data").set_value(FIXTURE_TICKER).run(timeout=60)
    at.button(key="run_wheel").click().run(timeout=90)
    assert not at.exception
    assert any("Trades on price" in s.value for s in at.subheader)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_wheel_dashboard.py::test_wheel_run_renders_the_candle_chart -q`
Expected: FAIL — no subheader named "Trades on price"

- [ ] **Step 3: Add the chart block**

In `dashboard/views/wheel.py`, extend the dashboard import at line 7:

```python
from dashboard import bars, charts, labels, theme, trades
```

Move the `position_log` call up so the chart and the blotter table share one call. Delete **both** of these lines from the blotter section (`:261-262`) — the import *and* the call. Deleting only the import leaves the call in place and the blotter gets computed twice per render:

```python
        from src.engine_v2.options.report import position_log      # delete
        blotter = position_log(res, cfg)                           # delete — re-added above
```

Insert the following **after** the metric row's `st.caption(…)` (`:190-192`) and **before** `st.subheader("Year-by-year return")`:

```python
        from src.engine_v2.options.report import position_log
        blotter = position_log(res, cfg)

        st.subheader("Trades on price")
        # Window = the run's actual trading days. Not the date_input values (a
        # user can pick a Saturday) and not ch["date"] (the chain runs past the
        # equity window on expiries) — open-ended segments would then float out
        # past the last candle.
        w0, w1 = res.equity.index[0], res.equity.index[-1]
        bars_df = bars.load_bars(rep.ticker, w0, w1)
        if bars_df.empty:
            st.info(f"No daily bars on disk for {rep.ticker} — candles unavailable. "
                    f"The blotter below still lists every position.")
        else:
            st.plotly_chart(
                charts.candles_with_trades(bars_df, trades.overlay_frame(blotter, w1)),
                width="stretch")
```

In the blotter section further down, `blotter` is now already defined — leave `display = blotter.copy()` and everything below it untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_wheel_dashboard.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Verify it actually works**

Run: `.venv/bin/python -m streamlit run dashboard/app.py`

On the Wheel page with `SPY (2024 sample fixture)`, click Run, and confirm by eye:

1. Candles render, and segments sit at plausible strike levels against them.
2. Clicking a legend entry hides that outcome group — the legend filters.
3. An assigned put's segment ends exactly where its SHARES segment begins, at the same level, so the campaign reads as one continuous line changing colour at the assignment.
4. Hovering a segment shows instrument, strike, outcome, P&L, campaign.
5. Any still-open position is dotted and stops at the last candle, not beyond it.

Ctrl-C when done.

- [ ] **Step 7: Commit**

```bash
git add dashboard/views/wheel.py tests/test_wheel_dashboard.py
git commit -m "feat(dashboard): candles with trades on the wheel page

The page showed the score and not the plays: you could read that a position was
assigned, but not watch price walk into the strike. Now every blotter position
is drawn at its strike over the candles, so assignments and stop-outs are
visible in their price context rather than excavated from a table.

position_log is called once and feeds both the chart and the blotter table. The
window comes from res.equity.index rather than the date pickers or the chain, so
open-ended segments stop at the last candle instead of floating past it.

Tickers with an options chain but no bars (XOP) degrade to an info line; the
blotter below it is unaffected.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Done when

- `.venv/bin/python -m pytest -q` is green
- The Wheel page is five sections: knobs, verdict row, candles, year-by-year, blotter
- The legend filters the chart by outcome group
- `git status` still shows exactly three modified files under `scripts/`, uncommitted and untouched
