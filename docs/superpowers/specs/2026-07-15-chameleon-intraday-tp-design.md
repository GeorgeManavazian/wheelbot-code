# Chameleon intraday-TP mode — design

**Date:** 2026-07-15
**Project:** Chameleon (regime router) — `code/etf-bot`
**Status:** design, pending implementation plan
**Depends on / consumes:** wheel intraday path (`intraday.py`, `wheel.py:150-181`), regime router v1 (`regime_router.py` @ `d34c335`), referee (`scripts/audit_defense_execution.py`)

## Problem

The regime router is EOD-only by design. Its WHEEL posture manages an open short by
end-of-day ask only (`regime_router.py:101-108`). The solo wheel already has an
intraday (hourly) take-profit path that fills at the next valid hourly bar
(`wheel.py:150-181`), so the current router-vs-wheel A/B table
(`data/options/reports/regime_router.txt`) silently denies the router the
intraday best case the wheel enjoys — an unfair comparison, flagged in the
Chameleon `_STATUS` honesty section.

Goal: give the router's WHEEL posture the same next-valid-hour TP fill, so
router-vs-wheel is an intraday-vs-intraday fight. **Routing decisions stay EOD**
— this is not intraday regime switching.

## Non-goals

- Intraday **routing** decisions (regime cell re-evaluated per hour). Regime
  module is daily; out of scope. This spec touches only the WHEEL leg's TP fill.
- New knobs, new strategy behavior, roll/stop/gates (router v1 refuses these).
- Unseen tickers. Seen four only (SPY GDX SLV XOP); unseen (XBI EEM EWZ TLT
  ARKK) stay behind `--after-basket-run` — running them spends the wheel's
  pre-registration.

## Approach

Transplant the wheel's proven intraday-TP block into the router's short-management
block. Invent nothing new; mirror `run_wheel_intraday` exactly. Three changes.

### 1. Engine — `run_regime_router(chain, cfg, regime_states, intraday=None)`

Add an `intraday=None` keyword param (a `{(expiry, strike, right): DataFrame[timestamp, close]}`
dict, identical shape to what `intraday_marks` produces and what `run_wheel`
consumes).

In the short-management block (`regime_router.py:101-108`), replace the EOD-only
TP check with the wheel's two-tier logic, verbatim in structure
(`wheel.py:150-181`):

1. `thresh = (1 - take_profit_pct) * short["credit"]`
2. If `intraday is not None` and the leg's `key=(expiry, strike, right)` is present:
   - Take that day's bars filtered to `close > 0`, sorted by timestamp.
   - For `i in range(len(day) - 1)`: if `day[i].close <= thresh`, fill at
     `day[i+1].close` (next valid bar), record `CLOSE_PUT`/`CLOSE_CALL` at
     `fill.timestamp`, set `short = None`, `closed_today = c`, `tp_fired = True`, break.
   - A cross on the day's **last** bar has no next bar → no intraday fill; fall
     through to the EOD check.
