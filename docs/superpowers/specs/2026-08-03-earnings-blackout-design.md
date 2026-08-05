# Earnings blackout gate — design

- **Date:** 2026-08-03
- **Status:** pre-registration — **this document freezes the rule BEFORE the A/B is run.** No results exist at the time of writing.
- **Part of:** the chop-scanner wheel's entry path (live paper bot lineage). One boolean, default off, byte-identical plain path — same packaging as the phase-2 regime gates (`2026-07-14-regime-gates-design.md`).
- **Owner decisions locked (2026-08-03):**
  1. Add the earnings blackout. Considered and deferred in the same dialogue: beta, correlation-to-held, IV rank, term-structure slope, put skew.
  2. One gate at a time. This spec covers the blackout only.
  3. Keep/kill: owner judges from a raw A/B. No pre-committed kill rule (standing convention).

## The problem (motivating, not proving)

**The ranking rule is a pre-earnings-name magnet.**

Live config ranks good-to-rent candidates by **vol percentile, highest first**. Implied and realized vol both rise into a scheduled earnings print. So the rule systematically prefers, among calm range-bound names, the ones with a print coming — and pays for that preference with the single largest source of overnight gap-down assignment risk a short put carries.

The wheel's own history says gap risk is what hurts: the stop gate won (it stopped selling panic bottoms), the entry gate died (it stopped entries that were fine). This gate is in the first category — it blocks a **scheduled discontinuity**, not a scary-looking tape.

Base rate: ~90-day earnings cycle against an 11-day hold means ~12% of *random* entries straddle a print. A vol-percentile-ranked selection should run materially above that. **Measuring the actual rate is the first output of the A/B and is itself a reportable finding**, independent of any P&L effect.

**Honesty caveat:** the above is a mechanism argument, not evidence. Zero live assignments have occurred (221 trades, 0 assignments as of 2026-07-31), so the loss branch this gate targets has never executed in production. The backtest A/B is the only validation available.

## The economic principle (one sentence)

**A scheduled earnings print inside the option's life is a discontinuity the premium was not priced to compensate us for at 0.30 delta** — the IV richness that attracted the ranking is the market correctly pricing the gap, not a mispricing to harvest.

## The rule (frozen)

One boolean, `earnings_blackout`, default `False`.

When `True`, a candidate short-put entry is **vetoed** if any known earnings datetime for that ticker falls in the window:

```
[ observation_date , contract_expiry + 1 calendar day ]
```

- **Inclusive both ends.**
- **The `+1` day is the after-market-close (AMC) correction, not a tuning knob.** A print released after the close on day X moves the stock on day X+1. Rather than parse BMO/AMC from the timestamp — a per-vendor convention with its own failure modes — the window is widened by one day, which covers the AMC case unconditionally.
- **Veto, never substitute.** Consistent with the A2 liquidity gate and the A3 unclosable gate: the bot does not go hunting for a different expiry that dodges the print. It skips the name for the day and re-evaluates tomorrow.
- **Puts only.** Covered calls are exempt, for the same reason the A2 gate exempts them: refusing a call leaves assigned shares honestly naked, which is worse. This is a deliberate asymmetry and is *not* the same judgment as saying earnings do not matter for calls.
- **Unknown allows.** A ticker with no earnings data is not blocked — the standing "unknown state always allows" convention from the phase-1/2 regime work.

**No tuning surface.** There is no configurable buffer, no per-ticker override, no DTE interaction. If the rule is wrong it is wrong as one falsifiable statement, which is the point.

## Why the veto is placed after contract selection

The window's right edge is the **actual chosen expiry**, not `target_dte`. Realized DTE varies inside the derived band, so `target_dte` would blur the edge by a few days in both directions.

That places the check alongside the existing `entry_gated_illiquid` / `entry_gated_unclosable` / `entry_gated_intrinsic` vetoes in `portfolio.py`'s routing loop, after `select_contract`.

**Cost, stated:** live, this means a chain is pulled for a name that is then blacked out. A pre-selection check keyed on `target_dte` would save that pull. Precision is worth more than the pull; revisit only if the pull budget becomes binding.

## Visibility (non-negotiable)

A blocked entry emits `entry_gated_earnings` into the same warnings channel as the other three entry vetoes, deduped per `(day, ticker)` because the `n_slots` while-loop revisits gated tickers on every iteration.

**The gate must never be able to fail silently.** Two distinct failure modes, deliberately distinguished:

