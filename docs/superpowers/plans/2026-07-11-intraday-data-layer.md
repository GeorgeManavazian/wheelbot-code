# Intraday Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest on intraday (1-minute) SPY/QQQ bars in the existing workbench by adapting clean local Databento parquets into the `DataSource` seam — no live API, no purchase.

**Architecture:** A build script normalizes the external `~/Documents/Trading data/{SPY,QQQ}_1m_*_ARCX.parquet` files (RTH filter, capitalize OHLCV, tz-naive ET, MultiIndex `(ticker, field)`) into a gitignored cache plus a small committed fixture. `intraday_source()` reuses the existing `ParquetSource` against that cache. The Run page gains a Daily/Intraday selector. Frequency-aware metrics already handle minute bars — no engine changes.

**Tech Stack:** Python 3.11, pandas, Streamlit, pytest. `.venv/bin/python`, `.venv/bin/pytest`.

## Global Constraints

- Bars are a pandas DataFrame with MultiIndex columns `(ticker, field)` where `field ∈ {Open,High,Low,Close,Volume}` (capitalized — the sim reads `bars[tkr]["Close"]`), DatetimeIndex rows, **tz-naive ET**.
- Do NOT modify `_simulate`/`run_simple`/`metrics_simple` — intraday works through them unchanged (metrics derive `periods_per_year` from bar spacing).
- Do NOT import `gate/`, `compute_verdict`, or `orchestrator.run_backtest` anywhere.
- Source files are external and machine-specific; the build script takes `--source-dir` (default `~/Documents/Trading data`). Library code never hard-codes that path.
- The full cache (`data/intraday/intraday_1m.parquet`, ~40 MB) is **gitignored**. Only the small fixture (`fixtures/intraday_1m_small.parquet`, ~5 trading days) is committed.
- Known edge: `load_bars` slices `df.loc[start:pd.Timestamp(end)]`. A **date-only** `end` truncates that day's intraday bars (minutes are after 00:00). Tests and callers must pass real timestamps (e.g. from `date_range()`), not bare dates, when they want the last day included.
- Every task ends green: `.venv/bin/pytest tests/ -q` passes before commit.

---

### Task 1: `build_intraday_cache.py` — normalize source → cache + committed fixture

**Files:**
- Create: `scripts/build_intraday_cache.py`
- Modify: `.gitignore`
- Create (artifact, committed): `fixtures/intraday_1m_small.parquet`
- Test: `tests/test_build_intraday_cache.py`

**Interfaces:**
- Consumes: external parquets `~/Documents/Trading data/{SPY,QQQ}_1m_2021-07-01_2026-07-03_ARCX.parquet` (cols `open/high/low/close/volume`, tz-aware ET DatetimeIndex).
- Produces:
  - `normalize_ticker(df: pd.DataFrame, ticker: str, include_extended: bool = False) -> pd.DataFrame` — pure, testable: RTH filter + capitalize + tz-naive + MultiIndex `(ticker, field)`.
  - `build(source_dir: str, include_extended: bool = False) -> pd.DataFrame` — reads both tickers, concats on the minute index.
  - `main()` — writes `data/intraday/intraday_1m.parquet` + `fixtures/intraday_1m_small.parquet`.
  - Module constants `TICKERS=["SPY","QQQ"]`, `CACHE_PATH`, `FIXTURE_PATH`, `RTH_START="09:30"`, `RTH_END="15:59"`.

- [ ] **Step 1: Write the failing test** (synthetic data — no external files needed)

