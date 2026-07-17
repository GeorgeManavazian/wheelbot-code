# Chameleon Dashboard Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chameleon (regime router) page to the Streamlit dashboard that mirrors the Wheel page — ticker + config, run, posture-shaded candle chart with an option-leg blotter.

**Architecture:** New `dashboard/views/chameleon.py` structured like `views/wheel.py`, consuming `run_regime_router` / `run_regime_router_intraday` unchanged. A dedicated `router_report()` supplies metrics/blotter (the wheel report crashes on a `RouterResult`). A shared, test-pinned allow-list module `dashboard/guard.py` replaces the copy-pasted deny-list `UNSEEN` sets. A `charts.posture_bands` helper + an optional posture overlay show which strategy is active when.

**Tech Stack:** Python 3, Streamlit (multipage `st.navigation`), Plotly, pandas, pytest, `streamlit.testing.v1.AppTest`.

## Global Constraints

- Run tests with `.venv/bin/python -m pytest` — the `.venv/bin/pytest` shebang is broken in this checkout.
- Set `PYTHONPATH=.` for scripts/tests that import `src` (e.g. `PYTHONPATH=. .venv/bin/python -m pytest ...`).
- Seen (runnable) tickers: `SPY GDX SLV XOP`. Unseen (must stay hidden from every dashboard dropdown): `XBI EEM EWZ TLT ARKK QQQ`. The guard is an **allow-list** — show only SEEN + the explicit fixture — matching the CLI runner's choice.
- Frozen router config (the pre-registered form): `put_delta=0.20, call_delta=0.20, target_dte=7, take_profit_pct=0.50, call_min_strike="basis"`.
- `route_log` entries are `(date, trend, vol, posture)`; `posture ∈ {"TREND", "WHEEL", "CASH"}`.
- No engine changes. The page consumes the router engine as-is.
- Branch: `chameleon-dashboard`. Spec: `docs/superpowers/specs/2026-07-16-chameleon-dashboard-design.md`.

---

### Task 1: Shared allow-list guard — `dashboard/guard.py`

**Files:**
- Create: `dashboard/guard.py`
- Test: `tests/test_dashboard_guard.py`

**Interfaces:**
- Consumes: `src.engine_v2.options.data.available_tickers(data_dir)`, `chain_path(ticker, data_dir)`.
- Produces:
  - `SEEN: tuple[str, ...]` = `("SPY", "GDX", "SLV", "XOP")`.
  - `seen_sources(data_dir="data/options", include_fixture_name=None, fixture_path=None) -> dict[str, str]` — `{ticker: chain_path}` restricted to `SEEN`, plus `{include_fixture_name: fixture_path}` when both are given and the fixture exists.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_guard.py
import os
import pandas as pd
from dashboard import guard


def _touch_chain(data_dir, ticker):
    p = os.path.join(data_dir, f"{ticker.lower()}_greeks_eod_all.parquet")
    pd.DataFrame({"date": [pd.Timestamp("2020-01-02")], "underlying": [1.0]}).to_parquet(p)


def test_seen_sources_is_allow_list(tmp_path):
    d = str(tmp_path)
    for t in ["SPY", "GDX", "SLV", "XOP", "XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ", "ZZZ"]:
        _touch_chain(d, t)
    out = guard.seen_sources(data_dir=d)
    assert set(out) == {"SPY", "GDX", "SLV", "XOP"}          # only SEEN
    for unseen in ["XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"]:
        assert unseen not in out
    assert "ZZZ" not in out                                   # novel ticker excluded by default


def test_seen_sources_adds_fixture_when_present(tmp_path):
    d = str(tmp_path)
    _touch_chain(d, "SPY")
    fx = os.path.join(d, "fixture.parquet")
    pd.DataFrame({"date": [pd.Timestamp("2020-01-02")]}).to_parquet(fx)
    out = guard.seen_sources(data_dir=d, include_fixture_name="SPY (fixture)", fixture_path=fx)
    assert out["SPY (fixture)"] == fx


def test_seen_sources_skips_missing_fixture(tmp_path):
    d = str(tmp_path)
    _touch_chain(d, "SPY")
    out = guard.seen_sources(data_dir=d, include_fixture_name="X", fixture_path=os.path.join(d, "nope.parquet"))
    assert "X" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.guard'`.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/guard.py
