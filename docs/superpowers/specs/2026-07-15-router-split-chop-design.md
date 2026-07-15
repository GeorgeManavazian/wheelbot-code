# v2 Router — split-chop-by-200 (`split_chop`) — design

> ## ⛔ FALSIFIED IN-SAMPLE (2026-07-15) — ABANDONED, DO NOT REVIVE AS SPECIFIED
> Built on branch `feat/router-split-chop` (deleted), referee-validated (routing
> correct, 0 mismatches, 121/177/251/99 split-days fired), then A/B'd in-sample.
> The rule **failed**: `ROUTER+split` tied or lost on all four seen tickers
> (SPY 145.3 vs 148.6, GDX ~tie, SLV 248.8 vs 265.3, XOP 145.8 vs 160.8).
> **Mechanism:** reclassifying chop-recovering days to the TREND cell buys shares,
> but the TREND posture carries a **forced-exit on downtrend**, and chop is price
> oscillating around the 200-line — so it buys, gets force-sold on the next dip,
> and whipsaws (SELL_SHARES 0→2/8/18, whipsaws 0→2/8/15). The motivating
> attribution measured per-day buy-hold return over above-200-chop days, which
> assumed **frictionless holding**; the router's TREND posture cannot hold
> frictionlessly. Vindicates the prior falsified findings (exits during declines
> sell bottoms). Kept as the pre-registration record. The open question it
> surfaced — *can a HOLD that does NOT force-sell on the dip recover the
> surrender?* — is a separate, un-started design.

**Date:** 2026-07-15
**Project:** Chameleon (regime router) — `code/etf-bot`
**Status:** FALSIFIED in-sample, abandoned (see banner above)
**Depends on / consumes:** regime router (`regime_router.py`), regime state (`regime/state.py`, provides `px_vs_200`), referee (`scripts/audit_defense_execution.py`)

## Pre-registration statement (binding)

This rule is **frozen by this document before it is run on any unseen ticker.** It is
economically motivated ex-ante, not fitted: *a stock trading at or above its own
200-day average is in an uptrend and should be held, even before the slow 50/200
moving-average cross confirms it.* The in-sample attribution that motivated it (below)
is a **mechanism check**, not a parameter search — no threshold here is tuned. The
real verdict is the basket run's unseen five (XBI EEM EWZ TLT ARKK), reported
separately, with the referee green, exactly as router v1 was validated. **Running
this on unseen tickers before the basket run spends the pre-registration — do not.**

## Problem (the motivating attribution — in-sample, seen four)

Router v1 beats the solo wheel on all four seen tickers but trails buy-hold on
SPY/GDX/XOP. Attribution of the router-vs-buy-hold gap by posture (in-sample)
localized the bleed to the **WHEEL posture**, and splitting WHEEL days by price
vs the 200-day SMA localized it further — the loss and the win live in *separable*
regimes:

| Ticker | WHEEL, price ≥ 200d (router − held) | WHEEL, price < 200d (router − held) |
|---|---|---|
| SPY | +13.9% | −7.9% |
| GDX | **−138.6%** | +5.4% |
| SLV | −3.6% | +9.7% |
| XOP | **−132.1%** | +3.0% |

