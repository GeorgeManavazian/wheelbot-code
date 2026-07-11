# Design: Wheel sub-project 5 — reporting

- **Date:** 2026-07-11
- **Status:** design — awaiting review
- **Part of:** the Wheel-on-SPY options backtester. Sub-project 5 of ~5.
- **Scope:** turn a `WheelResult` (equity + trade log) into a digestible report — headline metrics, year-by-year, vs buy-hold SPY, and wheel-specific stats. Reuses the frequency-aware `metrics_simple`. Includes one required engine fix (settle a residual open short).

## Why now

The engine produces an equity curve + trade log but nothing that answers "is this wheel any good?". Reporting turns it into the numbers the owner judges on — and honors the standing methodology: **judge on the recent window (headline), keep full history, always show year-by-year, never one blended number** (`backtest-judge-on-recent`).

Caveat: reporting is only *meaningful* over a long history. On the current 2-month fixture the numbers are illustrative, not conclusive — a fuller multi-year pull (sub-project 3) makes it real. Building the machinery now is still worthwhile and unblocks that.

## Required engine fix first (final-review finding #3)

`run_wheel` currently only resolves a short on `d == expiry`. If the backtest window ends with a short still open (the normal case for the last position), `final_cash`/`final_shares` misrepresent the end state (the equity series is correct — it carries the mid liability). **Fix:** after the loop, settle any residual open short — buy it back at the final day's mid (mark-to-mid, no commission, a synthetic close) OR expose the open short on `WheelResult`. Chosen: **settle to mid** so `final_cash`/`final_shares` are self-consistent with the equity series, and add a `residual_settled: bool` flag on `WheelResult`. This makes the report's totals honest.

## Inputs

- `WheelResult` (equity Series, trades, final_cash, final_shares).
- The `chain` (for the underlying series → the SPY buy-hold benchmark, and to derive `periods_per_year`).
- `WheelConfig` (starting_capital, commission — for premium/commission totals).

## The report (`WheelReport` dataclass / dict)

**Performance (via `metrics_simple`, frequency-aware):**
- `total_return`, `cagr`, `sharpe`, `max_drawdown` — full history.
- `recent` — same metrics over the recent window (headline; default `recent_start` = the standing 5y, but clamped to the data's span; on short data it equals full history and is labeled as such).
- `yearly` — per-year return AND per-year Sharpe (the decay curve).
- Benchmark: **buy-hold SPY** over the same dates (shares = capital / first spot; equity = shares × spot), reported as the same metric set for side-by-side.

**Wheel-specific (from the trade log):**
- `n_puts_sold`, `n_calls_sold`, `n_assignments`, `n_called_away`, `n_take_profits`, `n_expired_worthless`.
- `premium_collected` (sum of SELL_* credits × mult × contracts), `premium_paid_to_close` (sum of CLOSE_* debits), `commission_paid` (per-contract × all option legs), `net_premium`.
- `assignment_rate` = assignments / puts_sold.
- `avg_days_held`, `pct_time_in_put_phase` vs call phase.

No pass/fail verdict (the gate stays parked). Diagnostics only.

## Where code lives

New module `src/engine_v2/options/report.py`:
- `spy_buy_hold(chain, starting_capital) -> pd.Series` (benchmark equity from the underlying series).
- `wheel_report(result, chain, config, recent_start="2021-07-01") -> WheelReport`.
- `format_report(report) -> str` (a clean printable text block, year-by-year table included).

Reuses `src.engine_v2.backtest.metrics_simple` (`cagr, sharpe, max_drawdown, yearly_returns, yearly_sharpe, infer_periods_per_year`). Imports the wheel types + metrics_simple + pandas. NOT the gate.

## Dashboard page (IN SCOPE — owner wants the report in the dashboard)

A Streamlit **"Wheel" page** added to the existing workbench (`dashboard/app.py` + `dashboard/views/wheel.py`):
- **Data source** selector: the committed `fixtures/spy_wheel_cycle.parquet` (always available) and the full pull `data/options/spy_greeks_eod_all.parquet` (shown when it exists, after the bulk pull's `--concat`). Guard with a friendly message if the full file isn't built yet.
- **Config inputs:** put/call delta, dte_min/max, take-profit % (with a "hold to expiry" = None option), commission/contract, starting capital.
- **On Run:** `run_wheel` → `wheel_report`, rendered: recent-headline metric tiles (CAGR/Sharpe/maxDD), **equity curve vs SPY buy-hold**, year-by-year return bars, wheel-stats block, and the **trade log** as a table.
- Reuses `wheel_report` (headless core stays the single source of truth); the page is a thin view. `format_report` remains for headless/logging use.

## Testing

- Engine residual-settlement fix: a synthetic window that ends with a short open → `final_cash` reflects the mark-to-mid buy-back and `residual_settled is True`; equity series unchanged.
- `spy_buy_hold`: shares = capital/first_spot; ending equity = shares × last spot (hand-checked on a tiny chain).
- Wheel stats: on the committed real fixture (12 trades, all TP), assert `n_puts_sold == 6`, `n_take_profits == 6`, `n_assignments == 0`, and `premium_collected`/`commission_paid` to pinned values.
- Metrics: `wheel_report` on the real fixture returns finite CAGR/Sharpe/maxDD and a `yearly` with 2024; `recent` equals full history on this short span and is flagged.
- `format_report` renders without error and contains the headline + year-by-year + wheel-stats sections.

## Non-goals

- No dashboard (flagged optional). No pass/fail gate. No new data pull (uses whatever chain is passed). No rolling/intraday changes. No multi-underlying.

## Open choices (defaulted; flag to change)

- **Residual short settled to mid** (vs exposing it unsettled). Mid keeps `final_cash` consistent with the equity curve.
- **Buy-hold SPY** as the single benchmark (the wheel's natural comparison — "would I have done better just holding SPY?"). 60/40 is less relevant for a single-name options strategy.
- `recent_start` clamped to the data span, labeled when it collapses to full history on short data.