"""Shared unseen-ticker guard for the dashboard. Allow-list, not deny-list: the
CLI runner review (2026-07-14) rejected the deny-list because it fails open on
typos and new tickers. Only the burned/seen set is ever offered until the
pre-registered basket run reports (amendment 2026-07-13d)."""
from __future__ import annotations

import os

from src.engine_v2.options.data import available_tickers, chain_path

SEEN = ("SPY", "GDX", "SLV", "XOP")


def seen_sources(data_dir: str = "data/options",
                 include_fixture_name: str | None = None,
                 fixture_path: str | None = None) -> dict[str, str]:
    """Ticker -> EOD chain path, restricted to SEEN. Optionally append a named
    fixture when its file exists. Anything outside SEEN (unseen basket, fresh
    pulls) is excluded by construction."""
    out = {t: chain_path(t, data_dir) for t in available_tickers(data_dir) if t in SEEN}
    if include_fixture_name and fixture_path and os.path.exists(fixture_path):
        out[include_fixture_name] = fixture_path
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_guard.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/guard.py tests/test_dashboard_guard.py
git commit -m "feat(dashboard): shared allow-list ticker guard, test-pinned"
```

---

### Task 2: Swap wheel.py and regime.py onto the shared guard

**Files:**
- Modify: `dashboard/views/wheel.py` (`_sources`, the `UNSEEN` constant, the ticker `help` text)
- Modify: `dashboard/views/regime.py` (the inline `UNSEEN` + list comprehension at `render`)
- Test: existing `tests/test_wheel_dashboard.py`, `tests/test_workbench_dashboard.py` stay green.

**Interfaces:**
- Consumes: `dashboard.guard.seen_sources`, `dashboard.guard.SEEN` (Task 1).
- Produces: no new public API; behavior is unchanged-or-stricter (allow-list can only narrow what the deny-list showed).

- [ ] **Step 1: Update wheel.py to use the guard**

Replace the `UNSEEN` constant and `_sources` in `dashboard/views/wheel.py`:

```python
# delete the UNSEEN constant (lines ~16-19) and replace _sources with:
from dashboard.guard import seen_sources

def _sources() -> dict:
    """Ticker -> EOD chain path. Seen (allow-listed) tickers + fixture fallback."""
    return seen_sources(include_fixture_name=FIXTURE_TICKER, fixture_path=FIXTURE)
```

Keep `FIXTURE_TICKER` / `FIXTURE` module constants. Leave the rest of `render()` unchanged.

- [ ] **Step 2: Update regime.py to use the guard**

In `dashboard/views/regime.py::render`, replace the inline `UNSEEN` set and comprehension (lines ~34-38) with:

```python
from dashboard.guard import SEEN
# ...
# Seen tickers only (allow-list, same guard as the Wheel/Chameleon pages).
tickers = [t for t in SEEN if t != "SPY"]
tick = st.selectbox("Ticker", ["SPY"] + tickers, key="regime_ticker")
```

(Add the `from dashboard.guard import SEEN` import to the top of `regime.py`.)

- [ ] **Step 3: Run the dashboard test suite to verify green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_wheel_dashboard.py tests/test_workbench_dashboard.py -v`
Expected: PASS (all existing dashboard tests).

- [ ] **Step 4: Commit**

```bash
git add dashboard/views/wheel.py dashboard/views/regime.py
git commit -m "refactor(dashboard): wheel + regime pages use the shared allow-list guard"
```

---

### Task 3: Router-aware report — `router_report()`

**Files:**
- Modify: `src/engine_v2/options/report.py` (add `RouterReport` dataclass + `router_report`; reuse existing `buy_hold_curve`, `position_log`)
- Test: `tests/engine_v2/options/test_router_report.py`

