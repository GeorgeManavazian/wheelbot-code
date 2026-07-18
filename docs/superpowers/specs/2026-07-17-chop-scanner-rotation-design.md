# Chop-scanner wheel rotation — design

**Date:** 2026-07-17
**Project:** Chop-scanner rotation (new strategy) — `code/etf-bot`
**Status:** BUILT + SHIPPED; **weather definition has since EVOLVED.** This spec's `is_good_renting_weather` is the *base* layer only (50/200 trend + vol). The live bot added a short-horizon **9/20 fast-spread gate** (down-only, `chop_max_fast_fall`) on top, 2026-07-18 — see vault `10 Live Paper Bot/How the weather works` and `docs/superpowers/AUDIT-live-bot-additions-2026-07-18.md`. Read those for the current rule; this doc is the original design.
**Depends on / consumes:** the portfolio rotation engine (`src/engine_v2/options/portfolio.py`, `run_portfolio_wheel`), the regime state module (`src/engine_v2/regime/state.py`, `regime/data.py`), the plain+basis wheel mechanics (`wheel.py`, `select.py`), the rotation runner (`scripts/run_portfolio_rotation.py`).

## Problem

The wheel earns its keep in **chop** (range-bound weather) and gets run over in trends — the single-ticker evidence is decisive: on XOP (a chopper) the WHEEL posture produced 110% of the router's profit, while on the trending names (GDX, SLV) a hold-day made ~12× a wheel-day. So instead of babysitting one underlying for nine years and sitting through its trending stretches, **scan a universe and always deploy the plain+basis wheel on whatever is in good renting weather right now; when a ticker stops being good to rent, move new capital to the next one.**

A rotation engine already exists (`run_portfolio_wheel`, pre-registered spec 2026-07-14) and was already run in-sample on the four seen tickers. It **lost**: rotated +126.1% vs a solo equal-weight wheel +133% (and buy-hold SPY +193%). The cause is diagnostic, not fatal: it ranks candidates by **`vol_pctile` descending — fattest premium** — which selects high-volatility names, and high vol is usually a *trend or a crash*, not chop. It rented out burning buildings. This project replaces that selection brain with a chop signal: rent the range-bound names, not the volatile ones.

## Non-goals

- New wheel mechanics. Plain + basis floor only — no rolling, no stop-loss, no regime gates, no early close. `run_portfolio_wheel` already refuses these; that stays.
- Config sweeps. One frozen config for this test (below). The dashboard is where the owner sweeps delta/TP/DTE later — out of scope here.
- Touching the reserved five (XBI EEM EWZ TLT ARKK). Those are the *wheel/router* project's pre-registered one-shot; this strategy never runs on them (see Honesty guards). This strategy's held-out is a separate fresh Schwab pull.
- Live/paper trading and scanning hundreds of tickers. Future vision, explicitly out of scope. This spec is the backtest edge test only.
- The intraday/hourly path. EOD fills only, matching the existing rotation engine.

## Frozen config (this test)

`put_delta=0.20, call_delta=0.50, take_profit_pct=0.50, target_dte=7, call_min_strike="basis"`, `starting_capital=100_000`, friction `$0.65/contract + cross-spread`, `cash_yield=0`.

- **Call delta 0.50** (not the project-standard 0.20): the strategy's aim is to *not hold shares* — recycle capital back to fresh chop fast. A near-the-money call is called away quickly whenever the shares are at/above basis, freeing the slot. The basis floor still overrides delta while underwater (never sells a call below net basis), so 0.50 speeds exits without risking a loss-locking call. Cost accepted by owner: capped upside on recoveries + more churn (fees/spread paid often). The friction model surfaces that churn honestly.
- Because call delta differs from the standard, the **solo-wheel baseline is re-run at 0.50 call delta** so the A/B compares matched configs (see Testing). Buy-hold is config-independent.

## The chop signal ("good to rent")

A new predicate `is_good_renting_weather(row) -> bool` in `regime/`, the only genuinely new logic. It reads a single regime-state row (the same rows `regime_series` already produces: `trend ∈ {uptrend, downtrend, chop}`, `vol ∈ {calm, normal, stressed}`, plus `vol_pctile`, `px_vs_200`, …).

**Default definition (frozen for this test):**

> good to rent = `trend == "chop"` AND `vol != "stressed"`

Rationale, per regime construction (`state.py`: `uptrend = c>sma200 & sma50>sma200`; `downtrend = c<sma200 & sma50<sma200`; `chop` = neither):

- **Exclude `uptrend`** — the "I'd rather just hold" case; selling puts caps upside, leave it and rent elsewhere.
- **Exclude `downtrend`** — the falling knife; assignment into something still dropping. This **subsumes and strengthens** the old `is_unpaid_decline` gate (which only excluded downtrend+calm/normal) — now *all* downtrends are excluded.
- **Keep `chop`** — price oscillating around flat MAs; puts expire worthless or assign-then-recover. The wheel sweet spot.
- **Exclude `stressed` vol** — chop with violent vol is a whipsaw range, not calm renting.

**Ranking among the eligible:** when a slot opens, rank all currently-good-to-rent tickers by **`vol_pctile` descending** — juiciest premium *within the safe set*. This is the fix: "juicy" can no longer drag selection into a trend, because only chop names are eligible to be ranked.

`is_good_renting_weather` is the **weather-threshold knob**. The default above is frozen for this test; the dashboard later can loosen/tighten it (include uptrend, allow stressed, add a tight `|px_vs_200| < X%` "really flat" band). The predicate is a pure function of one state row so the dashboard and referee can both call it.

