# Per-day step extraction (sub-project B1) — design

**Date:** 2026-07-17
**Project:** Live paper-trading bot — sub-project B1 (of B: paper-execution/state core). B decomposes into B1 step extraction → B2 state store + live paper-step → B3 affordability filter + N allocation → B4 P&L/snapshot recording.
**Status:** design, pending implementation plan
**Depends on / consumes:** `src/engine_v2/options/portfolio.py` (`run_portfolio_wheel`), the engine's `select`/`wheel` helpers.

## Problem

`run_portfolio_wheel` is a **batch backtest**: it preloads all history and replays every day in one loop. The live paper bot must run **one day at a time** — wake up, see today + yesterday's persisted state, act, sleep. To guarantee the live bot trades *identically* to what the backtest validated, the two must share the exact same per-day logic. This sub-project **extracts that per-day logic into a `step_one_day()` function** both callers use, behind a thin `Market` seam that lets the same `step` read from full history (backtest) or today's-data-plus-memory (live). B1 is a pure refactor: the backtest output stays byte-identical.

## Non-goals

- The live runner, state persistence (JSON), live Schwab wiring — sub-project B2.
- Affordability filter, N re-tuning — sub-project B3.
- P&L snapshot recording — sub-project B4.
- Any behavior change. B1 is structure-only; the batch result must not move.
- `LiveMarket` — defined/built in B2. B1 ships only the interface + `BatchMarket`.

## Architecture

Three pieces, all in `src/engine_v2/options/` (they belong with the engine; live code reuses them on py3.12):

### 1. `Market` — the data seam

A small interface `step_one_day` reads market data through, instead of touching data dicts directly. Methods (all keyed by ticker + day):
- `chain(ticker, day) -> pd.DataFrame | None` — the day's option chain (or None if no data).
- `spot(ticker, day, fallback) -> float` — the day's underlying price; `fallback` (the position's carried `last_spot`) when the day is absent.
- `settle_price(ticker, expiry) -> float | None` — last underlying price on-or-before `expiry` (the late-expiry settlement reach). **This is the only history-dependent access** — the reason the seam exists.
- `regime_row(ticker, day)` — the strictly-prior-day regime state row (or None), via the existing `_row_before`.
- `eligible(ticker, day) -> bool` — `day >= clean_start.get(ticker, day)` (the XOP-split guard).
- `universe -> list[str]` — the ordered universe.

### 2. `BatchMarket(chains, regime_states, clean_start)` — the backtest implementation

Wraps the preloaded `und` (per-ticker underlying series), `by_date` (per-ticker per-day chain), and `regime_states` dicts that `run_portfolio_wheel` already builds. `settle_price` does the real on-or-before-expiry history reach (`und[tk][und[tk].index <= expiry].iloc[-1]`), preserving today's exact batch behavior. `LiveMarket` (B2) will implement the same interface over today's adapter data + carried prices; `settle_price` there returns the carried `last_spot`.

### 3. `PortfolioState` + `step_one_day`

`PortfolioState` (a dataclass — the persistable state, JSON-serialized in B2):
- `cash: float`
- `positions: list` — open campaigns; each a dict `{ticker, short, shares, phase, basis, premium, campaign, last_spot}` (unchanged from today's `pos` dict).
- `campaign: int` — the campaign-id counter.
- `days_flat: int`, `days_shares_uncovered: int` — running diagnostics.
- `prev_d` — last processed date (for cash-yield accrual); `None` initially.

`step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult`:
- **Mutates `state`** in place (cash, positions, counters, prev_d advance).
- Returns `StepResult(trades: list, equity: float, warnings: list, route_events: list)` — *today's* outputs only.
- Body = the current loop body verbatim (`portfolio.py:87-224`): cash-yield accrual → manage each held position (TP → expiry/assignment → covered-call at 0.50-delta/basis-floor) → drop finished campaigns → routing fill (chop eligibility + `vol_pctile` rank + `cash/empty_slots` sizing) → flat/uncovered accounting → equity mark. Every `by_date`/`und` read is replaced by the matching `market.` call.

### `run_portfolio_wheel` after the refactor

Shrinks to the batch shell:
1. Validate universe (reserved-ticker refusal, regime-states present) — unchanged.
2. Preload `chains`/`regime_states`, build `BatchMarket`.
3. Init `PortfolioState`.
4. `for day in dates: r = step_one_day(state, market, day, cfg, selector=selector, n_slots=n_slots)` — accumulate `r.trades`, `r.equity` (into the equity Series), `r.warnings`, `r.route_events`.
5. **Residual-settlement finalizer** (batch-only — `portfolio.py:226-237`): mark still-open positions at the last day. The live bot never runs this.
6. Assemble and return `PortfolioResult` exactly as today (same fields: equity, trades, final_cash, final_shares, residual_settled, days_flat, warnings, days_shares_uncovered, route_events, n_campaigns_opened).

## Data flow

```
BATCH:  run_portfolio_wheel -> preload -> BatchMarket -> PortfolioState
          -> for day: step_one_day(state, BatchMarket, day, cfg, ...) -> finalize -> PortfolioResult
LIVE (B2): load PortfolioState (JSON) -> LiveMarket(today) -> step_one_day(state, LiveMarket, today, cfg, ...) -> save state
```

Same `step_one_day`, two `Market` implementations.

## Error handling

- Unchanged from today: universe validation raises on reserved/unknown-state tickers; a missing day's chain → `market.chain` returns None and the existing None-guards handle it; a missing spot → `market.spot` falls back to the position's `last_spot`.
- `step_one_day` makes no I/O and no network calls — pure computation over `state` + `market`. (Persistence and live fetch are B2.)

## Testing

- **Byte-identical anchor (load-bearing):** the entire existing portfolio suite stays green **unchanged** — `tests/engine_v2/options/test_portfolio.py` (incl. `test_universe_of_one_matches_solo_run_wheel`, which pins trade-by-trade equality + `equity.equals` against the solo wheel), `test_portfolio_selector.py`, `test_portfolio_nslots.py`, `test_portfolio_universe.py`. Plus the `--rotation` referee (`scripts/audit_defense_execution.py --rotation`) still exits 0. These already pin the batch output penny-for-penny; if the refactor changed behavior, they fail.
- **`step_one_day` isolation unit** (new): a tiny hand-built `PortfolioState` + a fake/`BatchMarket` with one ticker and one day where a short put expires worthless → assert the returned `StepResult.trades` (a `PUT_EXPIRED`) and the mutated state (campaign dropped, cash unchanged) are exactly right. Proves the extracted brain works standalone — how the live bot calls it.
- **`BatchMarket` unit** (new): `chain`/`spot`/`regime_row`/`eligible` return the right wrapped values; `settle_price` returns the last price on-or-before a given expiry (the history reach), and `spot` uses the fallback when the day is absent.
- Full 3.9 suite green.

## Files touched

- `src/engine_v2/options/portfolio.py` — extract `step_one_day` + `PortfolioState` + `StepResult`; `run_portfolio_wheel` reduced to the shell.
- `src/engine_v2/options/market.py` (new) — `Market` interface + `BatchMarket`.
- `tests/engine_v2/options/test_step_one_day.py`, `tests/engine_v2/options/test_batch_market.py` — new.

## Open items

None blocking. `LiveMarket` and persistence are B2; this sub-project is the shared-brain extraction only, gated by the byte-identical anchor.