**Interfaces:**
- Consumes: `RouterResult` (fields `equity`, `trades`, `route_log`, `days_in_posture`, `whipsaw_pairs`, `warnings`, `intraday_tp_fills`, `eod_tp_fills`); `buy_hold_curve(chain, starting_capital)`; `position_log(result, cfg)`; `src.engine_v2.backtest.metrics_simple` (`infer_periods_per_year`, `cagr`, `sharpe`, `max_drawdown`, `yearly_returns`).
- Produces:
  - `RouterReport` dataclass: `metrics: dict`, `posture: dict`, `fills: dict`, `benchmark_underlying: dict`, `yearly_return: pd.Series`, `blotter: pd.DataFrame`, `ticker: str`.
  - `router_report(result, chain, cfg) -> RouterReport`.
  - `metrics` keys: `total_return`, `cagr`, `sharpe`, `max_drawdown`.
  - `posture` keys: `days` (the `days_in_posture` dict), `transitions` (int), `whipsaws` (int), `unknown` (int).
  - `fills` keys: `intraday_tp`, `eod_tp`.
  - `benchmark_underlying` keys: `total_return`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_router_report.py
import pandas as pd
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.report import router_report
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for
from src.engine_v2.backtest import metrics_simple as m

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _run(ticker="SPY"):
    ch = pd.read_parquet(chain_path(ticker))
    ch["date"] = pd.to_datetime(ch["date"])
    states = regime_series(closes_for(ticker))
    cfg = WheelConfig(ticker=ticker, **BASE)
    return run_regime_router(ch, cfg, states), ch, cfg


def test_router_report_metrics_match_equity():
    res, ch, cfg = _run()
    rep = router_report(res, ch, cfg)
    ppy = m.infer_periods_per_year(res.equity.index)
    assert rep.metrics["total_return"] == float(res.equity.iloc[-1] / res.equity.iloc[0] - 1)
    assert rep.metrics["max_drawdown"] == m.max_drawdown(res.equity)
    assert abs(rep.metrics["cagr"] - m.cagr(res.equity, ppy)) < 1e-12


def test_router_report_posture_and_fills():
    res, ch, cfg = _run()
    rep = router_report(res, ch, cfg)
    assert rep.posture["days"] == res.days_in_posture
    assert rep.posture["whipsaws"] == res.whipsaw_pairs
    # transitions = count of posture changes across the route_log
    expected_tr = sum(1 for a, b in zip(res.route_log, res.route_log[1:]) if a[3] != b[3])
    assert rep.posture["transitions"] == expected_tr
    assert rep.fills["intraday_tp"] == res.intraday_tp_fills
    assert rep.fills["eod_tp"] == res.eod_tp_fills


def test_router_report_does_not_touch_days_flat():
    # RouterResult has no days_flat; router_report must not read it (the reason
    # wheel_report cannot be reused here).
    res, ch, cfg = _run()
    assert not hasattr(res, "days_flat")
    rep = router_report(res, ch, cfg)   # must not raise
    assert rep.blotter is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_router_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'router_report'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/engine_v2/options/report.py` (near `WheelReport` / `wheel_report`):

```python
@dataclass
class RouterReport:
    metrics: dict
    posture: dict
    fills: dict
    benchmark_underlying: dict
    yearly_return: pd.Series
    blotter: pd.DataFrame
    ticker: str = "SPY"


def router_report(result, chain, cfg) -> RouterReport:
    """Report for a RouterResult. Decoupled from wheel_report, which reads
    result.days_flat (absent on RouterResult). Metrics use the same
    metrics_simple functions as scripts/run_regime_router.py, so the page and
    the CLI agree by construction."""
    eq = result.equity
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    metrics = {
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "cagr": m.cagr(eq, ppy),
        "sharpe": m.sharpe(rets, ppy),
        "max_drawdown": m.max_drawdown(eq),
    }
    rlog = result.route_log or []
    transitions = sum(1 for a, b in zip(rlog, rlog[1:]) if a[3] != b[3])
    unknown = sum(1 for w in (result.warnings or []) if w[1] == "route_state_unknown")
    posture = {
        "days": result.days_in_posture,
        "transitions": transitions,
        "whipsaws": result.whipsaw_pairs,
        "unknown": unknown,
    }
    fills = {"intraday_tp": result.intraday_tp_fills, "eod_tp": result.eod_tp_fills}
    bh = buy_hold_curve(chain, cfg.starting_capital)
    benchmark_underlying = {"total_return": float(bh.iloc[-1] / bh.iloc[0] - 1)}
    return RouterReport(
        metrics=metrics, posture=posture, fills=fills,
        benchmark_underlying=benchmark_underlying,
        yearly_return=m.yearly_returns(eq),
        blotter=position_log(result, cfg),
        ticker=cfg.ticker,
    )
