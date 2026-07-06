# Dashboard Honesty & Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard colors agree with the multiple-testing warning, add a SPY buy-hold benchmark to every performance view, and fix leaderboard/compare/plateau readability.

**Architecture:** Pure presentation-layer round. One additive extraction in `src/batch/runner.py` (`luck_sharpe`); everything else in `dashboard/`. Benchmark computed dashboard-side from the frozen playground parquet (temporary, dies when engine grows benchmark rows).

**Tech Stack:** Python, Streamlit (st.navigation API), pandas Styler, plotly express, pytest + streamlit AppTest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-dashboard-honesty-design.md`
- No engine/loader/recompute behavior changes (sole exception: additive `luck_sharpe` extraction, `luck_warning` output string must stay byte-identical).
- Luck banner text/placement unchanged.
- Color never the only signal — every shaded cell still shows its number.
- Run all commands from repo root `~/Documents/Trading code/etf-bot` with `.venv/bin/python -m pytest`.
- Benchmark constants: `START_CASH = 100_000`, `LABEL = "benchmark_spy"`, `FRIENDLY = "S&P 500 buy & hold — benchmark"`.

---

### Task 1: Extract `luck_sharpe` in src/batch/runner.py

**Files:**
- Modify: `src/batch/runner.py:54-58`
- Test: `tests/test_batch.py` (append)

**Interfaces:**
- Produces: `luck_sharpe(n_runs: int, years: float) -> float` — later tasks import it via `from src.batch.runner import luck_sharpe`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_batch.py`:

```python
def test_luck_sharpe_extraction():
    from src.batch.runner import luck_sharpe, luck_warning
    import math
    # formula: sqrt(2*ln(max(n,2))/years)
    assert luck_sharpe(31, 11.0) == pytest.approx(
        math.sqrt(2 * math.log(31) / 11.0))
    assert luck_sharpe(1, 10.0) == luck_sharpe(2, 10.0)  # n clamped to 2
    # warning string still embeds the same number, unchanged format
    assert f"~{luck_sharpe(31, 11.0):.2f}" in luck_warning(31, 11.0)
```