The surrender (GDX −138, XOP −132) is almost entirely in **chop above the 200-line**
— early recoveries the classifier still labels "chop" because the 50/200 golden
cross lags the bottom by weeks/months. In that window the router wheels the stock
(cash-secured puts don't participate; covered calls cap the climb) instead of
holding it. Meanwhile **chop below the 200-line** is where the wheel *earns its
rent* — router beats holding on all four (SPY the lone −7.9 exception). The two
buckets barely overlap, so a rule that changes only the above-200 bucket is not a
wash: it recovers the surrender without touching the winning bucket.

## The rule

Currently `regime_router._cell` classifies the prior-day state into a routing cell:

- `uptrend` (close>200 AND 50>200) → TREND (hold)
- `chop` (close and 50 disagree about the 200) → WHEEL
- unpaid decline (downtrend + calm/normal) → CASH
- downtrend+stressed / unknown → WHEEL

With `split_chop = True`, one classification changes:

> A `chop` day whose `px_vs_200 >= 0` (price at or above the 200-day SMA) is
> reclassified to **`uptrend` → TREND cell.** `chop` with `px_vs_200 < 0` stays
> WHEEL. `px_vs_200` missing or stale → treated as `< 0` (stays WHEEL — never
> hold on absent information, mirroring the router's `unknown → WHEEL` rule).

`px_vs_200` is read from the **same strictly-prior, staleness-bounded state row**
that `_state_before` uses for `trend`/`vol` — no look-ahead, and consistent with
the row the trend/vol decision came from.

## Why mapping to the existing TREND cell is the whole implementation

Reclassifying chop-recovering to the existing `TREND` cell makes the desired
open-position behavior (owner decision: "don't force anything, stop adding") fall
out with **zero new code paths**:

- **Flat cash + TREND cell** → buys & holds (existing entry, `regime_router.py:150-170`).
- **Assigned shares (phase CALL) + TREND cell** → the covered-call entry requires
  `cell == "WHEEL"` (`:186-187`), so no call is sold → shares held **uncovered**,
  participating in the climb. The `days_shares_uncovered` counter already exempts
  non-WHEEL cells (`:216`), so uncovered-in-trend is correctly not counted a failure.
- **Open short put + flip to TREND** → step-1 short management is cell-independent
  (`:98`), so the put runs to TP/expiry/assignment untouched.
- **Price later drops below 200** → chop-rolling → WHEEL cell → covered calls
  resume; the existing "hand trend shares to the wheel on chop" transition
  (`:142-147`) already handles a hold→wheel handoff.
- **Flip to downtrend** → chop-recovering shares are `shares` (assigned) or
  `trend_shares` (bought); the existing forced-exit (`:133-141`, trend_shares +
  downtrend) and assignment lifecycle apply exactly as for any TREND position.

No new posture, no new lifecycle, no forced unwind. The only change is the cell
label.

## Components

### 1. Engine — `regime_router.py` + `WheelConfig`

- `WheelConfig.split_chop: bool = False` (new field; default OFF).
- New helper `_px_before(states, d)` — returns `px_vs_200` from the last state
  row strictly before `d`, bounded by the same `GATE_STALENESS_DAYS` as
  `_state_before`; returns `None` if missing/stale. (Do not change
  `_state_before`'s signature — it is unpacked as `(trend, vol)` in multiple
  callers.)
- `_cell(states, d, split_chop=False)` — after computing `(trend, vol, unknown)`
  as today, if `split_chop and trend == "chop"`: read `px = _px_before(states, d)`;
  if `px is not None and px >= 0` → return **`"TREND", "uptrend", vol, False`**.
  Otherwise unchanged. The router passes `cfg.split_chop` into `_cell`.
- **The returned trend is relabeled to `"uptrend"`, not left as `"chop"`.** The
  router's transitions key on the returned trend (`g_trend`): `g_trend=="chop"`
  hands trend shares to the wheel, `g_trend=="downtrend"` force-sells. A
  chop-recovering day must be a genuine hold, so it must present as `uptrend` to
  those transitions — otherwise a held position would be handed straight to the
  wheel on the same day, defeating the rule. Consequence: when price later drops
  below the 200-line, `g_trend` becomes `"chop"` again (px < 0, not relabeled) and
  the existing hand-to-wheel transition fires correctly; a drop to real downtrend
  force-sells exactly as for any TREND position. `route_log` records the effective
  (relabeled) trend, which is the routing decision actually taken.
- **Byte-identical invariant:** with `split_chop=False`, `_cell` returns exactly
  today's classification; the router output is identical to v1 on all four seen
  tickers. Test-pinned. Protects merged v1 (`be2bc3d`).

### 2. Referee — `scripts/audit_defense_execution.py`

- `_cell_local` gains the same split, with its OWN independent `px_vs_200` lookup
  (re-derived from the local state series, NOT imported from the engine — the
  referee's independence is the whole point). It reads px from its own as-of row,
  and for a chop-recovering day returns `("TREND", "uptrend")` — the same relabel
  the engine applies, so the referee's share-action day-walk (which keys on the
  returned trend for its hand-to-wheel and forced-sale checks) mirrors the engine.
- `audit_router` adds a `split_chop=True` arm alongside the existing arms
  (mirroring how the conviction-trim arm is audited), re-deriving every route +
  share action + leg termination for the split config. `--router` must exit 0.

### 3. Run script — `scripts/run_regime_router.py`

- A `ROUTER+split` arm in the A/B (like the existing `ROUTER+trim` arm), run with
  `split_chop=True`, so the in-sample effect is visible. The existing seen-only
  allow-list (`:40-45`) is unchanged; the arm is off by default in v1's frozen
  config.

## Testing

1. **Byte-identical when OFF** — `run_regime_router` with `split_chop=False`
   equals v1 output (equity + trades) on all four seen tickers. Load-bearing.
2. **Reclassification** — a synthetic/known chop day with `px_vs_200 >= 0`
   routes to TREND under `split_chop=True` (buys/holds, no put/covered-call
   opened that day); the same day with `px_vs_200 < 0` stays WHEEL.
3. **Look-ahead guard** — `_px_before` never reads the day-`d` row; a state row
   dated `d` is not consulted for the day-`d` decision.
4. **Missing/stale px** — `_px_before` returns `None` → day stays WHEEL.
5. **Referee `--router` exits 0** with the split arm — re-derives the split
   classification independently, 0 mismatches on the seen four.

## Scope guards (YAGNI)

- One new bool, no other knobs. No new posture. Chop-rolling (below 200) stays
  WHEEL — the data-confirmed winning bucket; do not touch it.
- No forced unwinds of open positions (owner decision).
- Seen four only until the basket run; unseen refuse without `--after-basket-run`.
- `split_chop` default OFF everywhere — v1 remains the shipped behavior until the
  out-of-sample verdict is in.

## Expected in-sample result (stated in advance, for honesty)

On the seen four this should recover most of the GDX/XOP surrender and cost SPY a
little (SPY's above-200 chop was genuine chop where the wheel won). A large
in-sample improvement is EXPECTED and is NOT evidence the rule generalizes — it is
the same data the rule was reasoned from. Only the unseen five decide.

## Files touched

- `src/engine_v2/options/regime_router.py` — `split_chop` param, `_px_before`, `_cell` split
- `src/engine_v2/options/wheel.py` — `WheelConfig.split_chop` field
- `scripts/audit_defense_execution.py` — `_cell_local` split + `audit_router` split arm
- `scripts/run_regime_router.py` — `ROUTER+split` A/B arm
- tests — new `split_chop` test module

## Open items

None blocking.
