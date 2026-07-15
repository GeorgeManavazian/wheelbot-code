# v2 Router — regime-conditional wheel params (DTE + stressed-downtrend delta) — design

**Date:** 2026-07-15
**Project:** Chameleon (regime router) — `code/etf-bot`
**Status:** design — PRE-REGISTERED (values fixed by reasoning below, before any run). Pending implementation plan.
**Depends on / consumes:** regime router v1 (`regime_router.py` @ intraday-TP merge `be2bc3d`, deep-audit-clean `d34c335`), wheel engine (`wheel.py`), contract selection (`select.py`), referee (`scripts/audit_defense_execution.py --router`).

## Problem

The router's WHEEL posture inherits the solo wheel's global params: `put_delta=0.20`,
`call_delta=0.20`, `target_dte=7`, `take_profit_pct=0.50`. These were never chosen
*for the regimes the router actually wheels in*. The router removed uptrends (→ hold
shares) and quiet downtrends (→ cash), so its WHEEL cells are almost entirely **chop
(any vol)**, **downtrend+stressed**, and **unknown** — i.e. mean-reverting and/or
high-IV conditions. Two of the inherited params are theory-mismatched to that world.

This spec changes exactly **two** params, each by a first-principles argument (not a
backtest search), pre-registers the values, and re-baselines the in-sample A/B once.
It adds **no new strategy engine** — this is a config/selection change inside the
audit-clean wheel engine.

## What this is NOT (non-goals)

- No grid-search / fork-testing (0.10 vs 0.15, 21 vs 30 DTE, etc.) on the burned
  tickers. Values are fixed by reasoning here; the basket run is the judge. Tuning by
  peeking at seen-ticker returns is the overfitting failure mode that killed
  `split-chop` and `conviction-trim`.
- No bear-call-spread / credit-spread engine (shelved; may revisit if the cheap
  low-delta version underdelivers).
- No stop gate, no cash-yield realism, no roll gate, no entry gate. Frozen out.
- No intraday **routing**; routing stays EOD (intraday-TP for the WHEEL leg already
  merged @ `be2bc3d`, orthogonal to this).
- No unseen tickers. Seen four only (SPY GDX SLV XOP). Unseen (XBI EEM EWZ TLT ARKK)
  stay behind `--after-basket-run`; running them spends the wheel's pre-registration.

## Change 1 — `target_dte` 7 → 30 (global, all wheel entries)

**Reasoning (theory, not fit):**
1. **Chop punishes short DTE.** Chop mean-reverts. A 7-DTE put takes a near-random
   weekly snapshot; if the range dips it ITM at that expiry you are assigned, even
   though the range would carry the strike back OTM the following week. ~30 DTE gives
   the oscillation time to recover before expiry → fewer forced assignments.
2. **Stressed vol pays through vega, and 7 DTE has ~none.** A stress spike is an IV
   spike; the edge is selling rich IV and buying it back as IV normalizes. ~30 DTE
   holds vega and captures that melt; a weekly collects the fat premium but misses the
   IV-normalization tailwind.
3. **Theta is 7 DTE's only advantage, and 50% TP already banks it.** A 30-DTE trade
   typically reaches 50% of max profit in ~1–2 weeks, so the steep part of the decay
   curve is captured without holding into weekly gamma/assignment. 30 DTE + 50% TP ≈
   best of both. 7 DTE also incurs ~4× the trades → ~4× commissions/spread-crossing.

**Why global (not regime-conditional):** the chop and chop-stressed cells both point
cleanly to ~30. The downtrend+stressed cell is genuinely ambiguous (vega argues
longer; position-reassessment speed argues shorter; the low delta + 50% TP cushion
the "longer" downside) — no clean single direction. A regime-conditional DTE we cannot
defend from theory is a new degree of freedom and thus overfit surface. Parsimony
wins: one value we can defend everywhere it fires.

**Value:** `target_dte = 30`. Data supports it (SPY chain carries DTE 0–50; the 20–35
band holds ~27% of rows, 35–50 ~13%). 30 targets the standard monthly and is
well-populated; 45 sits at the data's thin edge and is rejected to avoid selection
misses.

## Change 2 — put delta 0.20 → 0.10 in **downtrend+stressed only**

**Reasoning (arithmetic, decisive):** In downtrend+stressed, assignment is likely
regardless of delta (in a real crash even deep-OTM puts are breached). For a put that
ends assigned — sell strike `K`, collect premium `P`, price falls to `S` — the result
is `P − (K − S)`. Comparing a higher-delta put (higher `K`, higher `P`) to a
lower-delta put:

    Δresult = (P_high − P_low) − (K_high − K_low)

Raising the strike by \$1 raises the premium by only `delta` dollars (< \$1 for an OTM
put), so the extra premium **never covers** the extra strike: higher delta is strictly
worse in the assigned branch. The only branch favoring higher delta is *not* being
assigned — rare in a stressed downtrend. Expected value points to **lower delta**. The
basis floor changes the magnitude (extra premium lowers the effective call floor) but
not the direction (a higher strike still leaves shares deeper underwater to recover).