```python
# tests/test_build_intraday_cache.py
import pandas as pd
import pytest
from scripts.build_intraday_cache import normalize_ticker

def _synthetic_one_day():
    # 04:00 -> 20:00 ET, 1-min, tz-aware; lowercase ohlcv
    idx = pd.date_range("2021-07-01 04:00", "2021-07-01 20:00", freq="min",
                        tz="America/New_York")
    n = len(idx)
    return pd.DataFrame(
        {"open": range(n), "high": range(n), "low": range(n),
         "close": range(n), "volume": range(n)}, index=idx)

def test_normalize_rth_only_390_bars_capitalized_tznaive():
    out = normalize_ticker(_synthetic_one_day(), "SPY", include_extended=False)
    # RTH 09:30..15:59 inclusive = 390 minutes
    assert len(out) == 390
    assert list(out.columns) == [("SPY", "Open"), ("SPY", "High"), ("SPY", "Low"),
                                 ("SPY", "Close"), ("SPY", "Volume")]
    assert out.index.tz is None
    assert out.index.min().strftime("%H:%M") == "09:30"
    assert out.index.max().strftime("%H:%M") == "15:59"

def test_normalize_include_extended_keeps_premarket():
    out = normalize_ticker(_synthetic_one_day(), "QQQ", include_extended=True)
    assert out.index.min().strftime("%H:%M") == "04:00"
    assert len(out) > 390
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_build_intraday_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.build_intraday_cache'`

- [ ] **Step 3: Write the script**

```python
# scripts/build_intraday_cache.py
"""Build a clean intraday cache from local Databento 1-minute parquets.

Source files live OUTSIDE the repo (default ~/Documents/Trading data) and are
never committed. This writes a gitignored full cache plus a small committed
fixture for tests. Run: python scripts/build_intraday_cache.py
"""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

TICKERS = ["SPY", "QQQ"]
SRC_TEMPLATE = "{ticker}_1m_2021-07-01_2026-07-03_ARCX.parquet"
CACHE_PATH = "data/intraday/intraday_1m.parquet"
FIXTURE_PATH = "fixtures/intraday_1m_small.parquet"
RTH_START, RTH_END = "09:30", "15:59"
_RENAME = {"open": "Open", "high": "High", "low": "Low",
           "close": "Close", "volume": "Volume"}

def normalize_ticker(df: pd.DataFrame, ticker: str,
                     include_extended: bool = False) -> pd.DataFrame:
    """RTH filter + capitalize OHLCV + tz-naive ET + MultiIndex (ticker, field)."""
    et = df.index.tz_convert("America/New_York")
    if not include_extended:
        t = et.time
        mask = (t >= pd.Timestamp(RTH_START).time()) & (t <= pd.Timestamp(RTH_END).time())
        df = df[mask]
        et = df.index.tz_convert("America/New_York")
    out = df.rename(columns=_RENAME)[list(_RENAME.values())].copy()
    out.index = et.tz_localize(None)
    out.columns = pd.MultiIndex.from_product([[ticker], out.columns])
    return out

def build(source_dir: str, include_extended: bool = False) -> pd.DataFrame:
    frames = []
    for ticker in TICKERS:
        p = Path(source_dir).expanduser() / SRC_TEMPLATE.format(ticker=ticker)
        if not p.exists():
            raise FileNotFoundError(f"missing source parquet: {p} (set --source-dir)")
        frames.append(normalize_ticker(pd.read_parquet(p), ticker, include_extended))
    return pd.concat(frames, axis=1).sort_index()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default="~/Documents/Trading data")
    ap.add_argument("--include-extended", action="store_true")
    args = ap.parse_args()

    out = build(args.source_dir, args.include_extended)
    Path("data/intraday").mkdir(parents=True, exist_ok=True)
    out.to_parquet(CACHE_PATH)

    days = pd.Index(out.index.normalize().unique())[:5]
    fixture = out[out.index.normalize().isin(days)]
    Path("fixtures").mkdir(exist_ok=True)
    fixture.to_parquet(FIXTURE_PATH)
    print(f"cache {CACHE_PATH}: {out.shape}; fixture {FIXTURE_PATH}: {fixture.shape}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_build_intraday_cache.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add gitignore entry and build the real artifacts**

Append to `.gitignore`:
```
data/intraday/
```

Run the build against the real source data (this produces the committed fixture + local cache):
Run: `.venv/bin/python -m scripts.build_intraday_cache`
Expected stdout like: `cache data/intraday/intraday_1m.parquet: (49XXXX, 10); fixture fixtures/intraday_1m_small.parquet: (39XX, 10)`
Verify the fixture is small and correct:
Run: `.venv/bin/python -c "import pandas as pd; d=pd.read_parquet('fixtures/intraday_1m_small.parquet'); print(d.shape); print(sorted(set(d.columns.get_level_values(0)))); print(d.index.min(), d.index.max(), d.index.tz)"`
Expected: ~3900 rows, `['QQQ', 'SPY']`, tz `None`, span ~5 trading days from 2021-07-01.

- [ ] **Step 6: Commit** (fixture yes, cache no — confirm the cache is gitignored)

Run: `git status --porcelain` and confirm `data/intraday/intraday_1m.parquet` does NOT appear.
```bash
git add scripts/build_intraday_cache.py tests/test_build_intraday_cache.py .gitignore fixtures/intraday_1m_small.parquet
git commit -m "feat: intraday cache build script + committed 1-min SPY/QQQ fixture"
```

---

### Task 2: `intraday_source()` + prove `run_simple` runs on intraday

**Files:**
- Modify: `src/engine_v2/data/source.py`
- Test: `tests/engine_v2/test_intraday_source.py`

**Interfaces:**
- Consumes: existing `ParquetSource`; `backtest.simple.run_simple`; a plugin (`CounterTrendDipBuy`).
- Produces:
  - Constants `INTRADAY_PATH = "data/intraday/intraday_1m.parquet"`, `INTRADAY_FIXTURE_PATH = "fixtures/intraday_1m_small.parquet"`.
  - `intraday_source(path: str = INTRADAY_PATH) -> ParquetSource`.

- [ ] **Step 1: Write the failing test** (uses the committed fixture, not the full cache)

```python
# tests/engine_v2/test_intraday_source.py
import pandas as pd
import pytest
from src.engine_v2.data.source import intraday_source, INTRADAY_FIXTURE_PATH
from src.engine_v2.backtest.simple import run_simple
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy as Strat

