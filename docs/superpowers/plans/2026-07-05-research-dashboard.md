# ETF Bot Research Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local, read-only Streamlit dashboard over screening-batch leaderboard CSVs, with on-demand recompute of equity curves (spec: `docs/superpowers/specs/2026-07-05-dashboard-design.md`).

**Architecture:** `dashboard/loader.py` finds/reads leaderboard CSVs; `dashboard/recompute.py` rebuilds a strategy object from a leaderboard row and re-runs `run_backtest` (pure, no Streamlit); `dashboard/shared.py` adds Streamlit caching; four small view files render Leaderboard, Run detail, Plateau, Compare. Zero engine changes except an additive `description` attribute on strategies.

**Tech Stack:** Python, pandas, Streamlit (multi-page via `st.navigation`), Plotly, pytest.

## Global Constraints

- Run everything from the repo root: `~/Documents/Trading code/etf-bot`.
- Use the repo venv explicitly: `.venv/bin/python -m pytest ...`, `.venv/bin/python -m streamlit run dashboard/app.py`.
- Playground data ONLY — never call `load_exam`, never pass any exam flag (spec: exam seal untouched).
- No engine modifications other than the additive `description` class attribute (Task 1).
- Read-only: dashboard never writes to `results/`, `data/`, or the vault.
- Stale-check tolerance: `rel_tol=1e-6` (spec).
- Full test suite must stay green after every task: `.venv/bin/python -m pytest -q`.

---

### Task 1: Mandatory strategy descriptions

**Files:**
- Modify: `src/strategies/base.py` (add `description = ""` class attribute)
- Modify: `src/strategies/ts_trend.py` (add description text)
- Modify: `src/strategies/momentum_rotation.py` (add description text)
- Test: `tests/test_descriptions.py` (create)

**Interfaces:**
- Consumes: existing `Strategy` base class, `TSTrend`, `MomentumRotation`.
- Produces: `Strategy.description: str` — plain-language, three labeled parts (`What it does`, `Why it should work`, `When it fails`). Views read `STRATEGIES[name].description` later.

- [ ] **Step 1: Write the failing test**

Create `tests/test_descriptions.py`:

```python
"""Every concrete strategy must explain itself in plain language (spec:
'Strategy descriptions' — mandatory, not optional polish)."""
from src.strategies.base import Strategy
from src.strategies.momentum_rotation import MomentumRotation  # noqa: F401
from src.strategies.ts_trend import TSTrend  # noqa: F401

REQUIRED_PARTS = ("What it does", "Why it should work", "When it fails")


def test_every_concrete_strategy_has_full_description():
    concrete = Strategy.__subclasses__()
    assert concrete, "no strategies discovered"
    for cls in concrete:
        desc = getattr(cls, "description", "")
        assert len(desc) > 100, f"{cls.name}: description missing or too thin"
        for part in REQUIRED_PARTS:
            assert part in desc, f"{cls.name}: description lacks '{part}' section"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_descriptions.py -v`
Expected: FAIL with "description missing or too thin"

- [ ] **Step 3: Implement**

In `src/strategies/base.py`, add one line inside `class Strategy` directly under `DEFAULTS: dict = {}`:

```python
    description = ""  # plain language: What it does / Why it should work / When it fails
```

In `src/strategies/ts_trend.py`, add inside `class TSTrend` under `DEFAULTS`:

```python
    description = """\
**What it does:** Once a month, checks each ETF in the basket: is today's
price higher than it was `lookback` trading days ago? Every ETF that passes
gets an equal slice (1/20th) of the account. Slots that fail stay in cash,
so market exposure shrinks automatically in bear markets.

**Why it should work:** Trends persist. Assets that have been rising keep
rising slightly more often than chance — classic explanations are investor
under-reaction to news and herding. Holding only what is already rising
sidesteps the worst of long bear legs.

**When it fails:** Choppy, sideways markets — the strategy buys strength
that immediately fades, over and over (whipsaw). Sharp V-shaped crashes and
rebounds (March 2020): it exits near the bottom and re-enters only after
much of the recovery is gone.
"""
```