## Architecture

Extend `run_portfolio_wheel`; do not rewrite. Three surgical changes, everything else frozen and already audited:

1. **Selection signal** — make the selector an explicit parameter (`selector ∈ {"vol_pctile", "chop"}`, default `"chop"`). `"vol_pctile"` retains today's exact ranking (`pct = row["vol_pctile"]`, unchanged); `"chop"` filters to `is_good_renting_weather` then ranks survivors by `vol_pctile` desc. Keeping the old selector reachable is what makes the byte-identical anchor well-defined — the anchor runs `selector="vol_pctile"`.
2. **Concurrency `N`** — generalize the single held `pos` to a **list of up to `N` concurrent campaigns** sharing the cash pool. `N` is a new config field. `N=1` + the *old* signal reproduces today's engine byte-identically (regression anchor).
3. **"Weather turned bad" = skip in entry ranking** — a held ticker that drops out of good-to-rent simply stops being eligible for *new* puts. Any open short put rides plain rules to expiry/assignment; assigned shares ride the basis floor until called away. No early close, no dump (that would be a stop-loss).

New code boundaries:
- **`is_good_renting_weather`** → `src/engine_v2/regime/` (a weather signal; reusable by the dashboard + referee).
- **The engine change** → `portfolio.py`.
- **New A/B arms** → `scripts/run_portfolio_rotation.py`.
- Untouched: the solo wheel, the Chameleon router, the intraday path.

## N-basket mechanics

- **Manage all held positions each day** with the plain+basis rules, independently (TP → expiry → covered call at 0.50 delta / basis floor). No interaction between campaigns.
- **Fill empty slots after management:** if `len(held) < N` and cash is available, rank good-to-rent tickers, **skip any ticker already held** (one campaign per ticker), open puts on the best until `N` filled or cash exhausted.
- **Capital per entry = `available_cash // empty_slots`** — equal-weight by construction and self-balancing (N=5, all empty, $100k → $20k, then $80k/4=$20k, …). A closing campaign returns its capital to the pool; the freed slot redeploys to the current best.
- **N=1** = one slot, all cash = today's engine exactly.

## Honesty guards

- **Look-ahead:** selection reads **strictly prior-day** state (`_row_before` already enforces state as-of a day `< d`); `is_good_renting_weather` reads that same prior-day row. Point-in-time.
- **Cross-sectional discipline:** ranking uses only prior-day info for every candidate; **universe membership is fixed ex-ante** (no survivorship — never add a ticker because it did well).
- **Friction:** `$0.65/contract + cross-spread` modeled (load-bearing at 0.50 call delta, which churns).
- **Reserved five untouched:** XBI EEM EWZ TLT ARKK belong to the wheel/router one-shot. This strategy never runs on them. Its own held-out is the fresh Schwab pull.
- **Referee citation gate:** before any rotation number is quoted, a referee independently re-derives chop eligibility + ranking + each entry from the raw states + chains and must agree — same discipline as the `--router` referee. No citation until it exits clean.

## Data plan (two phases)

- **Phase 1 — in-sample development (burns nothing new):** universe = the **9 tickers on disk except the reserved five**: SPY GDX SLV XOP + AAPL AMZN NVDA META FB. Nine names give the scanner something real to rotate across and mix ETFs with single stocks. All exploratory/in-sample. Build, validate mechanics, first read on whether chop-rotation beats the baselines.
- **Phase 2 — edge test (the real verdict):** owner pulls ~50 tickers / ~1yr via the Schwab developer account; freeze the rules; run **once** on that fresh universe. This is the genuine held-out for this strategy. Out of scope for the Phase-1 implementation plan; noted here so the design carries it.

## Testing

- **Byte-identical anchor:** `run_portfolio_wheel` at `N=1, selector="vol_pctile"` equals today's committed output on the four seen tickers (proves the positions-list refactor preserves single-slot behavior; keeps the existing pre-registered number reproducible).
- **`is_good_renting_weather` unit:** chop+normal → True, chop+calm → True, chop+stressed → False, uptrend → False, downtrend → False.
- **Capital sizing unit:** N=5, all empty, $100k → each entry $20k; a freed slot redeploys.
- **One-campaign-per-ticker unit:** never two open positions on the same root.
- **Look-ahead guard:** a constructed test proving day-`d` selection uses only state strictly before `d`; extend the project's existing no-look-ahead check to the rotation path.
- **A/B run (in-sample, 9 tickers, raw, EOD):** rotation at **N=1** and **N=5**, vs **solo equal-weight wheel re-run at 0.50 call delta**, vs **buy-hold** — both baselines side by side, raw, owner judges, no pre-committed kill rule.
- **Referee `--rotation`:** re-derives eligibility/ranking/entries independently; exit 0 required before citing.

## Files touched

- `src/engine_v2/regime/` — new `is_good_renting_weather(row) -> bool` (+ its unit test).
- `src/engine_v2/options/portfolio.py` — chop-gated selection, `N` concurrency (positions list + `cash//empty_slots` sizing), config field for `N` and the weather predicate.
- `scripts/run_portfolio_rotation.py` — chop-signal arms at N=1 / N=5, matched-config solo baseline, 9-ticker universe.
- `scripts/audit_defense_execution.py` (or a rotation referee) — `--rotation` re-derivation citation gate.
- tests — the units above + the byte-identical anchor + the look-ahead guard.

## Open items

None blocking. Phase 2 (Schwab pull + one-shot edge test) is deliberately deferred to its own cycle after Phase 1 reports.
