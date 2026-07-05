# ETF Bot Research Engine — Design Spec

**Date:** 2026-07-05
**Status:** Approved by owner (session 2026-07-05)
**Decision:** Build a reusable strategy-testing engine first, instead of tailoring code to one strategy. Strategy-as-plugin; batch-test many strategies against a frozen dataset.

## Goal

A transparent, auditable backtesting engine for ETF daily-bar strategies that:

1. Tests many strategies/parameter sets quickly (batch runner).
2. Makes look-ahead and multiple-testing cheating structurally hard.
3. Later becomes the core of the live Schwab-automated trading bot (byte-identical engine rule).

Owner is a near-beginner coder — code should be simple, readable, and teach through the work. Transparency beats speed: daily bars × ~20 ETFs × 15 years is tiny data (~3,800 bars/instrument); a plain pandas loop is fast enough for thousands of runs.

## Non-goals (v1)

- No intraday data, no options, no shorting, no leverage.
- No vectorized/optimized engine (vectorbt-style) — not needed at this scale.
- No live trading integration yet (Schwab API comes after the engine is proven).

## Architecture

```
data/raw/         <- yfinance pulls, untouched
data/playground/  <- frozen parquet 2010–2020 + SHA256 manifest (committed)
data/exam/        <- frozen parquet 2021+    + SHA256 manifest (committed)
src/engine/       <- data loader, backtest loop, accounting, metrics
src/strategies/   <- one file per strategy plugin
src/batch/        <- batch runner, leaderboard, plateau report
results/          <- run outputs (parquet + CSV), gitignored
tests/            <- pytest suite
```

### 1. Data layer

- **Universe (v1, ~20 liquid ETFs):** SPY, QQQ, IWM, EFA, EEM, TLT, IEF, SHY, LQD, HYG, GLD, SLV, DBC, VNQ, XLE, XLF, XLK, XLV, XLU, XLI.
- **Source:** yfinance bootstrap (free), daily OHLCV, split+dividend adjusted. Must verify `auto_adjust` behavior explicitly — known foot-gun. Schwab API price history becomes canonical source later.
- **QC script checks:** missing bars vs NYSE calendar; zero/negative prices; adjustment sanity (SPY total return vs known benchmark); per-ETF inception date report so no instrument silently enters mid-backtest.
- **Freeze protocol:** playground = 2010-01-01 to 2020-12-31; exam = 2021-01-01 onward. Both stored as parquet with SHA256 checksums in a committed manifest. QC report saved alongside.
- **Exam seal:** loader refuses `data/exam/` unless called with an explicit `--exam` flag; every exam load appends a line to a permanent exam-run log (committed). Mass screening physically cannot touch exam data by accident.

### 2. Engine core (~300 lines target)

- Daily event loop. At bar close T the strategy receives a data window ending at T and returns target weights. Orders execute at T+1 **open**. Same-bar fills are impossible by construction.
- **Friction:** commission $0 (Schwab equities); slippage default 5 bps each way, configurable up to 10–20 bps for stress runs.
- **Constraints:** weights sum ≤ 1.0; no leverage; no shorting; whole-share positions; dollar accounting.
- Unfilled/infeasible orders (insufficient cash after rounding) are dropped with a warning, never silently borrowed.

### 3. Strategy API (plugin contract)

```python
class Strategy:
    name: str
    params: dict

    def target_weights(self, window: pd.DataFrame) -> dict[str, float]:
        """window = all data up to and including today's close.
        Return {ticker: weight}. Missing tickers = weight 0."""
```

- Engine passes ONLY the truncated window — a strategy physically cannot read future data.
- One file per strategy in `src/strategies/`. All candidate families (momentum rotation, time-series trend, counter-trend dips, trend-filtered allocation) are expressible as target weights.
- Idea pipeline: vault strategy note (Quantpedia / Clenow / Chan / anywhere) → one plugin file → batch leaderboard → kill or iterate verdict recorded back in the vault.

### 4. Metrics

Per run: CAGR, max drawdown, Sharpe, trade count, annual turnover, exposure %, average positions held, year-by-year return table.

Honesty stats printed with every result: trade count vs statistical-significance floor (30 absolute minimum, 100+ preferred), count of positive years, flag if performance concentrated in 1–2 exceptional periods.

### 5. Batch runner

- Input: list of (strategy class × parameter grid).
- Runs all combinations on playground data; writes results parquet + leaderboard CSV.
- **Plateau report:** for each parameter set, show neighboring parameter sets' performance side by side — narrow spikes (curve-fit) vs plateaus (robust) visible at a glance.

### 6. Anti-luck guardrails

- Mass screening runs on playground ONLY.
- Leaderboard header prints a multiple-testing reminder: N runs tested → expected best-by-luck Sharpe estimate.
- Exam protocol: pass bars pre-registered in a vault decision note BEFORE the run; one shot; result recorded win or lose; failures are recorded, not rescued.

### 7. Testing (pytest)

- Synthetic price series with hand-computed P&L — engine output must match exactly.
- Look-ahead canary: a "cheater" strategy attempting to buy before a known jump must be inexpressible through the API (verify the window truncation).
- Unit tests: friction math, whole-share rounding, cash constraint, next-open fill timing.

## Build order

1. Data layer: pull → QC → freeze split with checksums.
2. Engine core + full test suite.
3. Two reference strategies as API proof: momentum rotation (Clenow ch 12 adapted) + time-series trend (ch 16).
4. Batch runner + plateau report.
5. First screening session on playground.

## Error handling

- Data layer: hard-fail on QC violations (missing bars, bad adjustments) — no silent patching; failures reported for owner decision.
- Engine: unknown ticker in weights → error; NaN prices inside a window → error (data must be fixed upstream, never interpolated silently).
- Batch runner: one strategy crashing must not kill the batch — log the failure, continue, report at the end.

## Open items (deferred, not blockers)

- Schwab API key (owner provides when ready) — canonical data + live orders.
- ThetaData key — options phase, much later.
- Deflated Sharpe / White's reality check as a formal multiple-testing correction — v2 of the leaderboard.
- Treasury-ETF cash parking (SHY) instead of raw cash — strategy-level choice, not engine.
