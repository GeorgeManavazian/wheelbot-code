# Chameleon dashboard page — design

**Date:** 2026-07-16
**Project:** Chameleon (regime router) — `code/etf-bot`
**Status:** design, pending implementation plan
**Depends on / consumes:** regime router (`regime_router.py`), regime state
(`regime/state.py`, `regime/data.py`), intraday wrappers (`intraday.py`), the
wheel dashboard page (`dashboard/views/wheel.py`) and its chart/blotter helpers
(`dashboard/charts.py`, `dashboard/trades.py`, `dashboard/bars.py`).

## Problem

The regime router is only reachable from CLI scripts (`run_regime_router.py`).
The owner wants to test it interactively the way the wheel is tested — pick a
ticker and config, run, see the result on a price chart with a blotter. Build a
Chameleon dashboard page that mirrors the Wheel page.

Two things make it more than a copy of `wheel.py`:

1. **The report layer is wheel-typed.** `wheel_report()` reads
   `result.days_flat` directly (`report.py:82`), a field `RouterResult` does not
   have — reusing it on a router result raises `AttributeError`. And
   `position_log()` only walks option legs; it ignores the router's
   `BUY_SHARES`/`SELL_SHARES`, so the router's TREND (hold-shares) posture is
   invisible to the blotter and the leg overlay.
