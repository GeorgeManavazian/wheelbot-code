# Backtest Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A data-agnostic, frequency-aware backtest workbench — pick a strategy + tickers + dates in the dashboard, click Run, see equity / CAGR / Sharpe / max-drawdown / year-by-year / per-regime / vs SPY + 60/40, with no honesty-gate verdict.

**Architecture:** Build on the already-working v2 sim (`position_history` / `_simulate`: real P&L, spread, borrow, long+short). Add a clean `run_simple` seam that bypasses CPCV + `compute_verdict`, a frequency-aware `Result`/metrics layer, a pluggable `DataSource` (fixture-backed for now), a strategy registry, and a dashboard Run page. Archive the parked gate + v1 engine last.

**Tech Stack:** Python 3.11, pandas, numpy, Streamlit, pytest. Package `src/engine_v2`, dashboard `dashboard/`. Test runner `.venv/bin/pytest`.

## Global Constraints

- Do NOT modify the P&L / cost logic inside `_simulate` — it is verified correct (spread + borrow + slippage bite). Only add an optional, backward-compatible `periods_per_year` parameter.
- No CPCV, DSR, FWER, NCO, or `compute_verdict` on the workbench path. The Run page must never import `gate/` or `orchestrator.run_backtest`.
- Bars are a pandas DataFrame with MultiIndex columns `(ticker, field)` where `field ∈ {Open,High,Low,Close,...}`, DatetimeIndex rows — the shape the fixture and `_simulate` already use.
- Frequency-awareness: annualization derives from median bar spacing; default `periods_per_year=252` preserves all existing behavior on daily bars.
- **Primary data is the real universe** `fixtures/bars_etf_universe_2010_2026.parquet` (20 ETFs, 2010–2026). The small `bars_2007_2010_small.parquet` (SPY/TLT/GLD) is retained ONLY as the fast unit-test bed. The workbench's default `DataSource` points at the universe file.
- **Standing evaluation methodology (owner, durable — `backtest-judge-on-recent`):** judge on the RECENT window (~last 5y, headline), keep FULL history for context, and ALWAYS expose **year-by-year Sharpe** (decay curve). Never headline one blended multi-year number. The `Result`/metrics layer must implement this — it is independent of `scripts/evaluate.py` (which is archived).
- `RECENT_START = "2021-07-01"` for the recent-window headline on the 2010–2026 universe.
- Archive means `git mv` into `archive/`, never delete (owner decision: park not delete).
- Every task ends green: `.venv/bin/pytest tests/engine_v2/ -q` (plus the task's own new test) passes before commit.

---

### Task 1: `DataSource` interface + `ParquetSource` (+ real-universe default)

**Files:**
- Create: `src/engine_v2/data/source.py`
- Test: `tests/engine_v2/test_source.py`

**Interfaces:**
- Consumes: existing `src/engine_v2/data/loader.py::load_bars(tickers, start, end, source, path)`.
- Produces:
  - `class DataSource(Protocol)` with `load(tickers, start, end) -> pd.DataFrame`, `available_tickers() -> list[str]`, `date_range() -> tuple[pd.Timestamp, pd.Timestamp]`.
  - Module constants `FIXTURE_PATH` (small 3yr test bed) and `UNIVERSE_PATH` (real 20-ETF 2010–2026).
  - `class ParquetSource(path=UNIVERSE_PATH)` implementing all three.
  - `def default_source() -> ParquetSource` returning the real-universe source (what the Run page uses).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/test_source.py
import pandas as pd
from src.engine_v2.data.source import ParquetSource, default_source, FIXTURE_PATH, UNIVERSE_PATH

def test_fixture_source_lists_tickers_and_range():
    src = ParquetSource(FIXTURE_PATH)
    assert set(src.available_tickers()) == {"SPY", "TLT", "GLD"}
    lo, hi = src.date_range()
    assert lo == pd.Timestamp("2007-01-03")
    assert hi == pd.Timestamp("2010-12-30")

def test_fixture_source_load_slices():
    src = ParquetSource(FIXTURE_PATH)
    bars = src.load(["SPY", "TLT"], "2008-01-01", "2008-12-31")
    assert set(bars.columns.get_level_values(0)) == {"SPY", "TLT"}
    assert bars.index.min() >= pd.Timestamp("2008-01-01")
    assert bars.index.max() <= pd.Timestamp("2008-12-31")

def test_default_source_is_real_universe():
    src = default_source()
    tk = set(src.available_tickers())
    assert {"SPY", "QQQ", "IWM", "TLT", "GLD"} <= tk
    assert len(tk) == 20
    lo, hi = src.date_range()
    assert lo == pd.Timestamp("2010-01-04")
    assert hi >= pd.Timestamp("2026-06-30")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/test_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine_v2.data.source'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/engine_v2/data/source.py
"""Pluggable bar source. Workbench code depends on this Protocol, never on a
concrete loader, so an intraday source (ThetaData / Schwab) drops in later.
The default source is the real 20-ETF daily universe; the small fixture is the
fast unit-test bed only."""
from __future__ import annotations
from typing import Protocol
import pandas as pd
from .loader import load_bars

FIXTURE_PATH = "fixtures/bars_2007_2010_small.parquet"
UNIVERSE_PATH = "fixtures/bars_etf_universe_2010_2026.parquet"

class DataSource(Protocol):
    def load(self, tickers, start, end) -> pd.DataFrame: ...
    def available_tickers(self) -> list[str]: ...
    def date_range(self) -> tuple[pd.Timestamp, pd.Timestamp]: ...

class ParquetSource:
    """DataSource backed by a checked-in parquet of MultiIndex (ticker, field) bars."""
    def __init__(self, path: str = UNIVERSE_PATH):
        self.path = path
        self._df = pd.read_parquet(path)

    def load(self, tickers, start, end) -> pd.DataFrame:
        return load_bars(list(tickers), start, end, source="parquet", path=self.path)

    def available_tickers(self) -> list[str]:
        return sorted(set(self._df.columns.get_level_values(0)))

    def date_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return self._df.index.min(), self._df.index.max()

def default_source() -> ParquetSource:
    """The real 20-ETF 2010–2026 universe — what the dashboard runs on."""
    return ParquetSource(UNIVERSE_PATH)
```

Note: `load_bars` enforces `MODERN_ERA_START = 2007-01-01`; the universe starts 2010, so no conflict. `load_bars` raises `KeyError` for tickers absent from the file — this is the unknown-ticker path the Run page surfaces.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/test_source.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/data/source.py tests/engine_v2/test_source.py
git commit -m "feat: pluggable DataSource + ParquetSource (real-universe default)"
```

---

### Task 2: Frequency-aware metrics (pure functions)

**Files:**
- Create: `src/engine_v2/backtest/metrics_simple.py`
- Test: `tests/engine_v2/test_metrics_simple.py`

**Interfaces:**
- Consumes: nothing beyond pandas/numpy and `data/regime.py::tag_regime`.
- Produces (all operate on a per-bar equity `pd.Series` indexed by DatetimeIndex, or a return series):
  - `infer_periods_per_year(index: pd.DatetimeIndex) -> float`
  - `cagr(equity: pd.Series, periods_per_year: float) -> float`
  - `sharpe(returns: pd.Series, periods_per_year: float) -> float`
  - `max_drawdown(equity: pd.Series) -> float`  (negative fraction)
  - `drawdown_series(equity: pd.Series) -> pd.Series`
  - `yearly_returns(equity: pd.Series) -> pd.Series`  (index = year int, value = fractional return)
  - `yearly_sharpe(returns: pd.Series, periods_per_year: float) -> pd.Series`  (index = year int, value = annualized Sharpe — the decay curve the standing methodology requires)
  - `buy_hold_equity(bars: pd.DataFrame, weights: dict[str, float], starting_equity: float) -> pd.Series`
  - `regime_breakdown(returns: pd.Series, bars: pd.DataFrame, periods_per_year: float) -> pd.DataFrame`

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/test_metrics_simple.py
import numpy as np
import pandas as pd
import pytest
from src.engine_v2.backtest import metrics_simple as m

def _daily(n=252, start="2010-01-01"):
    return pd.date_range(start, periods=n, freq="B")

def test_infer_periods_per_year_daily():
    idx = _daily(300)
    assert m.infer_periods_per_year(idx) == pytest.approx(252, abs=6)

def test_infer_periods_per_year_minute():
    idx = pd.date_range("2010-01-04 09:30", periods=400, freq="T")
    # ~390 trading minutes/day * 252 days
    assert m.infer_periods_per_year(idx) > 90_000

def test_cagr_and_maxdd_on_known_curve():
    idx = _daily(253)
    equity = pd.Series(np.linspace(100.0, 110.0, 253), index=idx)  # +10% over ~1y
    assert m.cagr(equity, 252) == pytest.approx(0.10, abs=0.02)
    assert m.max_drawdown(equity) == pytest.approx(0.0, abs=1e-9)

def test_sharpe_frequency_scales():
    idx = _daily(252)
    rets = pd.Series(np.full(252, 0.001), index=idx)  # constant positive, zero std
    # zero variance -> guarded to 0.0, not inf/nan
    assert m.sharpe(rets, 252) == 0.0

def test_yearly_returns_splits_by_year():
    idx = pd.date_range("2010-01-01", "2011-12-31", freq="B")
    equity = pd.Series(np.linspace(100, 121, len(idx)), index=idx)
    yr = m.yearly_returns(equity)
    assert set(yr.index) == {2010, 2011}

def test_yearly_sharpe_one_per_year():
    idx = pd.date_range("2010-01-01", "2011-12-31", freq="B")
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    ys = m.yearly_sharpe(rets, 252)
    assert set(ys.index) == {2010, 2011}
    assert ys.notna().all()

def test_buy_hold_matches_hand_calc():
    idx = _daily(3)
    cols = pd.MultiIndex.from_tuples([("SPY", "Close")])
    bars = pd.DataFrame([[100.0], [110.0], [121.0]], index=idx, columns=cols)
    eq = m.buy_hold_equity(bars, {"SPY": 1.0}, 100_000.0)
    assert eq.iloc[-1] == pytest.approx(121_000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/test_metrics_simple.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine_v2.backtest.metrics_simple'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/engine_v2/backtest/metrics_simple.py
"""Frequency-aware, gate-free diagnostics computed from a per-bar equity curve.
Annualization derives from bar spacing so the same code is correct for daily
bars now and intraday bars later. No pass/fail — diagnostics only."""
from __future__ import annotations
import numpy as np
import pandas as pd
from ..data.regime import tag_regime, REGIME_COLS

def infer_periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 252.0
    deltas = np.diff(index.values).astype("timedelta64[s]").astype(float)
    med_s = float(np.median(deltas))
    if med_s <= 0:
        return 252.0
    year_s = 365.25 * 24 * 3600
    if med_s >= 20 * 3600:          # daily-or-coarser bars: count trading days
        return 252.0
    trading_seconds_per_year = 252 * 6.5 * 3600   # 6.5h session
    return trading_seconds_per_year / med_s

def cagr(equity: pd.Series, periods_per_year: float) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    total = equity.iloc[-1] / equity.iloc[0]
    years = len(equity) / periods_per_year
    if years <= 0 or total <= 0:
        return float("nan")
    return float(total ** (1 / years) - 1)

def sharpe(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = float(r.std())
    if sd == 0.0 or np.isnan(sd):
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))

def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0

def max_drawdown(equity: pd.Series) -> float:
    if len(equity) == 0:
        return float("nan")
    return float(drawdown_series(equity).min())

def yearly_returns(equity: pd.Series) -> pd.Series:
    if len(equity) == 0:
        return pd.Series(dtype=float)
    by_year = equity.groupby(equity.index.year)
    first, last = by_year.first(), by_year.last()
    return (last / first - 1.0).rename("return")

def yearly_sharpe(returns: pd.Series, periods_per_year: float) -> pd.Series:
    """Annualized Sharpe per calendar year — the decay curve the standing
    methodology requires. One value per year present in the index."""
    r = returns.dropna()
    if len(r) == 0:
        return pd.Series(dtype=float)
    out = {y: sharpe(grp, periods_per_year) for y, grp in r.groupby(r.index.year)}
    return pd.Series(out, name="sharpe")

def buy_hold_equity(bars: pd.DataFrame, weights: dict, starting_equity: float) -> pd.Series:
    eq = None
    for tkr, w in weights.items():
        close = bars[tkr]["Close"]
        norm = close / close.iloc[0]
        leg = w * starting_equity * norm
        eq = leg if eq is None else eq + leg
    return eq.rename("benchmark")

def regime_breakdown(returns: pd.Series, bars: pd.DataFrame,
                     periods_per_year: float) -> pd.DataFrame:
    labels = tag_regime(bars).reindex(returns.index).ffill()
    rows = []
    for col in REGIME_COLS:
        for val, grp in returns.groupby(labels[col]):
            rows.append({"dimension": col, "regime": val,
                         "sharpe": sharpe(grp, periods_per_year),
                         "mean_ret": float(grp.mean()), "bars": int(len(grp))})
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/test_metrics_simple.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/backtest/metrics_simple.py tests/engine_v2/test_metrics_simple.py
git commit -m "feat: frequency-aware gate-free metrics"
```

---

### Task 3: `periods_per_year` passthrough into `_simulate`, `run_simple` seam + `Result`

**Files:**
- Modify: `src/engine_v2/backtest/orchestrator.py` (`_instrument_sigma`, `_simulate`, `position_history` — add optional `periods_per_year`)
- Create: `src/engine_v2/backtest/simple.py`
- Test: `tests/engine_v2/test_simple.py`

**Interfaces:**
- Consumes: `orchestrator.position_history`, `BacktestConfig`; `metrics_simple.*`; `strategy.protocol.validate_plugin`.
- Produces:
  - `@dataclass Result` with fields: `equity: pd.Series`, `returns: pd.Series`, `trades: int`, `cagr: float`, `sharpe: float`, `max_drawdown: float`, `yearly: pd.Series`, `yearly_sharpe: pd.Series`, `recent: dict` (keys `start`, `cagr`, `sharpe`, `max_drawdown`), `regime: pd.DataFrame`, `benchmarks: dict[str, pd.Series]`, `periods_per_year: float`. Per the standing methodology, `recent` is the HEADLINE and `yearly_sharpe` is the decay curve.
  - `run_simple(strategy_cls, bars, config=None, params=None, periods_per_year=None, recent_start="2021-07-01") -> Result`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/test_simple.py
import pandas as pd
import pytest
from src.engine_v2.backtest.simple import run_simple, Result
from src.engine_v2.backtest.orchestrator import BacktestConfig, position_history
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy as Strat

BARS = pd.read_parquet("fixtures/bars_2007_2010_small.parquet")

def test_run_simple_equity_matches_position_history():
    cfg = BacktestConfig()
    res = run_simple(Strat, BARS, config=cfg, params={})
    ref = position_history(Strat, {}, BARS, BARS.index, cfg)
    assert isinstance(res, Result)
    assert res.equity.iloc[-1] == pytest.approx(ref["equity"].iloc[-1])

def test_run_simple_has_gate_free_diagnostics():
    res = run_simple(Strat, BARS, params={})
    assert res.periods_per_year == pytest.approx(252, abs=6)
    assert not hasattr(res, "verdict")
    assert set(res.benchmarks) == {"SPY", "60_40"}
    assert res.trades > 0
    assert -1.0 <= res.max_drawdown <= 0.0

def test_run_simple_carries_recent_and_yearly_sharpe():
    # recent_start inside the fixture window (2007-2010) so the slice is non-empty
    res = run_simple(Strat, BARS, params={}, recent_start="2009-01-01")
    assert set(res.recent) == {"start", "cagr", "sharpe", "max_drawdown"}
    assert res.recent["start"] == "2009-01-01"
    assert len(res.yearly_sharpe) >= 1
    assert res.yearly_sharpe.index.min() >= 2007

def test_higher_spread_lowers_end_equity():
    lo = run_simple(Strat, BARS, config=BacktestConfig(spread_bps_per_side=0.0), params={})
    hi = run_simple(Strat, BARS, config=BacktestConfig(spread_bps_per_side=50.0), params={})
    assert hi.equity.iloc[-1] < lo.equity.iloc[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/test_simple.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine_v2.backtest.simple'`

- [ ] **Step 3a: Add backward-compatible `periods_per_year` to orchestrator**

In `src/engine_v2/backtest/orchestrator.py`, change `_instrument_sigma` and thread the parameter through `_simulate` and `position_history`. Default `252` preserves current behavior.

```python
def _instrument_sigma(bars, tkr, asof, periods_per_year: float = 252.0) -> float:
    past = bars[tkr]["Close"].loc[:asof].pct_change().dropna().tail(60)
    return float(past.std() * (periods_per_year ** 0.5)) if len(past) >= 20 else 0.20
```

In `_simulate`, add `periods_per_year: float = 252.0` to the signature and pass it into the `_instrument_sigma(...)` call:

```python
def _simulate(strategy_cls, params, bars, test_index, cfg: BacktestConfig,
              periods_per_year: float = 252.0):
    ...
                target_notional = size_position(
                    float(f), equity,
                    _instrument_sigma(bars, tkr, asof, periods_per_year),
                    target_risk=cfg.target_risk,
                )
```

In `position_history`, add the same param and forward it:

```python
def position_history(strategy_cls, params, bars, test_index, cfg: BacktestConfig,
                     periods_per_year: float = 252.0):
    """Per-bar equity, net position and shares traded. For tests and diagnostics."""
    return _simulate(strategy_cls, params, bars, test_index, cfg, periods_per_year)
```

- [ ] **Step 3b: Write `simple.py`**

```python
# src/engine_v2/backtest/simple.py
"""Clean backtest seam: one straight pass over the full date range, no CPCV,
no gate, no verdict. Returns rich diagnostics. This is the workbench entrypoint."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from ..strategy.protocol import validate_plugin
from .orchestrator import position_history, BacktestConfig
from . import metrics_simple as m

@dataclass
class Result:
    equity: pd.Series
    returns: pd.Series
    trades: int
    cagr: float
    sharpe: float
    max_drawdown: float
    yearly: pd.Series
    yearly_sharpe: pd.Series
    recent: dict
    regime: pd.DataFrame
    benchmarks: dict
    periods_per_year: float

def run_simple(strategy_cls, bars: pd.DataFrame,
               config: BacktestConfig | None = None,
               params: dict | None = None,
               periods_per_year: float | None = None,
               recent_start: str = "2021-07-01") -> Result:
    validate_plugin(strategy_cls)
    cfg = config or BacktestConfig()
    ppy = periods_per_year or m.infer_periods_per_year(bars.index)

    hist = position_history(strategy_cls, params or {}, bars, bars.index, cfg, ppy)
    equity = hist["equity"]
    returns = equity.pct_change().fillna(0.0)

    # Standing methodology: recent window is the HEADLINE; keep full history too.
    eq_recent = equity[equity.index >= recent_start]
    ret_recent = returns[returns.index >= recent_start]
    recent = {
        "start": recent_start,
        "cagr": m.cagr(eq_recent, ppy),
        "sharpe": m.sharpe(ret_recent, ppy),
        "max_drawdown": m.max_drawdown(eq_recent),
    }

    benchmarks = {"SPY": m.buy_hold_equity(bars, {"SPY": 1.0}, cfg.starting_equity)}
    if "TLT" in set(bars.columns.get_level_values(0)):
        benchmarks["60_40"] = m.buy_hold_equity(
            bars, {"SPY": 0.6, "TLT": 0.4}, cfg.starting_equity)

    return Result(
        equity=equity,
        returns=returns,
        trades=int((hist["traded"] > 0).sum()),
        cagr=m.cagr(equity, ppy),
        sharpe=m.sharpe(returns, ppy),
        max_drawdown=m.max_drawdown(equity),
        yearly=m.yearly_returns(equity),
        yearly_sharpe=m.yearly_sharpe(returns, ppy),
        recent=recent,
        regime=m.regime_breakdown(returns, bars, ppy),
        benchmarks=benchmarks,
        periods_per_year=ppy,
    )
```

- [ ] **Step 4: Run tests to verify pass (new + existing orchestrator suite unchanged)**

Run: `.venv/bin/pytest tests/engine_v2/test_simple.py tests/engine_v2/ -q`
Expected: PASS — new file 3 passed; the existing orchestrator/`position_history` tests still pass (default `periods_per_year=252` preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/backtest/simple.py src/engine_v2/backtest/orchestrator.py tests/engine_v2/test_simple.py
git commit -m "feat: run_simple gate-free seam + Result, frequency passthrough"
```

---

### Task 4: Strategy registry

**Files:**
- Create: `src/engine_v2/strategy/registry.py`
- Test: `tests/engine_v2/test_registry.py`

**Interfaces:**
- Consumes: `strategy/counter_trend.py::CounterTrendDipBuy`, `strategy/gap_pattern.py` (its plugin class), `protocol.validate_plugin`.
- Produces: `STRATEGIES: dict[str, type]` (display_name -> class) and `get_strategy(display_name) -> type`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/test_registry.py
import pytest
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy
from src.engine_v2.strategy.protocol import validate_plugin

def test_registry_lists_known_plugins():
    names = list(STRATEGIES)
    assert any("Counter-Trend" in n for n in names)
    assert len(names) >= 2

def test_every_registered_plugin_is_valid():
    for cls in STRATEGIES.values():
        validate_plugin(cls)  # raises if not

def test_get_strategy_roundtrips():
    name = next(iter(STRATEGIES))
    assert get_strategy(name) is STRATEGIES[name]

def test_get_strategy_unknown_raises():
    with pytest.raises(KeyError):
        get_strategy("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine_v2.strategy.registry'`

- [ ] **Step 3: Write minimal implementation**

(Class names confirmed: `CounterTrendDipBuy` in `counter_trend.py`, `GapPatternTypeA` in `gap_pattern.py`.)

```python
# src/engine_v2/strategy/registry.py
"""Central list of runnable strategies. Add a plugin = drop a file in this
package + one line here. The dashboard reads STRATEGIES to build its dropdown."""
from __future__ import annotations
from .counter_trend import CounterTrendDipBuy
from .gap_pattern import GapPatternTypeA

_PLUGINS = [CounterTrendDipBuy, GapPatternTypeA]

STRATEGIES: dict[str, type] = {cls.display_name: cls for cls in _PLUGINS}

def get_strategy(display_name: str) -> type:
    return STRATEGIES[display_name]  # raises KeyError on unknown
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/test_registry.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/strategy/registry.py tests/engine_v2/test_registry.py
git commit -m "feat: strategy registry"
```

---

### Task 5: Dashboard Run page (default page)

**Files:**
- Create: `dashboard/views/run.py`
- Modify: `dashboard/app.py`
- Test: `tests/test_workbench_dashboard.py`

**Interfaces:**
- Consumes: `strategy.registry.STRATEGIES/get_strategy`, `data.source.default_source`, `backtest.simple.run_simple`, `backtest.orchestrator.BacktestConfig`.
- Produces: `dashboard/views/run.py::render()` (Streamlit page), reachable as the default page. Widgets carry stable `key=`s (`strategy`, `tickers`, `date_range`, `spread`, `borrow`, `run_backtest`) so tests can drive them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workbench_dashboard.py
from streamlit.testing.v1 import AppTest

def test_run_page_renders_without_crash():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception

def test_run_page_produces_a_result_on_run():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    # narrow scope so the smoke run is fast: 2 tickers, default (full) date range
    at.multiselect(key="tickers").set_value(["SPY", "TLT"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=120)
    assert not at.exception
    assert len(at.metric) >= 1  # recent-headline CAGR / Sharpe / maxDD tiles rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_workbench_dashboard.py -v`
Expected: FAIL — no `run_backtest` button / page not wired.

- [ ] **Step 3: Write the Run page**

```python
# dashboard/views/run.py
"""Run page: pick strategy + tickers + dates + cost knobs, click Run, see
gate-free diagnostics on the real 20-ETF universe. Calls run_simple
in-process — no CSV round-trip. Headline = recent window; also shows the
year-by-year Sharpe decay curve (standing methodology)."""
import streamlit as st
from src.engine_v2.strategy.registry import STRATEGIES, get_strategy
from src.engine_v2.data.source import default_source
from src.engine_v2.backtest.simple import run_simple
from src.engine_v2.backtest.orchestrator import BacktestConfig

def render():
    st.title("Run a backtest")
    src = default_source()
    tickers_all = src.available_tickers()
    lo, hi = src.date_range()

    name = st.selectbox("Strategy", list(STRATEGIES), key="strategy")
    st.caption(get_strategy(name).mechanism)
    tickers = st.multiselect("Universe", tickers_all, default=tickers_all, key="tickers")
    start, end = st.slider("Date range", min_value=lo.to_pydatetime(),
                           max_value=hi.to_pydatetime(),
                           value=(lo.to_pydatetime(), hi.to_pydatetime()),
                           key="date_range")
    spread = st.number_input("Spread (bps/side)", 0.0, 100.0, 1.0, 0.5, key="spread")
    borrow = st.number_input("Borrow (bps/yr)", 0.0, 2000.0, 50.0, 10.0, key="borrow")

    if st.button("Run", key="run_backtest", type="primary"):
        if not tickers:
            st.warning("Pick at least one ticker.")
            st.stop()
        bars = src.load(tickers, start, end)
        cfg = BacktestConfig(spread_bps_per_side=spread, borrow_bps_annual=borrow)
        res = run_simple(get_strategy(name), bars, config=cfg)

        st.subheader(f"Headline — recent since {res.recent['start']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("CAGR", f"{res.recent['cagr']:.2%}")
        c2.metric("Sharpe", f"{res.recent['sharpe']:.2f}")
        c3.metric("Max drawdown", f"{res.recent['max_drawdown']:.2%}")
        st.caption(f"Full history: CAGR {res.cagr:.2%} · Sharpe {res.sharpe:.2f} · "
                   f"maxDD {res.max_drawdown:.2%} · trades {res.trades}")

        curve = res.equity.rename("strategy").to_frame()
        for bname, series in res.benchmarks.items():
            curve[bname] = series
        st.subheader("Equity curve")
        st.line_chart(curve)
        st.subheader("Year-by-year Sharpe (decay curve)")
        st.bar_chart(res.yearly_sharpe)
        st.subheader("Year-by-year return")
        st.bar_chart(res.yearly)
        st.subheader("By regime")
        st.dataframe(res.regime, use_container_width=True)
        st.caption(f"bars/yr ≈ {res.periods_per_year:.0f}")
```

- [ ] **Step 4: Rewire `app.py` to default to the Run page**

Replace the body of `dashboard/app.py` with a Run-first navigation (the leaderboard/compare/plateau pages are archived in Task 6, so import only `run` here):

```python
# dashboard/app.py
"""Entry point:  .venv/bin/python -m streamlit run dashboard/app.py
Run from the repo root."""
import streamlit as st
from dashboard.views import run

st.set_page_config(page_title="ETF Bot Workbench", layout="wide")
pg = st.navigation([st.Page(run.render, title="Run", url_path="run", default=True)])
pg.run()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_workbench_dashboard.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add dashboard/views/run.py dashboard/app.py tests/test_workbench_dashboard.py
git commit -m "feat: dashboard Run page (in-process, gate-free)"
```

---

### Task 6: Archive the noise (option B)

**Files (git mv into `archive/`, preserving relative paths):**
- `src/engine_v2/gate/` → `archive/engine_v2/gate/`
- `src/engine/`, `src/batch/`, `src/strategies/` → `archive/src/...`
- `scripts/screen.py`, `scripts/evaluate.py`, `scripts/freeze_split.py` → `archive/scripts/...`
- `dashboard/verdict_panel.py`, `dashboard/recompute.py`, `dashboard/loader.py`, `dashboard/benchmark.py` → `archive/dashboard/...`
- `dashboard/views/{leaderboard,compare,plateau,run_detail}.py` → `archive/dashboard/views/...`
- Their tests (v1 backtest/metrics/batch, old dashboard render) → `archive/tests/...`

**Interfaces:**
- Consumes: nothing. This task only moves parked code and removes now-dead imports.
- Produces: a repo whose live tree contains only the workbench path.

- [ ] **Step 1: Find every reference into the code being archived**

Run: `grep -rn "engine_v2.gate\|from src.engine\b\|src.batch\|src.strategies\|dashboard.verdict_panel\|dashboard.recompute\|dashboard.loader\|dashboard.benchmark\|views import compare\|views import leaderboard\|views import plateau\|views import run_detail\|orchestrator import run_backtest\|compute_verdict" src dashboard tests`
Expected: the only live hits are in files being archived, or in `orchestrator.py::run_backtest` (which itself imports the gate). Note each hit.

- [ ] **Step 2: Confirm the workbench does not import the gate**

Run: `grep -rn "gate\|compute_verdict\|run_backtest" src/engine_v2/backtest/simple.py src/engine_v2/backtest/metrics_simple.py dashboard/views/run.py dashboard/app.py`
Expected: no matches. If any appear, stop and fix before moving files.

- [ ] **Step 3: Move the files**

```bash
mkdir -p archive
git mv src/engine_v2/gate archive/engine_v2_gate
git mv src/engine archive/src_engine
git mv src/batch archive/src_batch
git mv src/strategies archive/src_strategies
git mv scripts/screen.py archive/screen.py
git mv scripts/evaluate.py archive/evaluate.py
git mv scripts/freeze_split.py archive/freeze_split.py
git mv dashboard/verdict_panel.py archive/verdict_panel.py
git mv dashboard/recompute.py archive/recompute.py
git mv dashboard/loader.py archive/loader.py
git mv dashboard/benchmark.py archive/benchmark.py
git mv dashboard/views/leaderboard.py archive/leaderboard.py
git mv dashboard/views/compare.py archive/compare.py
git mv dashboard/views/plateau.py archive/plateau.py
git mv dashboard/views/run_detail.py archive/run_detail.py
```

- [ ] **Step 4: Move the orphaned tests**

Run: `grep -rln "from src.engine\b\|src.batch\|src.strategies\|dashboard.loader\|dashboard.recompute\|verdict_panel\|views.leaderboard\|views.compare\|views.plateau\|views.run_detail" tests`
For each file printed, `git mv tests/<file> archive/tests/<file>`. (These test archived code; keep them parked, not deleted.)

- [ ] **Step 5: Neutralize `orchestrator.run_backtest`'s gate import**

`run_backtest` imports `from ..gate.verdict import compute_verdict`, now archived. The workbench never calls `run_backtest`, but the module must still import. Delete the `run_backtest` function and its now-unused imports (`compute_verdict`, `make_folds`, `cpcv_combos`, `purged_train_index`, `expand_trials`, `tag_regime` if unused elsewhere in the file) from `orchestrator.py`. Keep `_simulate`, `position_history`, `_fill_at_close_via_sim`, `_instrument_sigma`, `BacktestConfig`, `_fold_trial_returns` if still referenced by tests — otherwise archive `_fold_trial_returns` too. Verify:

Run: `.venv/bin/python -c "import src.engine_v2.backtest.orchestrator"`
Expected: no ImportError.

- [ ] **Step 6: Run the full live suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS. Collection errors mean a live test still imports archived code — move that test to `archive/tests/` (repeat Step 4) until green.

- [ ] **Step 7: Update the README launch line**

In `README.md`, ensure the run instruction is:
```
.venv/bin/python -m streamlit run dashboard/app.py
```
and note that `archive/` holds the parked honesty-gate + v1 engine.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: archive parked gate + v1 engine; workbench is the live path"
```

---

## Self-Review

**Spec coverage:**
- `DataSource` + `ParquetSource` + real-universe default → Task 1 ✓
- `run_simple` seam bypassing gate → Task 3 ✓
- `Result` + metrics (CAGR/Sharpe/maxDD/yearly return + yearly Sharpe/per-regime/benchmarks) → Tasks 2, 3 ✓
- Standing methodology (recent-window headline + year-by-year Sharpe) → Tasks 2, 3, 5 ✓
- Real 20-ETF universe as primary data → Tasks 1, 5 ✓
- Frequency-aware annualization (metrics + `_instrument_sigma`) → Tasks 2, 3 ✓
- Strategy registry → Task 4 ✓
- Dashboard Run page + default + dynamic pickers → Task 5 ✓
- Archive noise (gate, verdict, v1, scripts incl. evaluate.py, old pages) → Task 6 ✓
- Real data pull (yfinance) dropped/deferred; intraday still absent → not a task, correct ✓
- Non-goals (no CPCV/DSR/FWER/NCO/verdict on path) → enforced in Task 6 Step 2 + Global Constraints ✓

**Placeholder scan:** none. Gap plugin class name confirmed `GapPatternTypeA` (Task 4). No TBD/TODO/"handle edge cases".

**Type consistency:** `Result` fields (Task 3) match what the Run page reads (Task 5): `equity`, `cagr`, `sharpe`, `max_drawdown`, `yearly`, `yearly_sharpe`, `recent` (dict: `start`/`cagr`/`sharpe`/`max_drawdown`), `regime`, `benchmarks`, `trades`, `periods_per_year`. `run_simple`/`position_history` signatures carry `periods_per_year` consistently (Tasks 2/3); `run_simple` also takes `recent_start`. `metrics_simple` function names (`yearly_sharpe` added) match their call sites in `simple.py`. Task 1 exposes `default_source`/`ParquetSource`/`FIXTURE_PATH`/`UNIVERSE_PATH` as Task 5 imports them.
