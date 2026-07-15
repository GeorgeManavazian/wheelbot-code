# Conviction Trim (Chameleon v1.1) — design

- **Date:** 2026-07-15
- **Status:** approved in design dialogue 2026-07-15; becomes the pre-registration when committed (rules frozen before any test)
- **Project:** Chameleon (vault `09 Chameleon`), the regime router. Code in `code/etf-bot`, module `src/engine_v2/options/regime_router.py`.
- **Clean revert point:** git tag `router-v1-clean` (@ 8d54da9) — `git reset --hard router-v1-clean` restores pre-trim state. Work happens on a branch; main untouched until owner merges.

## Motivating evidence (in-sample, honest)

Scour of the four seen tickers (2026-07-15), forward-21d returns from every uptrend day, pooled and per-ticker:

- **"Strength" does not separate edge.** Bucketing uptrend days by distance above the 200d line, by the 50d–200d gap, or by drawdown gives a FLAT expected return across quartiles (~+1.1% each). The original "size up on strong trends" premise is unsupported — weak-looking uptrends pay as well as strong ones.
- **Extremes carry the same return but a fatter tail.** The most-extended quartile (furthest above 200d, top vol) shows unchanged mean return with a distinctly worse 5th-percentile outcome (−15% to −16% vs −10%). Rubber-band risk: no extra reward for the stretch, more snap-back.
- **Vol-targeting's premise is weak here.** corr(vol_pctile, fwd21) ≈ 0 on GDX/SLV (+0.28 XOP) — higher vol does not predict lower return, so inverse-vol sizing would be a Sharpe play at best.

Conclusion the evidence forces: the only defensible sizing move is **tail control at the extreme**, not return-chasing by strength. This spec implements exactly that and nothing more.

## The rule (fixed, one new constant)

```
TRIM_FRACTION = 0.5   # documented constant, deliberately round/unfished
```

On a **trend HOLD entry** (`BUY_SHARES`, i.e. flat cash + strictly-prior-day cell == TREND), if the strictly-prior-day vol state is **"stressed"** (the phase-1 threshold `vol_pctile > 0.75`, frozen since the regime-advisor spec, never tuned for this purpose):

- buy `lots = (cash // (spot * mult)) // 2` instead of the full `cash // (spot * mult)`.
- the remaining cash stays idle (cash-secured, no leverage). Everything else about the entry is unchanged.

Otherwise (uptrend + calm/normal vol): full size, exactly as today.

**Scope, deliberately narrow:**
- Applies ONLY to the trend HOLD entry. Wheel put entries (cash-secured, basis-floor-governed) are untouched — the tail evidence is about holds, and puts carry a different risk profile.
- Sizing is decided at ENTRY only, matching every other router decision. No mid-hold re-trim (avoids new transitions and whipsaw). A half-size hold that later converts to wheel shares (chop) or is force-sold (downtrend) runs the existing paths unchanged, on the smaller position.
- Trigger reuses the existing frozen `stressed` state; the only genuinely new number is `TRIM_FRACTION = 0.5`.

## Config + wiring

New field on `WheelConfig`: `conviction_trim: bool = False`. Default off → the router is byte-identical to today when off (the all-chop anchor test still holds; all-chop never enters a TREND cell so the branch is inert there regardless). Validation: `conviction_trim` requires the router path (it is meaningless to solo `run_wheel`, which has no TREND posture) — documented, not enforced by the solo engine.

`RouterResult` additions: `n_trimmed_entries: int` (count of half-size `BUY_SHARES`), `days_half_size: int` (days holding a trimmed trend position). The report surfaces both.

Integer edge case: if the full position is 1 lot, `1 // 2 == 0` → no shares bought that day (documented: a single-lot trimmed entry sits in cash and re-evaluates next day, same as any other "nothing eligible" day). At $100k this only bites near very high share prices; recorded, not special-cased.

## Testing

Add to `tests/engine_v2/options/test_regime_router.py`:
- `test_trim_off_is_byte_identical`: full router run, `conviction_trim=False` vs the existing default — identical trades/equity (guards the flag's default-off invariance on a real chain).
- `test_stressed_uptrend_entry_is_half_size`: uptrend + stressed prior-day state → `BUY_SHARES` lots == full_lots // 2; remaining cash correct.
- `test_calm_uptrend_entry_is_full_size`: uptrend + calm/normal → full lots (trim does not fire).
- `test_trim_only_touches_trend_not_wheel`: stressed-vol WHEEL cell (downtrend+stressed) → put entry sized exactly as untrimmed (wheel untouched).
- `test_trimmed_hold_converts_and_sells_at_half`: half-size hold → chop converts the half position to wheel shares; → downtrend sells the half position. Downstream paths operate on the smaller size, no error.
- `test_single_lot_trim_buys_nothing`: full=1 lot, stressed → 0 bought, counted as a cash day.
- `test_no_lookahead_trim`: same-day stressed flip on entry day is ignored (strictly-prior rule).
- Coverage folded into the engine_v2 gate (≥95%).

## Referee

Extend `audit_defense_execution.py --router`: when `conviction_trim` is on, every `BUY_SHARES` on a derived stressed-uptrend day must be half the full cash-sizable lots (re-derived locally from cash-at-that-point and spot); every `BUY_SHARES` on a calm/normal uptrend day must be full size. Double-entry both directions (a full-size buy on a stressed day, or a half-size buy on a calm day, is a mismatch). Exit 0 required before citing any trimmed-router result.

## A/B protocol (pre-registered)

Runner `scripts/run_regime_router.py` gains a trimmed arm. Per seen ticker (SPY GDX SLV XOP; XOP from 2020-07-01), report **three** lines: router (no trim), router + conviction_trim, and the two existing benchmarks (buy-hold, solo wheel+basis). Metrics per arm: P&L, total, CAGR, Sharpe, **max drawdown (the headline)**, per-year, plus `n_trimmed_entries` / `days_half_size`.

**Expectations recorded ex-ante:** trimming stressed-vol holds to half will most likely **lower total return slightly** (stressed uptrends had positive expected return, so giving up half of them costs some upside) while **lowering max drawdown** (the fat left tail at the extreme is what we're cutting). 

- **Success:** materially lower max drawdown for a small return give-up (better risk-adjusted ride).
- **Kill:** lower return AND no drawdown improvement — the trim bought nothing.
- Owner judges, no pre-committed kill rule (standing choice). Everything in-sample on burned tickers; real validation waits on the wheel's basket run releasing fresh data.

## Out of scope

- Sizing by trend "strength" (unsupported by the evidence above).
- Vol-targeting / continuous inverse-vol sizing (weak premise here; separate spec if ever).
- Trimming wheel entries, leverage / sizing ABOVE all-in, mid-hold re-sizing, per-regime fractions other than the single 0.5.
- Any new threshold (the stressed trigger is inherited, not defined here).

## References

- Chameleon router: `2026-07-14-regime-router-design.md`. Regime states: `2026-07-13-regime-advisor-design.md` (source of the frozen `stressed` threshold).
- Scour evidence: session 2026-07-15 (forward-return buckets by conviction metric; recorded here, regenerable from `regime_series` + closes).