2. **The unseen-ticker guard is copy-pasted and untested.** `UNSEEN = {"XBI",
   "EEM", "EWZ", "TLT", "ARKK", "QQQ"}` is duplicated in `views/wheel.py:19` and
   `views/regime.py:36`, as a **deny-list** — the exact form the CLI runner
   review rejected 2026-07-14 ("a deny-list fails open on typos and new
   tickers"). No test covers it. A third copy on the Chameleon page would widen
   an already-fragile seam guarding a one-shot resource.

## Non-goals

- Engine changes. The page consumes `run_regime_router` /
  `run_regime_router_intraday` exactly as they are.
- Running unseen tickers (XBI EEM EWZ TLT ARKK QQQ) from the UI. They stay
  hidden until the basket run reports — same discipline as the CLI
  `--after-basket-run` guard and the existing wheel/regime pages.
- Changing the router's frozen A/B definition or the amendment-16a hourly
  windowing rule. The page reflects them; it does not redefine them.
- Reproducing the referee-gated citation table on the page. Referee stays the
  CLI citation gate. The page is for exploration.

## Approach

A new `dashboard/views/chameleon.py`, structured like `wheel.py`, plus a shared
guard module and a router-aware report path. No engine edits.

### 1. Shared ticker guard — `dashboard/guard.py`

Replace both copy-pasted `UNSEEN` sets with one allow-list module, matching the
CLI runner's deliberate choice (allow-list, not deny-list).

```python
# dashboard/guard.py
SEEN = ("SPY", "GDX", "SLV", "XOP")   # burned tickers, safe to run pre-basket

def seen_sources(include_fixture_name=None, fixture_path=None) -> dict:
    """Ticker -> EOD chain path, restricted to the SEEN allow-list.
    Anything outside SEEN (unseen basket, new pulls) is excluded by construction,
    so a novel ticker dropped on disk never appears until SEEN is edited."""
    from src.engine_v2.options.data import available_tickers, chain_path
    out = {t: chain_path(t) for t in available_tickers() if t in SEEN}
    if include_fixture_name and fixture_path and os.path.exists(fixture_path):
        out[include_fixture_name] = fixture_path
    return out
```

`views/wheel.py` and `views/regime.py` switch to this (their local `UNSEEN`
sets and inline filters go away). Behavior is stricter, not looser: the
allow-list can only ever show SEEN + the explicit fixture.

### 2. Router-aware report — `router_report()` in `report.py` (chosen: fork A)

A dedicated router report, decoupled from `wheel_report`'s wheel-specific stats,
rather than retrofitting `wheel_report` to tolerate a router result. Rationale:
the two strategies already have separate result types and separate CLI runners;
the dashboard follows that existing seam. Retrofitting would add a wheel concept
(`days_flat`) to `RouterResult` and couple a future wheel-report change to the
router page.

`router_report(result, chain, cfg)` returns a small dataclass:

- `metrics`: `total_return`, `cagr`, `sharpe`, `max_drawdown` — computed from
  `result.equity` via `metrics_simple` (same functions the CLI runner's `perf()`
  uses, so the page and the CLI agree by construction).
- `posture`: `days_in_posture` (T/W/C), `transitions` (count of posture changes
  in `route_log`), `whipsaw_pairs`, `unknown` days — read off the
  `RouterResult` fields that already exist.
- `fills`: `intraday_tp_fills`, `eod_tp_fills` (0 on an EOD run).
- `benchmark_underlying`: buy-hold curve via `buy_hold_curve(chain, cap)` (reused
  unchanged), for the vs-buy-hold tile.
- `blotter`: `position_log(result, cfg)` — reused as-is. It walks option legs and
  is correct for the WHEEL-posture legs; TREND share holds are intentionally not
  in the blotter (they carry no option leg). The page states this.

No `days_flat`, no `campaign_table` — the two wheel-coupled paths that would
crash or mislead are simply not on the router path.

### 3. Posture band chart primitive — `charts.posture_bands(route_log)`

New helper returning a list of shaded spans `(start_date, end_date, posture)`
collapsed from the per-day `route_log` (consecutive same-posture days merge into
one span). The page draws the existing `candles_with_trades` (price + option-leg
overlay, reused unchanged) and layers the posture bands behind the candles as
translucent `vrect`s — TREND / WHEEL / CASH each a theme colour. This is the one
new visual: it shows *which strategy is active when*, the whole point of a
router, and it covers the TREND stretches the leg overlay leaves blank.

### 4. Page — `dashboard/views/chameleon.py`

Mirrors `wheel.py`:

- Ticker dropdown from `guard.seen_sources(...)` (+ SPY fixture fallback).
- Full config knobs (owner's call 2026-07-16): put/call delta, target DTE, TP,
  capital — same widgets as the wheel page.
- Date window (`date_input` start/end) + hourly take-profit checkbox (disabled
  when no hourly parquet on disk, same as wheel). XOP pre-2020-07-01 split
  warning carried over.
- On hourly, window the chain to the hourly bar span before running (amendment
  16a) so no arm silently falls back to EOD; caption the EOD-fallback day count.
- Run → build `WheelConfig(..., call_min_strike="basis")`, `states =
  regime_series(closes_for(ticker))`, then `run_regime_router` or
  `run_regime_router_intraday`. Stash result in session state (same pattern).
- Result: metric tiles (P&L / Sharpe / maxDD / vs buy-hold), a posture summary
  line (days T/W/C, transitions, whipsaws, intraday/EOD TP), the posture-shaded
  candle chart with the leg overlay, year-by-year bars, and the option-leg
  blotter. Caption: share-hold (TREND) P&L lives in the equity curve, not the
  blotter.

### 5. Off-config banner (non-blocking)

The frozen router form is `put_delta=0.20, call_delta=0.20, target_dte=7,
take_profit_pct=0.50, call_min_strike="basis"`. When the chosen config equals it,
the page shows a small note: "pre-registered form." When it differs, a caption
(not a blocker): "Off-config — not the pre-registered router; posture routing
shifts with DTE (see the v2 DTE experiment)." This labels exploratory runs so an
off-config number is not mistaken for the citable router. It does not gate the
run — the owner asked for full knobs.

## Data flow

```
guard.seen_sources ──▶ ticker dropdown ──▶ read chain parquet
                                             │
knobs + dates + hourly ──────────────────────┤
                                             ▼
              (hourly) window chain to hourly bar span   [amendment 16a]
                                             ▼
   states = regime_series(closes_for(ticker))
                                             ▼
   run_regime_router[_intraday](chain, cfg, states) ─▶ RouterResult
                                             ▼
   router_report(result, chain, cfg) ─▶ metrics · posture · blotter · benchmark
                                             ▼
   tiles · posture summary · posture-shaded candles+legs · yearly · blotter
```

## Error handling

- No SEEN data on disk → warning + `st.stop()` (wheel pattern).
- Chosen chain path missing → warning + stop.
- Window < 5 trading days → warning + stop (wheel pattern).
- No hourly parquet → hourly checkbox disabled with a help note (wheel pattern).
- `regime_series` empty (too little history) → warning, no run.
- Unseen ticker can never be selected (allow-list), so no runtime unseen guard is
  needed on the run path — the dropdown is the guard, and it is now test-pinned.

## Testing

1. **Guard allow-list** (`tests/test_dashboard_guard.py`, new): `seen_sources()`
   returns only SEEN + fixture; the unseen five and QQQ never appear; a synthetic
   `zzz_greeks_eod_all.parquet` dropped in a temp data dir is excluded by default.
   This is the load-bearing safety test — it guards the one-shot.
2. **Router page smoke** (`tests/test_chameleon_dashboard.py`, new): render the
   page against the SPY fixture, run a router result end to end, assert no
   `AttributeError` (the `days_flat` crash) and that metric tiles + blotter
   populate. Uses the same Streamlit test harness as `test_wheel_dashboard.py`.
3. **`posture_bands`** (unit): a small `route_log` with two posture changes →
   three merged spans with correct boundaries; a single-posture log → one span.
4. **`router_report`** (unit): on a fixture router result, metrics match a direct
   `metrics_simple` computation on the same equity; posture counts match the
   `RouterResult` fields; blotter row count equals the option-leg count.
5. Existing wheel/regime dashboard tests stay green after the guard swap.

## Files touched

- `dashboard/guard.py` — new, shared allow-list.
- `dashboard/views/chameleon.py` — new page.
- `dashboard/views/wheel.py`, `dashboard/views/regime.py` — switch to
  `guard.seen_sources` / the shared allow-list; drop local `UNSEEN`.
- `dashboard/app.py` — register the Chameleon page in nav.
- `dashboard/charts.py` — `posture_bands` helper + posture overlay on the router
  chart.
- `src/engine_v2/options/report.py` — `router_report()` + its result dataclass.
- `tests/test_dashboard_guard.py`, `tests/test_chameleon_dashboard.py` — new;
  plus `posture_bands` / `router_report` unit tests.

## Open items

None blocking. The page is exploration-only; the referee CLI stays the citation
gate, and no page number is citable without it.