**Value:** `0.10` (deep-OTM, ~"play safe") in the downtrend+stressed cell. **Chop and
chop+stressed (the jackpot cell) stay at 0.20 — untouched.** `call_delta` stays 0.20.
`take_profit_pct` stays 0.50 (already near-optimal: frees collateral, exits the
back-half gamma zone, raises win rate).

## Invariants (protect the deep-audit-clean status)

1. **Off ⇒ byte-identical.** The regime-conditional delta is a new config field
   defaulting to `None` (no override). With it `None` and `target_dte` unchanged, the
   router is byte-identical to v1 — same pattern as the intraday-TP `intraday=None`
   guard. This is the load-bearing regression test.
2. **All-chop ≡ solo-wheel anchor still holds.** In all-chop data the
   downtrend+stressed cell never fires, so the override never applies; both router and
   solo wheel run 0.20 / 30-DTE identically. The anchor is re-pinned at DTE 30.
3. **Fair-fight re-baseline.** The solo-wheel benchmark re-runs at `target_dte=30`
   too (both sides move together). Router-vs-wheel stays an apples-to-apples fight;
   only router-vs-buy-hold is expected to shift.

## Selection-band change (`select.py`)

`derived_band(target_dte)` currently returns `(max(5, target_dte-2), target_dte+3)` —
±2/+3 days, tuned for weeklies. At a 30-DTE target that band is too tight for monthly
spacing: on less-liquid tickers (GDX/SLV/XOP) some days have no in-band expiry →
`select_contract` returns `None` → the router sits in cash unintentionally, polluting
the A/B.

**Reasoned fix:** widen the band so the nearest monthly (~±2 weeks of target) is
reliably in range, while still rejecting expiry stubs. Pre-registered band:
`(max(14, target_dte-10), target_dte+10)`. Rationale, not fit: a fill within ~10 days
of a 30-day target preserves the tenor's character (still "monthly-ish"), and the
`max(14, …)` floor keeps stubs out. The band widens as a function of `target_dte`, so
it governs both the router and the solo-wheel benchmark identically at DTE 30 (both
re-baseline together) — the comparison stays fair.

## Referee (citation gate)

`audit_defense_execution.py --router` re-derives every route + share action + leg and
must exit 0 before any router number is cited. It independently re-derives strike
selection; it must apply the **same** regime-conditional delta rule (downtrend+stressed
→ 0.10, else base) and the widened band, keyed on the strictly-prior-day state exactly
as the engine does. Double-entry: the referee's delta choice per put-open must match
the engine's. `--router` (and `--router --hourly`) exit 0 on all four seen tickers is
the gate.

## Reporting

Re-run `scripts/run_regime_router.py` at the frozen config (DTE 30; downtrend+stressed
delta 0.10; solo-wheel benchmark also DTE 30). Report the new in-sample A/B to
`data/options/reports/regime_router.txt`, dual benchmarks (beat buy-hold AND beat solo
wheel), raw. Add a per-ticker count of downtrend+stressed put-opens (how often the
0.10 override actually fired) so a near-no-op cell is visible, not hidden. Label
everything **in-sample on burned tickers** until the basket run releases fresh data.

## Honesty protocol (binding)

1. This file pre-registers both values **and their reasoning** BEFORE any run. Commit
   it first.
2. Run the frozen config **once**. Report whatever it says, raw. Do not iterate the
   values against the seen-ticker output.
3. Correctness (referee exit 0, byte-identical-when-off, anchor) is provable now;
   **profitability is not** — the real verdict waits on the basket run (owner-owned
   trigger). Seen-ticker deltas are directional evidence only.
4. Recorded ex-ante expectation (for later judging): DTE 30 should reduce forced
   assignments and lift premium-per-trade vs 7 DTE; the 0.10 downtrend+stressed delta
   should reduce assignment depth in crashes at the cost of thinner premium in that
   cell. Kill signal = both changes lower return AND give no drawdown/assignment
   relief on the seen four, or the downtrend+stressed cell is a near-no-op (few
   overrides fire) making Change 2 moot.

## Files touched

- `src/engine_v2/options/regime_router.py` — pick put delta by cell at WHEEL entry
  (downtrend+stressed → override, else base); byte-identical when override `None`.
- `src/engine_v2/options/wheel.py` — `WheelConfig` gains `put_delta_stressed_dt:
  float | None = None` (validated; only the router consumes it).
- `src/engine_v2/options/select.py` — widen `derived_band` per the reasoned band above.
- `scripts/audit_defense_execution.py` — `--router` re-derives with the
  regime-conditional delta + widened band.
- `scripts/run_regime_router.py` — frozen config DTE 30; solo-wheel benchmark DTE 30;
  downtrend+stressed put-open counter in the report.
- `tests/engine_v2/options/test_regime_router.py` (+ intraday module) — byte-identical
  when override `None`; downtrend+stressed entries use 0.10; chop entries use 0.20;
  DTE-30 selection lands in the widened band; anchor re-pinned at DTE 30.

## Open items

- None blocking. The exact widened-band constants (`-10 / +10`, floor 14) are
  reasoned, not fit; if the plan finds a monthly-spacing edge case on a specific
  ticker, adjust by the same "nearest monthly, no stubs" rule, not by return.