| Situation | Meaning | Handling |
|---|---|---|
| Ticker has an empty calendar and is a **fund/ETF** | Correct answer is "no earnings" | Normal, not counted as a problem |
| Ticker lookup **failed or was never pulled** | The gate is off for this name and nobody knows | Counted and surfaced per run |

Collapsing these two is exactly the `zombie_check` failure class (2026-07-29 audit): a lookup outage would present as "no name has earnings," the gate would quietly stop existing, and the run would look clean. The cache therefore stores an explicit per-ticker status, not merely a list of dates.

## Data source

**yfinance** `Ticker.get_earnings_dates()`. Verified 2026-08-03: returns 100 rows per name spanning **2002 → forward-scheduled 2026** dates (AAPL 2002-04-17 → 2026-10-29; RIG → 2026-08-05). Covers the full backtest history and the live forward calendar from one call. Requires `lxml` (new dependency; `yfinance` was already required).

**Schwab was checked first and rejected on evidence.** `get_instruments(..., Projection.FUNDAMENTAL)` returns 56 fields including `nextDividendDate`, `declarationDate`, `beta`, `shortIntToFloat` — **and no earnings date of any kind.** The official, already-authenticated feed cannot serve this gate. (Noted for later, unrelated to this spec: `nextDividendDate` is the input an ex-dividend early-assignment guard on the covered-call leg would need, and `beta` is available if the deferred correlation work is revisited.)

### Look-ahead disclosure

Earnings dates are **announced by companies weeks in advance**, so knowing a print is scheduled ~12 days out is information a real-time trader genuinely had. This is the reason the gate is backtestable at all, and it is why the window's right edge sits at ~12 days rather than months.

**Residual bias, stated in full:** yfinance returns the *actual, final* report dates. A company that **rescheduled** its print gives the backtest the corrected date where a real-time trader would have held the superseded one. This is unmeasurable with this data source and is not corrected for. It is judged small — reschedules are uncommon and usually move by days, and the ±1-day window edge absorbs the smallest of them — but it is a real look-ahead and must be repeated wherever these numbers are cited.

A second, milder point: dates far in the past are recorded facts, while the most recent forward date may still be an estimate. Only the live path touches forward estimates; the backtest reads settled history.

## The A/B (how this gets judged)

Run the existing chop-gate A/B harness (`scripts/run_chop_gate_ab.py`) with one added arm: the live frozen config with `earnings_blackout=True` against the identical config with it off. Everything else held fixed.

**Reported raw, no interpretation withheld or added:**

1. **How many entries the gate actually blocked**, and what share of all entries that is. If this is near zero the gate is inert and nothing else matters — this is the primary output.
2. Plain P&L, both arms, per ticker, seen vs unseen groups kept separate per the standing basket-run convention.
3. Assignment count, both arms. **This is the mechanism check**: the gate's whole claim is that it avoids gap-down assignments. If assignments do not fall, the gate did not do what it says it does, whatever happened to P&L.
4. Flat-day count, both arms — the cost side. A gate that blocks entries buys safety with idle capital.

**Prior, recorded before seeing results:** the wheel's history is that entry-side gates lose (entry gate dead, symmetric 9/20 far worse) and only the stop gate won. The honest prior is that **this gate probably does not help P&L**. The reason to run it anyway is that it is the one entry-side rule whose target is a scheduled discontinuity rather than a price pattern, and that assignment-rate effect (#3) is worth knowing regardless of P&L.

**Default outcome is DON'T ADD.** The gate ships default-off and stays off unless the A/B gives the owner a reason.

## Non-goals

- No earnings-based *entry* signal (selling into elevated IV deliberately) — opposite strategy, out of scope permanently.
- No IV rank, term structure, or skew. Those are ranking inputs, tracked separately, and are backtestable only on the ~15 tickers with ThetaData chain history.
- No covered-call earnings rule. See the puts-only note above.
- No ex-dividend guard, despite the data now being known to exist.

## Files

- `src/engine_v2/options/earnings.py` — calendar loading + the window predicate
- `src/engine_v2/options/wheel.py` — `earnings_blackout: bool = False`
- `src/engine_v2/options/portfolio.py` — the veto + `entry_gated_earnings`
- `src/engine_v2/options/market.py`, `live/market_live.py` — `earnings_dates()` on the Market seam, duck-typed via `getattr` exactly as `bounded_settle_price` already is, so no existing test fake breaks
- `scripts/pull_earnings.py` — the cache puller
- `data/earnings/calendar.parquet` — the cache
