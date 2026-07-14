# Portfolio rotation (regime-routed wheel) — design

- **Date:** 2026-07-14
- **Status:** approved in design dialogue; **this document freezes the rules before any test runs**
- **Owner decisions locked:** shape A (regime-routed rotation, one shared pool, no leverage); routing rule = richest premium first (vol percentile desc, fixed tie order); basis floor always on; seen tickers only until the basket run reports.

## Problem statement (measured, 2026-07-14)

Solo basis-floor wheels hold true idle cash only 0–2% of days — but capital is *routed blindly*: when a campaign ends, the bot can only re-enter its own ticker, whatever that ticker's regime. Rotation gives the freed cash a choice. Expectation recorded ex-ante: this improves premium capture density and deployment, and does NOT close the buy-hold gap (which is upside surrender, not idleness).

## The strategy (all rules fixed, zero knobs)

One shared pool (default $100k), universe = the seen set **SPY GDX SLV XOP**, one campaign at a time (all-in sizing, unchanged from solo). Frozen basket config: 20Δ both legs, target 7 DTE, 50% TP, `call_min_strike="basis"`, commissions/spreads as ever. EOD fills in v1 (comparable to the full-history EOD baselines); no intraday in v1.

**Daily loop (union calendar of all chains):**
1. A held position (short option or siege shares) is managed exactly by the solo rules on its own ticker: TP → expiry resolution; covered calls under the basis floor; sieges wait (siege exit stays dead).
2. Flat with cash → **entry routing**:
   - **Eligible** = ticker has chain data today AND today ≥ its clean-start date AND strictly-prior-day ticker state is NOT unpaid decline (downtrend + calm/normal) AND a contract is selectable with a valid mark and n ≥ 1.
   - Unknown/stale/warmup state: **allows** (never gate on missing information), logged `(date, "route_state_unknown", ticker)`.
   - **Ranking:** eligible tickers by **vol percentile, descending** (strictly-prior-day state row — get paid the most to wait); tie → fixed order **SPY > GDX > SLV > XOP**.
   - Enter the top-ranked ticker all-in. Log `(date, "route", chosen, [(ticker, vol_pctile), ...])` for the referee.
3. No entries otherwise; a day flat with no eligible ticker is a counted flat day (expected to be rarer than solo).

**Constants (documented, never config):** `ROTATION_TIE_ORDER = ("SPY","GDX","SLV","XOP")`; `CLEAN_START = {"XOP": 2020-07-01}` (split-broken chain before that — STATUS item); a ticker whose chain ends (SPY 2026-04) simply leaves the eligible set.

**Information discipline:** all state reads strictly-prior-day with the 14-day staleness bound (same rule as gates/autopsy); vol-percentile ranking uses the same prior-day row. No look-ahead, property-tested.

## Module layout

New `src/engine_v2/options/portfolio.py` — `run_portfolio_wheel(chains: dict, cfg, regime_states: dict, clean_start: dict|None) -> PortfolioResult`. Reuses `select_contract`, `option_mark`, `sell_proceeds`/`buy_cost`, `is_unpaid_decline`, `_state_before` from existing modules; duplicates NO decision logic beyond the loop itself. `PortfolioResult`: equity, trades (contracts carry roots), final cash/shares-by-ticker, warnings, route_events, days_flat, days_shares_uncovered. Single-position invariant enforced.

Intraday marks are keyed (expiry, strike, right) in the solo engine — cross-ticker collision risk is why v1 is EOD-only; hourly needs per-ticker keying and its own spec amendment.

## Testing (TDD)

- **Solo-equivalence regression (the strong one):** portfolio with universe = {one ticker} produces byte-identical trades/equity to `run_wheel` at the same config.
- Synthetic 2-ticker chains: routing picks higher vol pctile; tie → fixed order; unpaid-decline ticker skipped; unknown state allows + warns; clean-start excludes; one campaign at a time (no second entry while position held); basis floor active per ticker; no-look-ahead (state flips on entry day don't count).
- Validation: unknown universe ticker → error; missing states for a universe member → error.
- Coverage: engine_v2 gate ≥95%.

## Referee (execution audit)

`audit_defense_execution.py` gains `--portfolio`: re-derives, with the audit's OWN state lookup (`_asof`/`_unpaid`) and its own eligibility/ranking walk, every route decision (eligible set, ranking, chosen ticker) and every leg's termination (TP/expiry) per ticker. Route chosen ≠ re-derived top → mismatch; entry on an ineligible day → mismatch. Exit 0 required before any result is cited.

## A/B protocol (pre-registered)

- **Baselines:** the four solo basis wheels, full history EOD ($100k each — already measured: SPY +106.1k, GDX +155.6k, SLV +134.1k, XOP(2020-07+) +136.5k) and their equal-weight average per dollar.
- **Treatment:** rotated portfolio, $100k, union calendar 2017-01 → data end.
- Report raw: P&L/Sharpe/maxDD, flat days, uncovered days, entries per ticker, route-event counts, premium collected; vs buy-hold SPY and vs equal-weight buy-hold of the four. No aggregation tricks, no config variants, one run.
- **Owner judges; no kill rule** (standing choice). Expectation on record: deployment ↑, premium density ↑; buy-hold gap persists.

## Out of scope

- Leverage / margin / concurrent campaigns (splitting the pool = sizing knobs)
- Unseen tickers (XBI EEM EWZ TLT ARKK QQQ) — nothing here touches the basket
- Hourly fills (v1 EOD; needs per-ticker intraday keying)
- Regime-switched hybrid (hold-in-uptrend) — separate future spec
- Any per-regime parameter variation

## References

- Gates: `2026-07-14-regime-gates-design.md` · Siege exit (falsified, killed): `2026-07-14-regime-siege-exit-design.md` · Basket (binding): `2026-07-12-wheel-multi-ticker-design.md` + 13d