```

Confirm `report.py` already imports `metrics_simple as m` and `dataclass`; if not, add `from dataclasses import dataclass` and `from src.engine_v2.backtest import metrics_simple as m` at the top.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_router_report.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/test_router_report.py
git commit -m "feat(report): router_report — metrics/posture/blotter for a RouterResult"
```

---

### Task 4: Posture bands data helper — `charts.posture_bands`

**Files:**
- Modify: `dashboard/charts.py` (add `posture_bands`)
- Test: `tests/test_dashboard_posture.py`

**Interfaces:**
- Consumes: a `route_log` list of `(date, trend, vol, posture)` tuples.
- Produces: `posture_bands(route_log) -> list[tuple]` — merged spans `(start_date, end_date, posture)`, consecutive same-posture days collapsed into one span; `end_date` is the last day of the span (inclusive). Empty list for empty input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_posture.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_posture.py -v`
Expected: FAIL with `ImportError: cannot import name 'posture_bands'`.

- [ ] **Step 3: Write minimal implementation**

Add to `dashboard/charts.py`:

```python
def posture_bands(route_log) -> list:
    """Collapse a per-day route_log [(date, trend, vol, posture), ...] into
    contiguous spans (start_date, end_date, posture). end_date is inclusive."""
    if not route_log:
        return []
    spans = []
    s_date, cur = route_log[0][0], route_log[0][3]
    prev = s_date
    for d, _t, _v, posture in route_log[1:]:
        if posture != cur:
            spans.append((s_date, prev, cur))
            s_date, cur = d, posture
        prev = d
    spans.append((s_date, prev, cur))
    return spans
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_posture.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/charts.py tests/test_dashboard_posture.py
git commit -m "feat(charts): posture_bands — collapse route_log into contiguous spans"
```

---

### Task 5: Posture overlay on the candle chart

**Files:**
- Modify: `dashboard/charts.py` (`candles_with_trades` — add optional `posture=None` param)
- Modify: `dashboard/theme.py` (add `POSTURE_COLORS`)
- Test: `tests/test_dashboard_posture.py` (extend)

**Interfaces:**
- Consumes: `posture_bands` output (Task 4); `theme.POSTURE_COLORS`.
- Produces: `candles_with_trades(bars, overlay, posture=None)` — when `posture` is a list of spans, draws each as a translucent background `vrect`; when `None`, output is unchanged from today (wheel page unaffected).
  - `theme.POSTURE_COLORS: dict` with keys `"TREND"`, `"WHEEL"`, `"CASH"`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_dashboard_posture.py
import pandas as pd
from dashboard.charts import candles_with_trades


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_posture.py -v`
Expected: FAIL — `candles_with_trades() got an unexpected keyword argument 'posture'`.

- [ ] **Step 3: Add theme colours**

Add to `dashboard/theme.py` after `GROUP_COLORS`:

```python
# Posture shading for the Chameleon chart (translucent bands behind candles).
POSTURE_COLORS = {
    "TREND": ACCENT,     # holding shares in an uptrend
    "WHEEL": WARNING,    # running the wheel
    "CASH": MUTED,       # sidelined
}
```

- [ ] **Step 4: Add the optional posture param**

In `dashboard/charts.py::candles_with_trades`, add `posture=None` to the signature and, immediately before `fig.update_layout(...)`, insert:

```python
    if posture:
        for start, end, name in posture:
            fig.add_vrect(
                x0=start, x1=end, layer="below", line_width=0,
                fillcolor=theme.POSTURE_COLORS.get(name, theme.MUTED),
                opacity=0.12,
                annotation_text=name, annotation_position="top left",
                annotation_font_size=10,
            )
```