(`tests/test_batch.py` already imports pytest; if not, add `import pytest`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_batch.py::test_luck_sharpe_extraction -v`
Expected: FAIL — `ImportError: cannot import name 'luck_sharpe'`

- [ ] **Step 3: Implement** — in `src/batch/runner.py` replace `luck_warning`:

```python
def luck_sharpe(n_runs: int, years: float) -> float:
    """Expected best Sharpe from pure luck across n_runs independent tries."""
    return math.sqrt(2 * math.log(max(n_runs, 2)) / years)


def luck_warning(n_runs: int, years: float) -> str:
    exp_max = luck_sharpe(n_runs, years)
    return (f"MULTIPLE-TESTING WARNING: {n_runs} runs on {years:.0f}y of data -> "
            f"best-by-pure-luck Sharpe ~{exp_max:.2f} — results below that "
            f"line are indistinguishable from noise.")
```

- [ ] **Step 4: Run full batch tests**

Run: `.venv/bin/python -m pytest tests/test_batch.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/batch/runner.py tests/test_batch.py
git commit -m "refactor: extract luck_sharpe from luck_warning (additive, no behavior change)"
```

---

### Task 2: style.py — absolute Sharpe shader + shade_family

**Files:**
- Modify: `dashboard/style.py`
- Test: Create `tests/test_style.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `style_metrics(df, luck_sharpe: float | None = None, highlight_label: str | None = None)` — Styler. `luck_sharpe=None` keeps today's relative sharpe shading; given → absolute anchors. `highlight_label` tints the row whose `label` column equals it (`#F1F5F9`), applied before column shades so shaded cells win.
  - `shade_family(color: str, i: int, n: int) -> str` — i-th of n distinct same-family shades; `n<=1` returns color unchanged.

- [ ] **Step 1: Write failing tests** — create `tests/test_style.py`:

```python
"""Color honesty: absolute sharpe anchors + within-family shade separation."""
import pandas as pd

from dashboard.style import shade_family, style_metrics

LUCK = 0.79


def _css_for(df, col, row=0, **kw):
    """All CSS applied to one cell, as a single 'prop: value; ...' string."""
    ctx = style_metrics(df, **kw)._compute().ctx
    cell = ctx.get((row, df.columns.get_loc(col)), [])
    return "; ".join(f"{p}: {v}" for p, v in cell)


def test_below_luck_gets_no_green():
    df = pd.DataFrame({"sharpe": [0.68, 0.30]})
    css = _css_for(df, "sharpe", row=0, luck_sharpe=LUCK)
    assert "22, 163, 74" not in css          # no green
    assert "100, 116, 139" in css            # gray = noise


def test_above_luck_gets_green_scaled():
    df = pd.DataFrame({"sharpe": [0.9, 1.5]})
    css_ok = _css_for(df, "sharpe", row=0, luck_sharpe=LUCK)
    css_top = _css_for(df, "sharpe", row=1, luck_sharpe=LUCK)
    assert "22, 163, 74" in css_ok and "22, 163, 74" in css_top


def test_none_luck_falls_back_to_relative():
    df = pd.DataFrame({"sharpe": [0.1, 0.2]})
    assert "22, 163, 74" in _css_for(df, "sharpe", row=1)  # old behavior


def test_highlight_label_tints_row():
    df = pd.DataFrame({"sharpe": [0.5], "label": ["benchmark_spy"]})
    css = _css_for(df, "label", row=0, highlight_label="benchmark_spy")
    assert "#F1F5F9" in css


def test_shade_family_distinct_and_identity():
    base = "#EA580C"
    assert shade_family(base, 0, 1) == base
    shades = [shade_family(base, i, 3) for i in range(3)]
    assert len(set(shades)) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_style.py -v`
Expected: FAIL — `ImportError: cannot import name 'shade_family'`

- [ ] **Step 3: Implement** — in `dashboard/style.py` add after `family_color`:

```python
def shade_family(color: str, i: int, n: int) -> str:
    """i-th of n same-family shades: ±12% brightness steps around the base."""
    if n <= 1:
        return color
    r, g, b = (int(color[j:j + 2], 16) for j in (1, 3, 5))
    f = 1 + 0.12 * (i - (n - 1) / 2)
    def mix(c):
        return max(0, min(255, round(c * f)))
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _sharpe_shade_absolute(luck: float):
    """Below luck line: gray (noise). Luck→1.0: pale→mid green. >1: strong."""
    def apply(col):
        out = []
        for v in col.astype(float):
            if v != v:
                out.append("")
            elif v < luck:
                d = min((luck - v) / max(luck, 1e-9), 1.0)
                out.append(f"background-color: rgba(100, 116, 139, {0.06 + 0.18 * d:.2f})")
            else:
                frac = 1.0 if luck >= 1 else min((v - luck) / (1.0 - luck), 1.0)
                out.append(f"background-color: rgba(22, 163, 74, {0.15 + 0.35 * frac:.2f})")
        return out
    return apply
```

Replace `style_metrics` with:

```python
def style_metrics(df, luck_sharpe=None, highlight_label=None):
    """Styler: honest sharpe shading (absolute vs luck line when given),
    relative green for cagr, red for drawdowns, optional row tint."""
    fmt = {c: f for c, f in FORMATS.items() if c in df.columns}
    styler = df.style.format(fmt, na_rep="—")
    if highlight_label is not None and "label" in df.columns:
        def tint(row):
            hit = row["label"] == highlight_label
            return ["background-color: #F1F5F9" if hit else ""] * len(row)
        styler = styler.apply(tint, axis=1)   # first, so column shades win
    if "sharpe" in df.columns:
        shader = (_sharpe_shade_absolute(luck_sharpe) if luck_sharpe is not None
                  else _shade("22, 163, 74"))
        styler = styler.apply(shader, subset=["sharpe"])
    if "cagr" in df.columns:
        styler = styler.apply(_shade("22, 163, 74"), subset=["cagr"])
    if "max_dd" in df.columns:
        styler = styler.apply(_shade("220, 38, 38", invert=True), subset=["max_dd"])
    return styler
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_style.py tests/test_dashboard_render.py -v`
Expected: ALL PASS (render smoke tests confirm Styler still integrates)

- [ ] **Step 5: Commit**

```bash
git add dashboard/style.py tests/test_style.py
git commit -m "feat: absolute luck-anchored sharpe shading + within-family shades"
```

---

### Task 3: benchmark.py + shared caching + luck_threshold

**Files:**
- Create: `dashboard/benchmark.py`
- Modify: `dashboard/shared.py`
- Test: Create `tests/test_benchmark.py`

**Interfaces:**
- Consumes: `src.engine.metrics.summarize(result)` (needs `.equity`, `.trades`, `.holdings_value`), playground long df (`date/open/high/low/close/volume/ticker`).
- Produces:
  - `benchmark.spy_equity(playground: pd.DataFrame) -> pd.Series | None` — SPY close indexed by date, normalized to start at 100_000; None if no SPY rows.
  - `benchmark.benchmark_row(equity: pd.Series) -> dict` — summarize fields + `label="benchmark_spy"`, `name="benchmark"`, `sample_flag="—"`, `turnover=0.0`, `exposure=1.0`.
  - `benchmark.LABEL`, `benchmark.FRIENDLY` constants.
  - `shared.spy_benchmark() -> tuple[pd.Series | None, dict | None]` (cached).
  - `shared.luck_threshold() -> float` — `luck_sharpe(len(current_lb()), years-of-playground)`; same n/years the banner uses.

- [ ] **Step 1: Write failing tests** — create `tests/test_benchmark.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.benchmark'`

- [ ] **Step 3: Implement** — create `dashboard/benchmark.py`:

```python
"""SPY buy-and-hold benchmark computed from the frozen playground parquet.

TEMPORARY dashboard-side duplication (owner-approved): dies when the engine
grows real benchmark rows (already in the iteration queue).
"""
from types import SimpleNamespace

import pandas as pd

from src.engine.metrics import summarize

START_CASH = 100_000
LABEL = "benchmark_spy"
FRIENDLY = "S&P 500 buy & hold — benchmark"


def spy_equity(playground: pd.DataFrame) -> pd.Series | None:
    spy = playground[playground["ticker"] == "SPY"]
    if spy.empty:
        return None
    close = spy.set_index("date")["close"].sort_index()
    return close / close.iloc[0] * START_CASH


def benchmark_row(equity: pd.Series) -> dict:
    result = SimpleNamespace(
        equity=equity,
        trades=pd.DataFrame(columns=["shares", "price"]),
        holdings_value=equity,  # buy-hold: always fully invested
    )
    row = summarize(result)
    row.update(label=LABEL, name="benchmark", sample_flag="—",
               turnover=0.0, exposure=1.0)
    return row
```

Append to `dashboard/shared.py`:

```python
from dashboard import benchmark
from src.batch.runner import luck_sharpe


@st.cache_data
def spy_benchmark():
    """(equity, leaderboard-row dict) for SPY buy-hold; (None, None) if absent."""
    eq = benchmark.spy_equity(playground())
    if eq is None:
        return None, None
    return eq, benchmark.benchmark_row(eq)


def luck_threshold() -> float:
    """Same n_runs/years the leaderboard banner uses."""
    dates = playground()["date"]
    years = (dates.max() - dates.min()).days / 365.25
    return luck_sharpe(len(current_lb()), years)
```

(Place the two imports with the existing imports at the top of `shared.py`.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/benchmark.py dashboard/shared.py tests/test_benchmark.py
git commit -m "feat: SPY buy-hold benchmark module + shared luck_threshold"
```

---

### Task 4: Leaderboard upgrade

**Files:**
- Modify: `dashboard/views/leaderboard.py`
- Modify: `dashboard/app.py`
- Test: `tests/test_dashboard_render.py` (existing smoke tests must stay green)

**Interfaces:**
- Consumes: `style.style_metrics(df, luck_sharpe, highlight_label)`, `shared.spy_benchmark()`, `shared.luck_threshold()`, `benchmark.LABEL/FRIENDLY` (Tasks 2–3).
- Produces: `st.session_state["pages"]` dict set by `app.py` (`"leaderboard"/"run"/"plateau"/"compare"` → `st.Page`) — compare/run views may rely on it existing under the full app.

- [ ] **Step 1: app.py — stash pages dict.** Replace the `pg = st.navigation(...)` block:

```python
pages = {
    "leaderboard": st.Page(leaderboard.render, title="Leaderboard",
                           url_path="leaderboard", default=True),
    "run": st.Page(run_detail.render, title="Run detail", url_path="run"),
    "plateau": st.Page(plateau.render, title="Plateau", url_path="plateau"),
    "compare": st.Page(compare.render, title="Compare", url_path="compare"),
}
st.session_state["pages"] = pages
pg = st.navigation(list(pages.values()))
pg.run()
```

- [ ] **Step 2: leaderboard.py — rewrite render.** Replace `COLUMN_HELP`/`METRIC_ORDER` with one dict (label + help), merge years, add benchmark row, height, page_link:

```python
import pandas as pd
import streamlit as st

from dashboard import benchmark, loader, naming, recompute, shared, style
from src.batch.runner import luck_warning

# raw column -> (display label, hover help)
COLUMNS = {
    "strategy": ("Strategy", "Strategy + settings in plain words (same run as the technical label, just readable)."),
    "sharpe": ("Sharpe", "Return per unit of risk. Above 1 good, above 2 excellent. Below the luck line: meaningless."),
    "cagr": ("CAGR", "Compound annual growth rate — average yearly return."),
    "max_dd": ("Max DD", "Max drawdown — worst peak-to-trough loss along the way. Closer to 0 is better."),
    "n_trades": ("Trades", "Trades in the backtest. Under 30 = statistically worthless; 100+ preferred."),
    "sample_flag": ("Sample", "OK = enough trades to take the stats seriously."),
    "years_up": ("Years up", "Calendar years that ended with a gain, out of years tested."),
    "turnover": ("Turnover/yr", "How much of the portfolio is replaced per year (1.0 = fully replaced once)."),
    "exposure": ("Exposure", "Share of time the money was invested rather than sitting in cash."),
    "best_year": ("Best yr", "Single best calendar-year return."),
    "worst_year": ("Worst yr", "Single worst calendar-year return."),
    "top2_share": ("Top-2 share", "How much of total profit came from just the 2 best periods — high = fragile."),
    "label": ("(technical)", "Technical label (used internally to identify the run)."),
}

METRIC_ORDER = ["strategy", "sharpe", "cagr", "max_dd", "n_trades",
                "sample_flag", "years_up", "turnover", "exposure",
                "best_year", "worst_year", "top2_share"]


def _years_up(df):
    return (df["positive_years"].astype(int).astype(str) + " of "
            + df["total_years"].astype(int).astype(str))


def render():
    st.title("Strategy leaderboard")
    st.caption("Every backtest in this batch, ranked by risk-adjusted return.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Each row is one strategy with one specific setting, tested on 2010–2020.\n"
            "- **Click any column header to sort.** Hover a column name for what it means.\n"
            "- The orange banner is the **luck line**: with this many attempts, the best "
            "random junk would score about that Sharpe. Anything below it proves nothing.\n"
            "- Colors: **green only above the luck line** — gray Sharpe cells are "
            "statistically indistinguishable from noise, whatever the number says.\n"
            "- The tinted top row is the do-nothing benchmark: buy SPY and hold.\n"
            "- Click a row, then use the link that appears to open its charts."
        )

    lb = shared.current_lb()
    ok, bad = loader.split_errors(lb)

    dates = shared.playground()["date"]
    years = (dates.max() - dates.min()).days / 365.25
    st.warning(luck_warning(len(lb), years))
    luck = shared.luck_threshold()

    fmap = naming.friendly_map(ok)
    view = ok.copy()
    view.insert(0, "strategy", view["label"].map(fmap))
    view["years_up"] = _years_up(view)

    spy_eq, spy_row = shared.spy_benchmark()
    if spy_row is not None:
        bench = dict(spy_row)
        bench["strategy"] = benchmark.FRIENDLY
        bench["years_up"] = (f"{int(bench['positive_years'])} of "
                             f"{int(bench['total_years'])}")
        view = pd.concat([pd.DataFrame([bench]), view], ignore_index=True)
    else:
        st.info("SPY not in dataset — benchmark hidden")

    cols = [c for c in METRIC_ORDER if c in view.columns] + ["label"]
    display = view[cols]

    event = st.dataframe(
        style.style_metrics(display, luck_sharpe=luck,
                            highlight_label=benchmark.LABEL),
        hide_index=True,
        on_select="rerun", selection_mode="single-row",
        width="stretch",
        height=min(35 * (len(display) + 1) + 3, 1200),
        column_config={c: st.column_config.Column(label=lab, help=h)
                       for c, (lab, h) in COLUMNS.items()
                       if c in display.columns})
    if event.selection.rows:
        label = display.iloc[event.selection.rows[0]]["label"]
        if label != benchmark.LABEL:
            st.session_state["selected_label"] = label
            pages = st.session_state.get("pages")
            if pages:
                st.page_link(pages["run"],
                             label=f"Open run detail → {fmap[label]}")
            else:
                st.caption(f"Selected **{fmap[label]}** — open *Run detail* "
                           "in the sidebar.")

    st.subheader("What these strategies do")
    for name in ok["name"].unique():
        cls = recompute.STRATEGIES.get(name)
        title = getattr(cls, "display_name", "") or name
        dot = style.family_color(name)
        with st.expander(title):
            st.markdown(
                f'<span style="color:{dot}">●</span> shown in this color on all charts',
                unsafe_allow_html=True)
            st.markdown(cls.description if cls else
                        "_plugin not found in src/strategies/_")

    if not bad.empty:
        st.subheader(":red[Crashed runs]")
        st.dataframe(bad[["label", "name", "error"]], hide_index=True,
                     width="stretch")
```

Notes for the implementer:
- Benchmark row is excluded from run-detail linking (it has no strategy plugin) — hence the `label != benchmark.LABEL` guard.
- `pages` is absent when a view renders outside `app.py` (AppTest `from_function`) — the caption fallback keeps smoke tests green.
- `positive_years`/`total_years` are dropped from display implicitly (not in `METRIC_ORDER`).

- [ ] **Step 3: Run render smoke tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_render.py -v`
Expected: ALL PASS (both from_file and per-view from_function tests)

- [ ] **Step 4: Visual check**

Run: `.venv/bin/python -m streamlit run dashboard/app.py` (or confirm against the already-running instance after restart). Verify: benchmark row tinted on top, all rows visible without inner scroll, friendly headers, gray sharpe cells (all below luck line), row click shows "Open run detail →" link that navigates and preselects the run.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/views/leaderboard.py
git commit -m "feat: honest leaderboard — luck-anchored colors, SPY benchmark row, friendly headers, full height, run-detail link"
```

---

### Task 5: Run detail + Compare — benchmark overlays, family shades, picks caption

**Files:**
- Modify: `dashboard/views/run_detail.py`
- Modify: `dashboard/views/compare.py`
- Test: `tests/test_dashboard_render.py` (stay green)

**Interfaces:**
- Consumes: `shared.spy_benchmark()`, `shared.luck_threshold()`, `style.shade_family`, `style.MUTED` (Tasks 2–3).

- [ ] **Step 1: run_detail.py — SPY overlay + axis labels.** Replace the equity/drawdown chart block (lines with `fig = px.line(eq, ...)` through the drawdown `st.plotly_chart`):

```python
    eq = result.equity
    fig = px.line(eq, title="Equity — growth of $100k (log scale)", log_y=True)
    fig.update_traces(line_color=color, showlegend=False)
    spy_eq, _ = shared.spy_benchmark()
    if spy_eq is not None:
        fig.add_scatter(x=spy_eq.index, y=spy_eq.values, mode="lines",
                        name="S&P 500 buy & hold",
                        line=dict(color=style.MUTED, width=1.5))
    fig.update_yaxes(title="$")
    st.plotly_chart(style.apply_plotly_defaults(fig))

    dd = eq / eq.cummax() - 1
    fig = px.area(dd, title="Drawdown — % below record high")
    fig.update_traces(line_color=style.NEG, fillcolor="rgba(220,38,38,0.25)",
                      showlegend=False)
    fig.update_yaxes(tickformat=".0%", title="% below peak")
    st.plotly_chart(style.apply_plotly_defaults(fig))
```

- [ ] **Step 2: compare.py — full rewrite of render body** (imports gain `benchmark`):

```python
import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import benchmark, loader, naming, recompute, shared, style

METRICS = ["sharpe", "cagr", "max_dd", "n_trades", "turnover", "exposure",
           "positive_years", "total_years", "sample_flag"]

DASHES = ["solid", "dash", "dot", "dashdot"]

BENCH_NAME = "S&P 500 (benchmark)"


def render():
    st.title("Compare runs")
    st.caption("Overlay a few runs to see which behaved best, and when.")
    with st.expander("How to read this page"):
        st.markdown(
            "- Every line starts at $1 — whoever ends highest grew the most.\n"
            "- Line **color = strategy family**; same-family runs get "
            "lighter/darker shades of the family color plus different dashes.\n"
            "- The gray line is the do-nothing benchmark: buy SPY and hold.\n"
            "- Watch the rough patches, not just the finish: a line that "
            "dives 30% before recovering was a much scarier ride.\n"
            "- The table below shades the same metrics as the leaderboard."
        )
    ok, _ = loader.split_errors(shared.current_lb())
    fmap = naming.friendly_map(ok)
    labels = st.multiselect("Pick 2–4 runs", ok["label"].tolist(),
                            max_selections=4, format_func=lambda l: fmap[l])
    if labels:
        st.caption("Picked: " + " · ".join(f"**{fmap[l]}**" for l in labels))
    if len(labels) < 2:
        st.info("Pick at least 2 runs.")
        return

    curves, families = {}, {}
    for label in labels:
        row = ok[ok["label"] == label].iloc[0].to_dict()
        try:
            result = shared.run_result(row)
        except recompute.UnknownStrategyError as e:
            st.error(f"{fmap[label]}: {e}")
            continue
        curves[fmap[label]] = result.equity / result.equity.iloc[0]
        families[fmap[label]] = row["name"]

    if len(curves) >= 2:
        df = pd.DataFrame(curves)
        spy_eq, _ = shared.spy_benchmark()
        if spy_eq is not None:
            df[BENCH_NAME] = (spy_eq / spy_eq.iloc[0]).reindex(df.index)

        by_family = {}
        for friendly, name in families.items():
            by_family.setdefault(name, []).append(friendly)
        color_map = {}
        for name, members in by_family.items():
            base = style.family_color(name)
            for i, friendly in enumerate(members):
                color_map[friendly] = style.shade_family(base, i, len(members))
        color_map[BENCH_NAME] = style.MUTED

        fig = px.line(df, log_y=True, title="Growth of $1 (log scale)",
                      color_discrete_map=color_map,
                      labels={"value": "growth of $1", "variable": "run"})
        for i, tr in enumerate(fig.data):
            tr.line.dash = ("dot" if tr.name == BENCH_NAME
                            else DASHES[i % len(DASHES)])
            if tr.name == BENCH_NAME:
                tr.line.width = 1.5
        st.plotly_chart(style.apply_plotly_defaults(fig))

    side = ok[ok["label"].isin(labels)].copy()
    side.insert(0, "strategy", side["label"].map(fmap))
    side = side.set_index("strategy")[[c for c in METRICS if c in side.columns]]
    st.dataframe(style.style_metrics(side, luck_sharpe=shared.luck_threshold()),
                 width="stretch")
```

- [ ] **Step 3: Run render smoke tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_render.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add dashboard/views/run_detail.py dashboard/views/compare.py
git commit -m "feat: SPY overlay on run detail + compare; family shade separation; picks caption"
```

---

### Task 6: Plateau honesty + 1-D branch, batch label, final polish

**Files:**
- Modify: `dashboard/views/plateau.py`
- Modify: `dashboard/naming.py`
- Modify: `dashboard/app.py` (selectbox format_func)
- Test: `tests/test_naming.py` (append)

**Interfaces:**
- Consumes: `shared.luck_threshold()` (Task 3), `style.POS/MUTED` constants.
- Produces: `naming.batch_label(filename: str) -> str`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_naming.py`:

```python
def test_batch_label_happy_path():
    from dashboard.naming import batch_label
    assert batch_label("leaderboard_20260705-224738_42e964f.csv") == \
        "Jul 5 2026, 22:47 · 42e964f"


def test_batch_label_garbage_passthrough():
    from dashboard.naming import batch_label
    assert batch_label("something_else.csv") == "something_else.csv"
    assert batch_label("") == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_naming.py -v`
Expected: 2 new FAIL — `ImportError: cannot import name 'batch_label'`

- [ ] **Step 3: Implement `batch_label`** — append to `dashboard/naming.py`:

```python
import re
from datetime import datetime

BATCH_RE = re.compile(r"leaderboard_(\d{8})-(\d{6})_([0-9a-f]+)\.csv$")


def batch_label(filename: str) -> str:
    """'leaderboard_20260705-224738_42e964f.csv' -> 'Jul 5 2026, 22:47 · 42e964f'.
    Anything unparseable passes through unchanged."""
    m = BATCH_RE.match(filename)
    if not m:
        return filename
    d, t, sha = m.groups()
    dt = datetime.strptime(d + t, "%Y%m%d%H%M%S")
    return f"{dt.strftime('%b')} {dt.day} {dt.year}, {dt:%H:%M} · {sha}"
```

(Move the `import re` / `from datetime import datetime` lines up with the existing imports.)

In `dashboard/app.py`, change the batch selectbox line:

```python
choice = st.sidebar.selectbox("Batch", names, format_func=naming.batch_label)
```

and add `naming` to the `from dashboard import ...` line.

- [ ] **Step 4: plateau.py — absolute scale + 1-D branch.** Replace everything from `table = plateau_table(...)` to the end of `render` with:

```python
    table = plateau_table(ok, name, param, metric)
    luck = shared.luck_threshold()

    if len(table.index) == 1:
        # one swept param -> a bar says more than a one-row heatmap
        vals = table.iloc[0]
        colors = ([style.POS if v >= luck else style.MUTED for v in vals]
                  if metric == "sharpe"
                  else [style.family_color(name)] * len(vals))
        fig = px.bar(x=[str(c) for c in table.columns], y=vals.values,
                     title=f"{metric} by {param}",
                     labels={"x": param, "y": metric})
        fig.update_traces(marker_color=colors)
        if metric == "sharpe":
            fig.add_hline(y=luck, line_dash="dot", line_color=style.NEG,
                          annotation_text=f"luck line ≈ {luck:.2f}")
        st.plotly_chart(style.apply_plotly_defaults(fig))
    else:
        ylab = " × ".join(str(n) for n in table.index.names if n != "_") or "(all)"
        kwargs = {}
        if metric == "sharpe":
            # absolute, luck-anchored scale: red below the line, green past it
            kwargs = dict(zmin=0.0,
                          zmax=max(1.0, float(table.max().max())),
                          color_continuous_midpoint=luck)
        fig = px.imshow(table, text_auto=".2f", aspect="auto",
                        color_continuous_scale="RdYlGn",
                        labels={"x": param, "y": ylab, "color": metric},
                        **kwargs)
        if metric == "sharpe":
            fig.update_coloraxes(colorbar=dict(
                tickvals=[0.0, luck, max(1.0, float(table.max().max()))],
                ticktext=["0", f"luck {luck:.2f}", "1.0+"]))
        st.plotly_chart(style.apply_plotly_defaults(fig))

    st.dataframe(table.style.format("{:.2f}", na_rep="—"),
                 width="stretch")
```

Also update the page's explainer expander bullet list — replace the second bullet with:

```python
            "- For Sharpe the color scale is **absolute**: red/yellow below "
            "the luck line (noise), green only past it. A page of red is an "
            "honest answer.\n"
```

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: ALL PASS (61 existing + ~12 new)

- [ ] **Step 6: Visual pass** — restart/reload the app, walk all four pages against the real batch CSV. Verify: plateau now mostly red/yellow with luck tick on colorbar; single-param families render bars with luck line; batch selector reads "Jul 5 2026, 22:47 · 42e964f".

- [ ] **Step 7: Commit**

```bash
git add dashboard/views/plateau.py dashboard/naming.py dashboard/app.py tests/test_naming.py
git commit -m "feat: luck-anchored plateau scale + 1-D bars, friendly batch labels"
```