In `src/strategies/momentum_rotation.py`, add inside `class MomentumRotation` under `DEFAULTS`:

```python
    description = """\
**What it does:** Once a month, ranks every ETF by trend quality — the slope
of its recent price path times how smooth that path is (regression R²). Buys
the `top_n` best scorers above `min_score`, giving smaller weights to the
more volatile ones. If nothing scores well, holds cash.

**Why it should work:** Cross-sectional momentum — recent winners keep
winning over 1–12 month horizons — is one of the most documented effects in
markets. The smoothness filter prefers steady climbers over one-headline
spikes, and inverse-volatility sizing keeps any single holding from
dominating risk.

**When it fails:** Momentum crashes — violent reversals after panics, where
beaten-down losers rocket and past winners lag. Holding only 2–3 ETFs means
one bad holding hurts; monthly rebalancing reacts slowly to fast turns.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_descriptions.py -v`
Expected: PASS

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add src/strategies/base.py src/strategies/ts_trend.py src/strategies/momentum_rotation.py tests/test_descriptions.py
git commit -m "feat: mandatory plain-language strategy descriptions"
```

---

### Task 2: Leaderboard loader

**Files:**
- Create: `dashboard/__init__.py` (empty)
- Create: `dashboard/loader.py`
- Test: `tests/test_dashboard_loader.py` (create)

**Interfaces:**
- Consumes: leaderboard CSVs written by `scripts/screen.py` (pattern `results/leaderboard_*.csv`; columns `label,name,<param cols>,cagr,max_dd,sharpe,n_trades,turnover,exposure,positive_years,total_years,sample_flag,error`).
- Produces:
  - `list_leaderboards(results_dir: str | Path = "results") -> list[Path]` — newest first, `[]` if none/missing dir.
  - `load_leaderboard(path) -> pd.DataFrame` — CSV as DataFrame, `error` column NaN→`""`.
  - `split_errors(lb: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]` — `(ok_rows, error_rows)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_loader.py`:

```python
import pandas as pd

from dashboard import loader

CSV = """label,name,lookback,cagr,max_dd,sharpe,n_trades,turnover,exposure,positive_years,total_years,sample_flag,error
ts_trend(lookback=63),ts_trend,63,0.08,-0.15,0.9,120,1.5,0.7,8,10,OK,
ts_trend(lookback=125),ts_trend,125,,,,,,,,,,"ValueError: boom"
"""


def _write(tmp_path, name="leaderboard_20260705-120000_abc1234.csv"):
    p = tmp_path / name
    p.write_text(CSV)
    return p


def test_list_leaderboards_empty_and_missing(tmp_path):
    assert loader.list_leaderboards(tmp_path) == []
    assert loader.list_leaderboards(tmp_path / "nope") == []


def test_list_leaderboards_newest_first(tmp_path):
    old = _write(tmp_path, "leaderboard_20260701-000000_aaa.csv")
    new = _write(tmp_path, "leaderboard_20260705-000000_bbb.csv")
    assert loader.list_leaderboards(tmp_path) == [new, old]


