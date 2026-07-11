# Design: Clean Backtest Workbench (v2, long+short, data-agnostic)

- **Date:** 2026-07-10
- **Status:** design — awaiting review
- **Affects:** dashboard, `src/engine_v2`, repo layout
- **Supersedes intent of:** the Job-A honesty gate as the mandatory path (parked per
  `07 ETF Bot/Decisions/2026-07-10 — Refocus, build the bot, park the gate.md`)

## Problem

Backtesting a strategy today means editing a screening script, running a terminal
command, producing a CSV, and viewing it in a display-only dashboard — then reading a
DSR/FWER/CPCV **verdict** (PASS/WATCH/SHELF) that the 2026-07-10 audit showed is
under-costed and provisional. Two engines (v1 ~540 LOC, v2 ~1069 LOC) sit side by
side. The result is noisy and easy to get lost in. Owner wants: **pick a strategy +
universe + dates, click Run, see honest results. No gate, no ceremony.**

## What already works (verified 2026-07-10, build ON this)

Empirically confirmed against the current code, not the stale notes:

- `src/engine_v2/backtest/orchestrator.py::_simulate` / `position_history` run a real
  bar-by-bar sim: mark-to-market P&L, long **and** short, and **real costs that bite**
  — spread (end equity 158k→100k as spread 0→50bps/side on the fixture), per-day
  borrow fee on shorts, slippage on the traded delta only. The "costs are fiction"
  audit finding is **stale** — it described the pre-`fef74f2`/`f656301`/`de6541e`
  state.
- `_simulate` is **frequency-agnostic** — it walks `bars.index`, never assumes daily.
- Strategy plugin contract `src/engine_v2/strategy/protocol.py::StrategyV2`
  (`forecast(bars, asof) -> Series` + `display_name`/`mechanism`/`parameter_grid`/
  `holding_period_cap`), validated by `validate_plugin`. Plugins exist:
  `counter_trend.py`, `gap_pattern.py`.
- Carver sizing (`sizing/carver.py`), regime tagging (`data/regime.py::tag_regime`).
- `BacktestConfig` already exposes `spread_bps_per_side`, `spread_bps_by_ticker`,
  `borrow_bps_annual`, `borrow_bps_by_ticker`.

## What is NOT built (this project)

1. A **clean seam** that runs the sim over a straight date range and returns rich
   results — the public `run_backtest()` instead runs CPCV folds + `compute_verdict`
   and returns a verdict dict.
2. A **`Result` + metrics** object (CAGR, Sharpe, max DD, year-by-year, per-regime,
   benchmarks).
3. **Frequency-aware annualization** — `_instrument_sigma` and the metrics use a
   hardcoded `252`. Correct for daily, wrong for intraday.
4. A **`DataSource` seam** — the loader only reads one fixture parquet and raises
   `NotImplementedError` for any other source.
5. A **dashboard Run page** — the dashboard is display-only (reads CSVs written by
   `scripts/screen.py`).
6. A **strategy registry** so the UI dropdown auto-lists plugins.

## Explicitly out of scope (deferred / parked)

- **Real data pull.** yfinance is daily-only; most target strategies (swing/day) need
  **intraday**, which no free source provides. Deferred until an intraday source
  (ThetaData — owner has an account — or Schwab) is chosen. The workbench is built
  **data-agnostic** so that source drops in behind `DataSource` with zero workbench
  changes. Until then the existing fixture (SPY/TLT/GLD daily, 2007–2010) is the test
  bed.
- **The Job-A gate** — CPCV, DSR, FWER, NCO, `compute_verdict`, sealed-split checksum
  ceremony. Parked (moved to `archive/`, not deleted). Returns only if/when we go back
  to *searching over many strategies*. K_effective = 1 for implementing one known
  strategy, so there is nothing to multiple-test-correct.

## Architecture

Data-agnostic and frequency-aware are the two load-bearing decisions. Everything the
workbench does starts from a `bars` DataFrame of arbitrary frequency; nothing above the
`DataSource` knows or cares where the bars came from or how far apart they are.

### Components

**1. `DataSource` (new, `src/engine_v2/data/source.py`)**
- Interface: `load(tickers, start, end) -> bars` (MultiIndex columns `[ticker][OHLC]`,
  as the fixture and `_simulate` already expect) plus `available_tickers()` and
  `date_range()` for the UI to populate pickers dynamically.
- One implementation now: `FixtureSource`, wrapping the existing
  `data/loader.py::load_bars`. Future `ThetaDataSource` / `SchwabSource` implement the
  same three methods.

**2. `run_simple` seam (new, `src/engine_v2/backtest/simple.py`)**
- `run_simple(strategy_cls, bars, config=None, params=None, periods_per_year=None) -> Result`.
- Calls the existing `position_history(strategy_cls, params, bars, bars.index, cfg)` —
  one straight pass over the full range. **No CPCV, no folds, no gate, no
  `compute_verdict`.**