def _src():
    return intraday_source(INTRADAY_FIXTURE_PATH)

def test_intraday_source_lists_spy_qqq():
    src = _src()
    assert set(src.available_tickers()) == {"SPY", "QQQ"}
    lo, hi = src.date_range()
    assert lo.tz is None and hi.tz is None
    assert lo.year == 2021

def test_intraday_source_load_slices_by_timestamp():
    src = _src()
    lo, hi = src.date_range()
    bars = src.load(["SPY"], lo, hi)          # real timestamps -> last day included
    assert ("SPY", "Close") in bars.columns
    assert len(bars) > 300                    # multiple RTH days of minutes

def test_run_simple_on_intraday_is_frequency_aware():
    src = _src()
    lo, hi = src.date_range()
    bars = src.load(["SPY", "QQQ"], lo, hi)
    res = run_simple(Strat, bars, params={})
    # 1-min RTH bars -> ~98k periods/yr, NOT 252
    assert res.periods_per_year > 90_000
    assert res.equity.nunique() > 1           # equity actually moves
    assert not pd.isna(res.sharpe)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/test_intraday_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'intraday_source'`

- [ ] **Step 3: Add to `src/engine_v2/data/source.py`**

Add the constants next to the existing `FIXTURE_PATH`/`UNIVERSE_PATH`:
```python
INTRADAY_PATH = "data/intraday/intraday_1m.parquet"
INTRADAY_FIXTURE_PATH = "fixtures/intraday_1m_small.parquet"
```
Add the factory next to `default_source`:
```python
def intraday_source(path: str = INTRADAY_PATH) -> ParquetSource:
    """1-minute SPY/QQQ bars (Databento, RTH, tz-naive ET). Requires the cache
    built by scripts/build_intraday_cache.py; the committed fixture path is for
    tests."""
    return ParquetSource(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/test_intraday_source.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/data/source.py tests/engine_v2/test_intraday_source.py
git commit -m "feat: intraday_source + run_simple frequency-aware on 1-min bars"
```

---

### Task 3: Run page — Daily / Intraday source selector

**Files:**
- Modify: `dashboard/views/run.py`
- Test: `tests/test_workbench_dashboard.py` (add cases)

**Interfaces:**
- Consumes: `data.source.default_source`, `data.source.intraday_source`, `INTRADAY_PATH`.
- Produces: a `key="data_source"` selector; missing-cache guard.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_workbench_dashboard.py
import os
from streamlit.testing.v1 import AppTest
from src.engine_v2.data.source import INTRADAY_PATH

def test_data_source_selector_exists_and_daily_default():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    assert at.selectbox(key="data_source").value.startswith("Daily")

@pytest.mark.skipif(not os.path.exists(INTRADAY_PATH),
                    reason="intraday cache not built on this machine")
def test_intraday_source_runs_in_dashboard():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.selectbox(key="data_source").set_value("Intraday 1-min (SPY/QQQ)").run(timeout=60)
    at.multiselect(key="tickers").set_value(["SPY"]).run(timeout=60)
    at.button(key="run_backtest").click().run(timeout=180)
    assert not at.exception
    assert len(at.metric) >= 1
```

Add `import pytest` at the top of the test file if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_workbench_dashboard.py -v`
Expected: FAIL — no `data_source` selectbox.

- [ ] **Step 3: Edit `dashboard/views/run.py`**

Update the imports:
```python
from src.engine_v2.data.source import default_source, intraday_source, INTRADAY_PATH
```
Replace the `src = default_source()` line at the top of `render()` with a selector + guard:
```python
    import os
    choice = st.selectbox("Data", ["Daily (20-ETF universe)",
                                    "Intraday 1-min (SPY/QQQ)"], key="data_source")
    if choice.startswith("Intraday"):
        if not os.path.exists(INTRADAY_PATH):
            st.warning("Intraday cache not built. Run:\n\n"
                       "`.venv/bin/python -m scripts.build_intraday_cache`\n\n"
                       "then reload.")
            st.stop()
        src = intraday_source()
    else:
        src = default_source()
```
Everything below (tickers/date/cost widgets, benchmark, `run_simple`, rendering) is unchanged — for intraday, `tickers_all` is `["SPY","QQQ"]`, `bench_tickers` resolves to `["SPY"]`, and the SPY benchmark shows (no TLT → no 60/40, which `run_simple` already guards).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_workbench_dashboard.py -v`
Expected: PASS. `test_intraday_source_runs_in_dashboard` runs if the cache was built in Task 1 (it was), else skips.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest tests/ -q`
Expected: all pass (intraday UI test passes locally with the built cache).
```bash
git add dashboard/views/run.py tests/test_workbench_dashboard.py
git commit -m "feat: Daily/Intraday data-source selector on Run page"
```

---

## Self-Review

**Spec coverage:**
- Build script (RTH filter, capitalize, tz-naive, MultiIndex, --source-dir, --include-extended) → Task 1 ✓
- Gitignored cache + committed 5-day fixture → Task 1 (Step 5/6) ✓
- `intraday_source()` reusing `ParquetSource` → Task 2 ✓
- Frequency-aware run on intraday (proof) → Task 2 (Step 1 test) ✓
- Run page Daily/Intraday selector + missing-cache guard → Task 3 ✓
- Non-goals (no live pull, no new strategy, SPY/QQQ only, no engine change) → respected; nothing implements them ✓

**Placeholder scan:** none. All code complete; artifact shapes given as expected ranges (real values pinned when the build runs in Task 1 Step 5).

**Type consistency:** `normalize_ticker`/`build`/constants (Task 1) match the test imports. `intraday_source`/`INTRADAY_PATH`/`INTRADAY_FIXTURE_PATH` (Task 2) match Task 3's imports and the dashboard test. Output MultiIndex `(ticker, "Close")` matches what `run_simple`/`_simulate` read. `date_range()` returns tz-naive Timestamps (from the tz-naive cache index), consistent with the load-by-timestamp tests.