- [ ] **Step 5: Run tests to verify pass (new + wheel regression)**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_dashboard_posture.py tests/test_dashboard_bars.py -v`
Expected: PASS — posture tests pass and the existing bars/wheel-chart tests stay green (the `None` default keeps the wheel chart byte-identical).

- [ ] **Step 6: Commit**

```bash
git add dashboard/charts.py dashboard/theme.py tests/test_dashboard_posture.py
git commit -m "feat(charts): optional posture bands behind the candle chart"
```

---

### Task 6: Chameleon page + nav registration

**Files:**
- Create: `dashboard/views/chameleon.py`
- Modify: `dashboard/app.py` (register the page in nav)
- Test: `tests/test_chameleon_dashboard.py`

**Interfaces:**
- Consumes: `dashboard.guard.seen_sources`, `guard.SEEN`; `router_report` (Task 3); `charts.posture_bands`, `charts.candles_with_trades` (Tasks 4-5); `bars.load_bars`, `trades.overlay_frame`; `run_regime_router` / `run_regime_router_intraday`; `regime_series`, `closes_for`; `WheelConfig`; `intraday_marks`.
- Produces: `render()` — the Streamlit page entry; registered as `st.Page(chameleon.render, title="Chameleon", url_path="chameleon")`.

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/test_chameleon_dashboard.py
import os
import pytest
from streamlit.testing.v1 import AppTest

FIX = "fixtures/spy_wheel_cycle.parquet"
FIXTURE_TICKER = "SPY (2024 sample fixture)"


@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel fixture not built")
def test_chameleon_page_runs_and_renders():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/chameleon.py").run(timeout=60)
    assert not at.exception
    at.selectbox(key="cham_data").set_value(FIXTURE_TICKER).run(timeout=60)
    at.button(key="run_cham").click().run(timeout=120)
    assert not at.exception                     # must NOT raise (the days_flat crash)
    assert len(at.metric) >= 3                  # P&L / Sharpe / maxDD tiles


def test_chameleon_page_hides_unseen_tickers():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/chameleon.py").run(timeout=60)
    assert not at.exception
    options = at.selectbox(key="cham_data").options
    for unseen in ["XBI", "EEM", "EWZ", "TLT", "ARKK", "QQQ"]:
        assert unseen not in options
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_chameleon_dashboard.py -v`
Expected: FAIL — `switch_page("views/chameleon.py")` errors (page does not exist).

- [ ] **Step 3: Write the page**