- Infers `periods_per_year` from median bar spacing when not given (≈252 for daily,
  ≈252*390 for 1-min), and threads it into sigma/metrics so intraday is correct later.
- Returns a `Result`.

**3. `Result` + metrics (new, `src/engine_v2/backtest/results.py`)**
- Holds the per-bar equity series, positions, and trades from `position_history`.
- Computes: total return, CAGR, annualized Sharpe, max drawdown, drawdown series,
  year-by-year returns table, per-regime breakdown (via `tag_regime`), trade count.
- Benchmarks on the **same** dates: SPY buy-hold and 60/40 (SPY/TLT) — computed
  directly from `bars`, guarded when a benchmark ticker is absent from the universe.
- No pass/fail. Diagnostics only.

**4. Strategy registry (new, `src/engine_v2/strategy/registry.py`)**
- A dict/list mapping `display_name -> class`, populated by importing the plugin
  modules. Adding a strategy = drop a file in `strategy/` + one register line. The UI
  reads this to build its dropdown; each entry also exposes `mechanism` and
  `parameter_grid` for display and param inputs.

**5. Dashboard Run page (new, `dashboard/views/run.py`; becomes default)**
- Form: strategy (dropdown from registry), tickers (multiselect from
  `DataSource.available_tickers()`), date range (from `DataSource.date_range()`),
  and cost knobs (`spread_bps_per_side`, `borrow_bps_annual`) with sane defaults.
- On Run: builds `BacktestConfig`, loads bars via `DataSource`, calls `run_simple`,
  renders from `Result`: equity curve (with SPY + 60/40 overlays), drawdown, CAGR /
  Sharpe / max DD metric tiles, year-by-year bar chart, per-regime table, trade count.
- Runs **in-process** — no CSV round-trip, no subprocess.

### Data flow

```
DataSource.load(tickers, start, end)
        -> bars (any frequency)
run_simple(strategy_cls, bars, config, params)
        -> position_history (existing _simulate: P&L + real costs, long/short)
        -> Result(equity, positions, trades)
        -> metrics + benchmarks (frequency-aware)
Run page renders Result   (no verdict, no gate)
```

### Error handling

- Unknown ticker → `DataSource` raises `KeyError`; Run page surfaces it as a friendly
  message, does not crash.
- Empty/too-short bars (< sigma window) → `Result` returns metrics as `NaN` with an
  explicit "not enough bars" note rather than fabricating a Sharpe.
- Benchmark ticker missing from the loaded universe → skip that overlay with a caption,
  keep the strategy curve.
- Plugin fails `validate_plugin` → registry refuses to list it (load-time failure, as
  the contract already enforces).

### Testing

- `run_simple` returns a `Result` whose equity matches `position_history` exactly
  (seam adds no P&L logic).
- Metrics: golden-master on the fixture — assert CAGR / Sharpe / maxDD to fixed values
  for `CounterTrendDipBuy` so refactors can't silently move the numbers.
- Frequency-awareness: same return series at daily vs a synthetic intraday index gives
  Sharpe scaled by the correct `sqrt(periods_per_year)` ratio.
- Benchmark: SPY buy-hold return computed by `Result` equals a hand computation on the
  fixture.
- Dashboard: `AppTest` smoke test — Run page renders and produces a `Result` for the
  default strategy without a live data source.
- Cost sanity (regression-lock the verified behavior): higher spread → lower end
  equity; short position accrues borrow.

## Repo cleanup (owner-approved: archive, don't delete)

Move to `archive/` (parked, recoverable — matches the "park not delete" decision):
- `src/engine_v2/gate/`, `dashboard/verdict_panel.py`
- v1 engine: `src/engine/`, `src/batch/`, `src/strategies/`
- screening scripts: `scripts/screen.py`, `scripts/evaluate.py`, `scripts/freeze_split.py`
- v1-based dashboard pages superseded by the Run page: `views/leaderboard.py`,
  `views/compare.py`, `views/plateau.py`, `views/run_detail.py`, `dashboard/recompute.py`,
  `dashboard/loader.py`, `dashboard/benchmark.py`

Keep `dashboard/{naming,style,shared}.py` if the Run page reuses them; archive
otherwise. `orchestrator.run_backtest` (the CPCV+gate entrypoint) stays in place but is
no longer on the everyday path — it is not imported by the workbench.

## Build order (working backtest as early as possible)

1. `DataSource` + `FixtureSource` — unblocks the loaded-bars contract.
2. `run_simple` seam + `Result`/metrics + frequency-aware annualization — the engine
   the workbench needs, headless and unit-tested.
3. Strategy registry + dashboard Run page — **backtesting is usable here.**
4. Archive the noise (option B) — last, once the clean path is proven.

## Non-goals / YAGNI

No CPCV, DSR, FWER, NCO, verdict gate, sealed-split ceremony, parameter sweeping, live
trading, or options layer in this project. This is a workbench for running one known
strategy honestly and reading the diagnostics.