3. If not `tp_fired` and `mark.ask <= thresh`: EOD fill (today's behavior, unchanged).

Applies to both short puts and covered calls in WHEEL cells — the wheel's block
keys on `c.right`, so both get intraday TP, matching the solo wheel.

**Invariant (must be test-pinned): `intraday=None` → byte-identical output to
today's router.** When `None`, tier 2 is skipped and only the EOD branch runs =
current behavior. This protects the 2026-07-15 deep-audit-clean status and the
all-chop ≡ solo-wheel byte-identical anchor.

### 2. Wrapper — `run_regime_router_intraday(chain, cfg, intraday_df, regime_states)`

Add to `intraday.py`, mirroring `run_wheel_intraday` (`intraday.py:77-80`):

```python
def run_regime_router_intraday(chain, cfg, intraday_df, regime_states):
    from .regime_router import run_regime_router
    return run_regime_router(chain, cfg, regime_states,
                             intraday=intraday_marks(intraday_df))
```

Reuses the existing `intraday_marks` filter (`volume > 0` and `close > 0`).

### 3. Referee — router-hourly citation gate

`audit_defense_execution.py` currently has: `audit()` (EOD, router included) and
`audit_hourly()` (plain wheel intraday only, via `run_wheel_intraday`). Add a
router-hourly path so `--router --hourly` re-derives the router's intraday fills
and exits 0.

The existing `audit_hourly` leg-walk (`:259-330`) is strategy-agnostic: it
reconstructs held short legs with `positions_from_trades` and derives each leg's
first termination (INTRADAY_TP / EOD_TP / EXPIRY) from the raw bar parquet with
independent code. Only `res = run_wheel_intraday(ch, cfg, ih)` (`:250`) is
wheel-specific. Router mode swaps that one line for
`run_regime_router_intraday(ch, cfg, ih, regime_states)` and reuses the rest.

Why the leg-walk is valid for the router unchanged:
- `positions_from_trades` reacts only to `SELL_PUT`/`SELL_CALL`/`ROLL_OPEN`
  (open) and TERMINAL (close). The router's extra `BUY_SHARES`/`SELL_SHARES`
  trades are ignored; the router emits no roll/stop. So leg reconstruction is
  unchanged.
- The router manages an open short every day by solo rules, cell-independent
  (`regime_router.py:98`). The referee's per-leg derivation also ignores cells.
  They align — a leg opened in a WHEEL cell is TP-managed regardless of later
  cell, exactly as the referee assumes.

Implementation shape: parametrize `audit_hourly(ticker, start, router=False)` or
add a thin `audit_hourly_router(ticker)`; wire `--router --hourly` in `main`.
Router mode must load `regime_states` (same source as the EOD router audit) and
pass it through.

## Contamination guards (carried, not re-fought)

- **Phantom-fill / close=0:** the `close > 0` filter is inherited at both layers
  (`intraday_marks` and the engine bar-walk). A zero close is not a price
  (XOP 2020 +2,582% fantasy). No new exposure.
- **XOP split (1:4 reverse, 2020-03-31, unadjusted):** XOP arm stays `2020-07-01+`.
  The engine has no split handling; any leg spanning that date is phantom.
- **Same-day close=0 intraday bug (fixed @ `e969b53`):** the `close > 0` filter
  is the fix; it is transplanted intact.

## Data

All nine tickers have `data/options/{ticker}_ohlc_1h_all.parquet` on disk,
including SPY (`spy_ohlc_1h_all.parquet`) — verified 2026-07-15. EOD chains:
`data/options/{ticker}_greeks_eod_all.parquet`. Sub lapses ~2026-07-25; no new
pulls needed — existing parquets suffice for the seen four.

## Reporting

Add a fill-type counter to `RouterResult`: `intraday_tp_fills` and `eod_tp_fills`
(ints). Populated in the short-management block. Serves the honesty flag —
intraday fills are optimistic-biased (fill at next-bar close, hourly trade
prints, no quotes on STANDARD). The report and any cited A/B must state this, as
the wheel's hourly report already does.

## Testing

1. **Byte-identical invariant** — router with `intraday=None` produces identical
   equity/trades to the current router on all four seen tickers (or a fixture).
   This is the load-bearing test.
2. **Intraday TP fires** — a fixture where a bar crosses `thresh` mid-day and the
   next valid bar is the fill; assert `CLOSE_*` at the next bar's timestamp/price.
3. **Last-bar cross → EOD fallback** — a cross on the day's final bar takes the
   EOD branch, not an intraday fill.
4. **close=0 ignored** — a zero-close bar below thresh does not trigger a fill.
5. **Wrapper** — `run_regime_router_intraday` equals `run_regime_router` with
   `intraday=intraday_marks(df)`.
6. **Referee `--router --hourly` exits 0** on the seen four — the citation gate.
   No router-hourly number is citable until this passes.

## Scope guards

- Seen four only. Unseen refuse without `--after-basket-run` (existing guard;
  the router-hourly path must inherit it).
- No new knobs, no roll/stop/gates.
- Everything in-sample on burned tickers — correctness, not a fresh-data verdict.

## Files touched

- `src/engine_v2/options/regime_router.py` — `intraday=` param + TP transplant + counters
- `src/engine_v2/options/intraday.py` — `run_regime_router_intraday` wrapper
- `scripts/audit_defense_execution.py` — router-hourly referee path
- `scripts/run_regime_router.py` — optional: load hourly parquets, report intraday A/B
- tests — new router-intraday test module

## Open items

None blocking. `run_regime_router.py` intraday A/B reporting is optional in this
spec (the engine + referee are the load-bearing pieces); it can be a follow-up if
the plan prefers to keep this change tight.