```python
# dashboard/views/chameleon.py
"""Chameleon (regime router) page: pick ticker + config, run, see the router's
postures shaded on the price chart with an option-leg blotter. Exploration only
— the referee CLI stays the citation gate. Mirrors views/wheel.py."""
import os
import pandas as pd
import streamlit as st
from dashboard import bars, charts, labels, theme, trades
from dashboard.guard import seen_sources
from src.engine_v2.options.data import intraday_path
from src.engine_v2.options.select import derived_band
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.intraday import intraday_marks
from src.engine_v2.options.report import router_report
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

FIXTURE_TICKER = "SPY (2024 sample fixture)"
FIXTURE = "fixtures/spy_wheel_cycle.parquet"
XOP_CLEAN_START = pd.Timestamp("2020-07-01")
FROZEN = dict(put_delta=0.20, call_delta=0.20, target_dte=7, take_profit_pct=0.50)


def render():
    st.title("Chameleon — regime router")
    st.caption("Routes per-ticker regime between hold-shares (TREND), the wheel "
               "(WHEEL) and cash (CASH). Exploration only — cite numbers off the "
               "referee CLI, not this page.")
    sources = seen_sources(include_fixture_name=FIXTURE_TICKER, fixture_path=FIXTURE)
    if not sources:
        st.warning("No options data under data/options/. Pull a seen-ticker chain first.")
        st.stop()

    ticker_name = st.selectbox(
        "Ticker", list(sources), key="cham_data",
        help="Unseen basket tickers stay hidden until the pre-registered basket "
             "run reports (amendment 2026-07-13d).")
    path = sources[ticker_name]
    ticker = "SPY" if ticker_name == FIXTURE_TICKER else ticker_name
    if not os.path.exists(path):
        st.warning(f"Data not found: {path}."); st.stop()
    ch = pd.read_parquet(path)
    ch["date"] = pd.to_datetime(ch["date"])

    min_d, max_d = ch["date"].min().date(), ch["date"].max().date()
    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="cham_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="cham_end")
    if ticker == "XOP" and pd.Timestamp(start) < XOP_CLEAN_START:
        st.warning("XOP's chain is split-broken before 2020-07-01: windows spanning "
                   "it book phantom gains — provenance-only.")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.20, 0.05, key="c_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.20, 0.05, key="c_cd")
    target_dte = c2.number_input("Target DTE", 5, 60, 7, key="c_tdte")
    band_lo, band_hi = derived_band(int(target_dte))
    c2.caption(f"trades expiries {band_lo}–{band_hi} days out")
    tp_slider = c3.slider("Take-profit (% of credit)", 1, 100, 50, key="c_tp")
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="c_cap")

    # Off-config banner (non-blocking): only the frozen form is the citable router.
    off = (put_delta != FROZEN["put_delta"] or call_delta != FROZEN["call_delta"]
           or int(target_dte) != FROZEN["target_dte"] or tp_slider != int(FROZEN["take_profit_pct"] * 100))
    if off:
        st.caption("⚠ Off-config — not the pre-registered router; posture routing "
                   "shifts with DTE (see the v2 DTE experiment).")
    else:
        st.caption("✓ Pre-registered form (frozen config).")

    intra = None if ticker_name == FIXTURE_TICKER else intraday_path(ticker)
    if ticker_name == FIXTURE_TICKER:
        INTRA_FIXTURE = "fixtures/spy_wheel_intraday_sample.parquet"
        intra = INTRA_FIXTURE if os.path.exists(INTRA_FIXTURE) else None
    intraday_on = st.checkbox("Intraday take-profit (hourly)", key="cham_intraday",
                              disabled=intra is None,
                              help=None if intra else "No hourly OHLC on disk for this ticker yet.")

    if st.button("Run", key="run_cham", type="primary"):
        ch = ch[(ch["date"] >= pd.Timestamp(start)) & (ch["date"] <= pd.Timestamp(end))].reset_index(drop=True)
        marks = None
        if intraday_on and intra:
            ih = pd.read_parquet(intra)
            ih["timestamp"] = pd.to_datetime(ih["timestamp"])
            # Window the chain to the hourly span (amendment 16a) so no day
            # silently falls back to EOD inside a run labelled hourly.
            h_lo, h_hi = ih["timestamp"].min().normalize(), ih["timestamp"].max().normalize()
            ch = ch[(ch["date"] >= h_lo) & (ch["date"] <= h_hi)].reset_index(drop=True)
            marks = intraday_marks(ih)
        if ch["date"].nunique() < 5:
            st.warning("Window too short."); st.stop()
        states = regime_series(closes_for(ticker))
        if states.empty:
            st.warning(f"Not enough history for a regime state on {ticker}."); st.stop()
        cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                          call_delta=call_delta, target_dte=int(target_dte),
                          take_profit_pct=tp_slider / 100.0, ticker=ticker,
                          call_min_strike="basis")
        res = run_regime_router(ch, cfg, states, intraday=marks)
        rep = router_report(res, ch, cfg)
        st.session_state["_cham_result"] = (res, rep, cfg)

    stashed = st.session_state.get("_cham_result")
    if stashed is not None:
        res, rep, cfg = stashed
        pnl = res.equity.iloc[-1] - cfg.starting_capital
        a, b, c, d = st.columns(4)
        a.metric("P&L", f"${pnl:,.0f}", f"{rep.metrics['total_return']:+.2%}")
        b.metric("Sharpe", f"{rep.metrics['sharpe']:.2f}")
        c.metric("Max drawdown", f"{rep.metrics['max_drawdown']:.2%}")
        gap = rep.metrics["total_return"] - rep.benchmark_underlying["total_return"]
        d.metric(f"vs buy-hold {rep.ticker}", f"{gap:+.2%}",
                 help=f"Buy-hold {rep.ticker} returned "
                      f"{rep.benchmark_underlying['total_return']:+.2%} over this window.")
        p = rep.posture
        st.caption(f"days TREND/WHEEL/CASH {p['days'].get('TREND',0)}/{p['days'].get('WHEEL',0)}/"
                   f"{p['days'].get('CASH',0)}  ·  transitions {p['transitions']}  ·  "
                   f"whipsaws {p['whipsaws']}  ·  intraday-TP {rep.fills['intraday_tp']}  ·  "
                   f"EOD-TP {rep.fills['eod_tp']}")

        st.subheader("Postures on price")
        st.caption("Bands: TREND (holding shares) · WHEEL (running the wheel) · "
                   "CASH (sidelined). Option legs draw only in WHEEL; TREND P&L is "
                   "in the equity curve, not the blotter.")
        w0, w1 = res.equity.index[0], res.equity.index[-1]
        bars_df = bars.load_bars(rep.ticker, w0, w1)
        if bars_df.empty:
            st.info(f"No daily bars on disk for {rep.ticker} — chart unavailable.")
        else:
            st.plotly_chart(
                charts.candles_with_trades(
                    bars_df, trades.overlay_frame(rep.blotter, w1),
                    posture=charts.posture_bands(res.route_log)),
                width="stretch")

        st.subheader("Year-by-year return")
        st.plotly_chart(charts.yearly_bars(rep.yearly_return, percent=True), width="stretch")

        st.subheader("Trade blotter (option legs)")
        display = rep.blotter.copy()
        for col in ("opened", "closed", "expiry"):
            display[col] = pd.to_datetime(display[col]).dt.strftime("%Y-%m-%d").replace("NaT", "")
        st.dataframe(labels.humanize(display), width="stretch", hide_index=True)
```