def test_load_and_split(tmp_path):
    lb = loader.load_leaderboard(_write(tmp_path))
    assert list(lb["error"]) == ["", "ValueError: boom"]  # NaN became ""
    ok, bad = loader.split_errors(lb)
    assert len(ok) == 1 and ok.iloc[0]["label"] == "ts_trend(lookback=63)"
    assert len(bad) == 1 and "boom" in bad.iloc[0]["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_loader.py -v`
Expected: FAIL with "No module named 'dashboard'"

- [ ] **Step 3: Implement**

Create empty `dashboard/__init__.py`, then `dashboard/loader.py`:

```python
"""Find and read leaderboard CSVs written by scripts/screen.py."""
from pathlib import Path

import pandas as pd


def list_leaderboards(results_dir="results") -> list[Path]:
    """All leaderboard CSVs, newest first (filenames embed the timestamp)."""
    return sorted(Path(results_dir).glob("leaderboard_*.csv"), reverse=True)


def load_leaderboard(path) -> pd.DataFrame:
    lb = pd.read_csv(path)
    lb["error"] = lb["error"].fillna("")
    return lb


def split_errors(lb: pd.DataFrame):
    """(ok_rows, error_rows) — crashed runs are shown, never dropped."""
    ok = lb[lb["error"] == ""].reset_index(drop=True)
    bad = lb[lb["error"] != ""].reset_index(drop=True)
    return ok, bad
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_loader.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/__init__.py dashboard/loader.py tests/test_dashboard_loader.py
git commit -m "feat: dashboard leaderboard loader"
```

---

### Task 3: Recompute-on-demand with stale guard

**Files:**
- Create: `dashboard/recompute.py`
- Test: `tests/test_dashboard_recompute.py` (create)

**Interfaces:**
- Consumes: `run_backtest(long_df, strategy) -> BacktestResult` (fields `equity: pd.Series`, `holdings_value: pd.Series`, `trades: pd.DataFrame`), `summarize(result) -> dict`, strategy classes with `name`, `DEFAULTS`, `label()`.
- Produces:
  - `STRATEGIES: dict[str, type]` — name → class registry.
  - `UnknownStrategyError(Exception)`.
  - `row_to_strategy(row: dict) -> Strategy` — rebuilds from leaderboard row; coerces CSV floats back to each param's `DEFAULTS` type; NaN/missing param → default; unknown `name` → `UnknownStrategyError`.
  - `recompute_run(row: dict, long_df: pd.DataFrame) -> BacktestResult`.
  - `check_stale(row: dict, result) -> list[str]` — empty list = fresh; else human-readable mismatch lines (`sharpe`, `cagr`, `max_dd`, rel_tol 1e-6).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_recompute.py`:

```python
"""Round-trip: leaderboard row -> strategy -> re-run -> identical metrics."""
import numpy as np
import pandas as pd
import pytest

from dashboard import recompute
from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.ts_trend import TSTrend


def synthetic_long_df(days=200, tickers=("AAA", "BBB"), seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2015-01-02", periods=days)
    frames = []
    for i, t in enumerate(tickers):
        close = pd.Series(
            100 * (1 + i) * np.cumprod(1 + rng.normal(0.0005, 0.01, days)),
            index=dates)
        frames.append(pd.DataFrame({
            "date": dates, "ticker": t,
            "open": close.shift(1).fillna(close.iloc[0]).values,
            "close": close.values}))
    return pd.concat(frames, ignore_index=True)


def make_row(strat, long_df):
    stats = summarize(run_backtest(long_df, strat))
    # CSV round-trip turns ints into floats — simulate that
    return {"label": strat.label(), "name": strat.name, "error": "",
            "lookback": float(strat.params["lookback"]), **stats}


def test_row_to_strategy_coerces_param_types():
    s = recompute.row_to_strategy({"name": "ts_trend", "lookback": 63.0})
    assert isinstance(s, TSTrend) and s.params["lookback"] == 63
    assert isinstance(s.params["lookback"], int)


def test_row_to_strategy_nan_param_uses_default():
    s = recompute.row_to_strategy({"name": "ts_trend", "lookback": float("nan")})
    assert s.params["lookback"] == TSTrend.DEFAULTS["lookback"]


def test_unknown_strategy_raises():
    with pytest.raises(recompute.UnknownStrategyError):
        recompute.row_to_strategy({"name": "deleted_plugin", "lookback": 63.0})


def test_round_trip_is_fresh_and_tampered_row_is_stale():
    long_df = synthetic_long_df()
    row = make_row(TSTrend(lookback=20), long_df)
    result = recompute.recompute_run(row, long_df)
    assert recompute.check_stale(row, result) == []
    tampered = {**row, "sharpe": row["sharpe"] + 0.1}
    problems = recompute.check_stale(tampered, result)
    assert problems and "sharpe" in problems[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_recompute.py -v`
Expected: FAIL with "cannot import name 'recompute'" (or ModuleNotFoundError)

- [ ] **Step 3: Implement**

Create `dashboard/recompute.py`:

```python
"""Rebuild a strategy from a leaderboard row and re-run its backtest.

Nothing here imports Streamlit — pure and unit-testable. Caching lives in
dashboard/shared.py. The stale check is an honesty guardrail: the dashboard
must never display numbers the CURRENT engine wouldn't produce.
"""
import math

from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.momentum_rotation import MomentumRotation
from src.strategies.ts_trend import TSTrend

STRATEGIES = {cls.name: cls for cls in (TSTrend, MomentumRotation)}
CHECK_METRICS = ("sharpe", "cagr", "max_dd")
RTOL = 1e-6  # spec: stale-leaderboard tolerance


class UnknownStrategyError(Exception):
    pass


def row_to_strategy(row: dict):
    cls = STRATEGIES.get(row["name"])
    if cls is None:
        raise UnknownStrategyError(
            f"strategy '{row['name']}' is not in src/strategies/ — "
            "renamed or deleted since this batch ran? Leaderboard row still "
            "counts; detail view is unavailable.")
    params = {}
    for key, default in cls.DEFAULTS.items():
        val = row.get(key, default)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            val = default
        params[key] = type(default)(val)  # CSV floats -> declared param type
    return cls(**params)


def recompute_run(row: dict, long_df):
    return run_backtest(long_df, row_to_strategy(row))


def check_stale(row: dict, result) -> list[str]:
    """[] = leaderboard matches current engine; else mismatch descriptions."""
    stats = summarize(result)
    problems = []
    for m in CHECK_METRICS:
        want, got = float(row[m]), float(stats[m])
        if not math.isclose(want, got, rel_tol=RTOL, abs_tol=1e-9):
            problems.append(
                f"{m}: leaderboard {want:.6f} vs recomputed {got:.6f}")
    return problems
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_recompute.py -v`
Expected: 4 PASS

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add dashboard/recompute.py tests/test_dashboard_recompute.py
git commit -m "feat: dashboard recompute-on-demand with stale-leaderboard guard"
```

---

### Task 4: App shell, shared caching, Leaderboard view

**Files:**
- Modify: `requirements.txt` (append two lines)
- Create: `dashboard/shared.py`
- Create: `dashboard/app.py`
- Create: `dashboard/views/__init__.py` (empty)
- Create: `dashboard/views/leaderboard.py`

**Interfaces:**
- Consumes: Task 2 `loader.*`, Task 3 `recompute.*`, engine `load_playground()`, `luck_warning(n_runs, years)` from `src.batch.runner`.
- Produces:
  - `shared.playground() -> pd.DataFrame` (cached resource).
  - `shared.leaderboard_df(path_str: str) -> pd.DataFrame` (cached).
  - `shared.run_result(row: dict) -> BacktestResult` (cached).
  - `shared.current_lb() -> pd.DataFrame` — leaderboard for the sidebar-selected batch (reads `st.session_state["lb_path"]`).
  - Each view module exposes `render() -> None`; leaderboard sets `st.session_state["selected_label"]` on row select.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
streamlit>=1.37
plotly>=5.20
```

Run: `.venv/bin/python -m pip install -r requirements.txt`
Expected: installs streamlit + plotly without errors.

- [ ] **Step 2: Create shared caching module**

Create empty `dashboard/views/__init__.py`, then `dashboard/shared.py`:

```python
"""Streamlit-side caching wrappers around the pure loader/recompute modules."""
import streamlit as st

from dashboard import loader, recompute
from src.engine.data import load_playground


@st.cache_resource
def playground():
    return load_playground()


@st.cache_data
def leaderboard_df(path_str: str):
    return loader.load_leaderboard(path_str)


@st.cache_data
def run_result(row: dict):
    """~1s first click per run, instant after (spec: recompute on demand)."""
    return recompute.recompute_run(row, playground())


def current_lb():
    return leaderboard_df(str(st.session_state["lb_path"]))
```

- [ ] **Step 3: Create the app shell**

Create `dashboard/app.py`:

```python
"""Entry point:  .venv/bin/python -m streamlit run dashboard/app.py
Run from the repo root (loader looks for ./results)."""
import streamlit as st

from dashboard import loader
from dashboard.views import compare, leaderboard, plateau, run_detail

st.set_page_config(page_title="ETF Bot Research", layout="wide")

paths = loader.list_leaderboards()
if not paths:
    st.title("No batches yet")
    st.info("Run a screening first:\n\n"
            "```\n.venv/bin/python -m scripts.screen\n```\n"
            "then reload this page.")
    st.stop()

names = [p.name for p in paths]
choice = st.sidebar.selectbox("Batch", names)  # newest first
st.session_state["lb_path"] = paths[names.index(choice)]

pg = st.navigation([
    st.Page(leaderboard.render, title="Leaderboard", url_path="leaderboard",
            default=True),
    st.Page(run_detail.render, title="Run detail", url_path="run"),
    st.Page(plateau.render, title="Plateau", url_path="plateau"),
    st.Page(compare.render, title="Compare", url_path="compare"),
])
pg.run()
```

Note: `run_detail`, `plateau`, `compare` don't exist yet. Create one-line stubs so the app starts (each replaced by Tasks 5–7):

`dashboard/views/run_detail.py`, `dashboard/views/plateau.py`, `dashboard/views/compare.py` each get:

```python
import streamlit as st


def render():
    st.info("Coming in a later task.")
```

- [ ] **Step 4: Create the Leaderboard view**

Create `dashboard/views/leaderboard.py` (replacing nothing — first real view):

```python
import streamlit as st

from dashboard import loader, recompute, shared
from src.batch.runner import luck_warning

METRIC_ORDER = ["label", "name", "sharpe", "cagr", "max_dd", "n_trades",
                "sample_flag", "positive_years", "total_years", "turnover",
                "exposure"]


def render():
    st.title("Strategy leaderboard")
    lb = shared.current_lb()
    ok, bad = loader.split_errors(lb)

    eq_index = shared.playground()["date"]
    years = (eq_index.max() - eq_index.min()).days / 365.25
    st.warning(luck_warning(len(lb), years))

    cols = [c for c in METRIC_ORDER if c in ok.columns]
    extras = [c for c in ok.columns if c not in cols + ["error"]]
    event = st.dataframe(
        ok[cols + extras], hide_index=True,
        on_select="rerun", selection_mode="single-row",
        use_container_width=True)
    if event.selection.rows:
        label = ok.iloc[event.selection.rows[0]]["label"]
        st.session_state["selected_label"] = label
        st.caption(f"Selected **{label}** — open *Run detail* in the sidebar.")

    st.subheader("What these strategies do")
    for name in ok["name"].unique():
        cls = recompute.STRATEGIES.get(name)
        with st.expander(name):
            st.markdown(cls.description if cls else
                        "_plugin not found in src/strategies/_")

    if len(bad):
        st.subheader(":red[Crashed runs]")
        st.dataframe(bad[["label", "name", "error"]], hide_index=True,
                     use_container_width=True)
```

- [ ] **Step 5: Verify by running the app**

Run: `cd ~/Documents/Trading\ code/etf-bot && .venv/bin/python -m streamlit run dashboard/app.py --server.headless true`

Check, in a browser at the printed localhost URL:
- If `results/` is empty: "No batches yet" screen with the screening command — then run `.venv/bin/python -m scripts.screen` and reload.
- With results: leaderboard table sorted/sortable, luck-warning banner visible, description expanders under the table, row click shows the "Selected …" caption, sidebar shows 4 pages + batch picker.

Stop the server with Ctrl-C.

- [ ] **Step 6: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q` — expected: all pass (views aren't imported by tests).

```bash
git add requirements.txt dashboard/shared.py dashboard/app.py dashboard/views/
git commit -m "feat: dashboard app shell with leaderboard view"
```

---

### Task 5: Run detail view

**Files:**
- Modify: `dashboard/views/run_detail.py` (replace the stub entirely)

**Interfaces:**
- Consumes: `shared.current_lb()`, `shared.run_result(row)`, `recompute.check_stale`, `recompute.UnknownStrategyError`, `recompute.STRATEGIES`, `summarize`, `yearly_returns` from `src.engine.metrics`.
- Produces: `render() -> None`; honors `st.session_state["selected_label"]` as the default selection.

- [ ] **Step 1: Implement (replace the whole stub file)**

`dashboard/views/run_detail.py`:

```python
import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared
from src.engine.metrics import summarize, yearly_returns


def render():
    st.title("Run detail")
    ok, _ = loader.split_errors(shared.current_lb())
    labels = ok["label"].tolist()
    if not labels:
        st.info("No successful runs in this batch.")
        return

    default = st.session_state.get("selected_label")
    idx = labels.index(default) if default in labels else 0
    label = st.selectbox("Run", labels, index=idx)
    row = ok[ok["label"] == label].iloc[0].to_dict()

    try:
        result = shared.run_result(row)
    except recompute.UnknownStrategyError as e:
        st.error(str(e))
        return

    problems = recompute.check_stale(row, result)
    if problems:
        st.error("**LEADERBOARD STALE** — engine changed since this batch "
                 "ran. Re-run: `.venv/bin/python -m scripts.screen`\n\n- "
                 + "\n- ".join(problems))

    stats = summarize(result)
    c = st.columns(6)
    c[0].metric("CAGR", f"{stats['cagr']:.1%}")
    c[1].metric("Sharpe", f"{stats['sharpe']:.2f}")
    c[2].metric("Max DD", f"{stats['max_dd']:.1%}")
    c[3].metric("Trades", f"{stats['n_trades']} ({stats['sample_flag']})")
    c[4].metric("Turnover/yr", f"{stats['turnover']:.1f}x")
    c[5].metric("Exposure", f"{stats['exposure']:.0%}")

    eq = result.equity
    st.plotly_chart(px.line(eq, title="Equity ($, log scale)", log_y=True),
                    use_container_width=True)
    dd = eq / eq.cummax() - 1
    st.plotly_chart(px.area(dd, title="Drawdown"), use_container_width=True)

    st.subheader("Year by year")
    yr = yearly_returns(eq)
    st.dataframe(yr.to_frame("return").style.format("{:.1%}"),
                 use_container_width=True)

    cls = recompute.STRATEGIES.get(row["name"])
    if cls:
        with st.expander("What this strategy does", expanded=True):
            st.markdown(cls.description)
```

- [ ] **Step 2: Verify by running the app**

Run: `.venv/bin/python -m streamlit run dashboard/app.py --server.headless true`

Check: pick a run → six metric tiles, equity + drawdown charts render, year table formatted as percents, description expander open. Select a row on Leaderboard first → Run detail defaults to it. First click ~1s, second instant (cache). Stale banner absent (engine unchanged since batch).

- [ ] **Step 3: Commit**

```bash
git add dashboard/views/run_detail.py
git commit -m "feat: run detail view — equity, drawdown, yearly table, description"
```

---

### Task 6: Plateau view

**Files:**
- Modify: `dashboard/views/plateau.py` (replace the stub entirely)

**Interfaces:**
- Consumes: `shared.current_lb()`, `plateau_table(leaderboard, name, param, metric)` from `src.batch.runner`, `recompute.STRATEGIES`.
- Produces: `render() -> None`.

- [ ] **Step 1: Implement (replace the whole stub file)**

`dashboard/views/plateau.py`:

```python
import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared
from src.batch.runner import plateau_table


def render():
    st.title("Parameter plateau")
    st.caption("Robust edges form PLATEAUS across neighboring parameters; "
               "isolated spikes are curve-fit artifacts.")
    ok, _ = loader.split_errors(shared.current_lb())
    if ok.empty:
        st.info("No successful runs in this batch.")
        return

    families = ok["name"].unique().tolist()
    name = st.selectbox("Strategy family", families)
    cls = recompute.STRATEGIES.get(name)
    params = list(cls.DEFAULTS) if cls else []
    swept = [p for p in params if p in ok.columns
             and ok.loc[ok["name"] == name, p].nunique() > 1]
    if not swept:
        st.info(f"'{name}' has no swept parameters in this batch — "
                "plateau needs a grid with ≥2 values.")
        return
    param = st.selectbox("Parameter", swept)
    metric = st.selectbox("Metric", ["sharpe", "cagr", "max_dd"])

    table = plateau_table(ok, name, param, metric)
    st.plotly_chart(
        px.imshow(table, text_auto=".2f", aspect="auto",
                  labels={"x": param, "y": " × ".join(map(str, table.index.names)),
                          "color": metric}),
        use_container_width=True)
    st.dataframe(table.style.format("{:.2f}"), use_container_width=True)
```

- [ ] **Step 2: Verify by running the app**

Run: `.venv/bin/python -m streamlit run dashboard/app.py --server.headless true`

Check: momentum_rotation family → heatmap with lookback/top_n/min_score axes, numbers overlaid; ts_trend → single-row heatmap over lookback. Switching metric redraws. `vol_window` (single value in grid) does NOT appear as a parameter choice.

- [ ] **Step 3: Commit**

```bash
git add dashboard/views/plateau.py
git commit -m "feat: plateau heatmap view"
```

---

### Task 7: Compare view

**Files:**
- Modify: `dashboard/views/compare.py` (replace the stub entirely)

**Interfaces:**
- Consumes: `shared.current_lb()`, `shared.run_result(row)`, `recompute.UnknownStrategyError`, `loader.split_errors`.
- Produces: `render() -> None`.

- [ ] **Step 1: Implement (replace the whole stub file)**

`dashboard/views/compare.py`:

```python
import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import loader, recompute, shared

METRICS = ["sharpe", "cagr", "max_dd", "n_trades", "turnover", "exposure",
           "positive_years", "total_years", "sample_flag"]


def render():
    st.title("Compare runs")
    ok, _ = loader.split_errors(shared.current_lb())
    labels = st.multiselect("Pick 2–4 runs", ok["label"].tolist(),
                            max_selections=4)
    if len(labels) < 2:
        st.info("Pick at least 2 runs.")
        return

    curves = {}
    for label in labels:
        row = ok[ok["label"] == label].iloc[0].to_dict()
        try:
            result = shared.run_result(row)
        except recompute.UnknownStrategyError as e:
            st.error(f"{label}: {e}")
            continue
        curves[label] = result.equity / result.equity.iloc[0]

    if len(curves) >= 2:
        df = pd.DataFrame(curves)
        st.plotly_chart(
            px.line(df, log_y=True,
                    title="Growth of $1 (log scale)",
                    labels={"value": "growth", "variable": "run"}),
            use_container_width=True)

    side = (ok[ok["label"].isin(labels)]
            .set_index("label")[METRICS].T)
    st.dataframe(side, use_container_width=True)
```

- [ ] **Step 2: Verify by running the app**

Run: `.venv/bin/python -m streamlit run dashboard/app.py --server.headless true`

Check: pick 3 runs → overlaid normalized curves on log scale with a legend, metrics table with one column per run. Picker caps at 4.

- [ ] **Step 3: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add dashboard/views/compare.py
git commit -m "feat: compare view — overlaid equity curves and side-by-side metrics"
```