- [ ] **Step 4: Register the page in nav**

In `dashboard/app.py`, add the import and the page entry:

```python
from dashboard.views import chameleon, regime, run, wheel, wheel_history
# ...
pg = st.navigation([
    st.Page(run.render, title="Run", url_path="run", default=True),
    st.Page(wheel.render, title="Wheel", url_path="wheel"),
    st.Page(chameleon.render, title="Chameleon", url_path="chameleon"),
    st.Page(regime.render, title="Regime", url_path="regime"),
    st.Page(wheel_history.render, title="History", url_path="history"),
])
```

- [ ] **Step 5: Run the smoke test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_chameleon_dashboard.py -v`
Expected: PASS (2 tests) — page runs a router result without the `days_flat` crash, unseen tickers absent from the dropdown.

- [ ] **Step 6: Commit**

```bash
git add dashboard/views/chameleon.py dashboard/app.py tests/test_chameleon_dashboard.py
git commit -m "feat(dashboard): Chameleon page — posture-shaded router chart + blotter"
```

---

### Task 7: Full-suite regression + referee sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: all green (prior count + the new guard/report/posture/chameleon tests).

- [ ] **Step 2: Manual dashboard smoke (optional but recommended)**

Run: `.venv/bin/python -m streamlit run dashboard/app.py`
Check: the Chameleon page loads, SPY/GDX/SLV/XOP are the only real tickers, a run renders posture bands + tiles + blotter, and the off-config banner flips when you change DTE off 7.

- [ ] **Step 3: Commit any doc/status touch-ups if made**

```bash
git add -A && git commit -m "chore: Chameleon dashboard page — suite green"
```

---

## Self-Review

**Spec coverage:**
- Shared allow-list guard (`guard.py`) → Task 1; wheel/regime swapped → Task 2. ✓
- Router-aware report (fork A, no `days_flat`) → Task 3. ✓
- `posture_bands` + posture overlay → Tasks 4–5. ✓
- Chameleon page mirroring wheel, full knobs, hourly windowing, XOP warning, off-config banner → Task 6. ✓
- Nav registration → Task 6 Step 4. ✓
- Tests: guard allow-list, router page smoke (days_flat crash), `posture_bands` unit, `router_report` unit → Tasks 1,3,4,6; plus wheel/regime regression → Task 2, full suite → Task 7. ✓
- Non-goals respected: no engine change; unseen tickers unreachable (allow-list, test-pinned); referee stays the citation gate (page is exploration-only, captioned). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step has real assertions. ✓

**Type consistency:** `seen_sources` signature identical in Tasks 1/2/6; `router_report` return keys (`metrics`/`posture`/`fills`/`benchmark_underlying`/`yearly_return`/`blotter`/`ticker`) consistent between Task 3 definition and Task 6 use; `posture_bands` span tuple `(start, end, posture)` consistent Tasks 4/5/6; `candles_with_trades(..., posture=...)` consistent Tasks 5/6. ✓

**One risk to watch at execution:** Task 6's smoke test needs `fixtures/spy_wheel_intraday_sample.parquet` only for the hourly checkbox; the smoke test runs EOD (checkbox left off), so it does not depend on that fixture. `bars.load_bars` for the fixture ticker returns SPY daily bars — if absent on disk the test still passes (the `bars_df.empty` branch renders an info box, no exception).
