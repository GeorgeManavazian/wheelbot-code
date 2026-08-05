# Yield floor + pre-registered config grid — design

Date: 2026-08-04
Status: design, pre-registration binding once committed
Owner decisions in this document are dated and attributed; nothing here was swept.

## Why this exists

The live paper bot is deployed, repaired and **off**. Before day 1 the owner asked for
three things (2026-08-04):

1. optimise the config,
2. work out whether the "weather" gate picks good tickers, and define what chop means,
3. maximise return per dollar of capital locked — *"why would we sell a 10c put that
   locks up 100k? doesn't make sense at all"*.

The owner then re-sequenced deliberately: **find where the bot loses money first, then
redesign selection last**, because selection cannot be brainstormed without a problem to
aim at. This spec covers the first two steps. Selection redesign is explicitly deferred.

## What we found before designing anything

Measured, not assumed. Evidence for each claim is reproducible from the repo.

### F1 — the minimum-credit floor is blind to collateral

`fills.tp_exit_floor` computes `max(MIN_TICK/(1-tp), 2*friction/(tp*mult))`. At the frozen
live config (TP 0.60, $0.65/contract, mult 100) that is **$0.025, i.e. $2.50 per
contract** — an absolute floor with no knowledge of the strike.

A $1,000-strike put locking **$100,000** clears every existing gate on a $2.50 credit.
The live bot already did a version of this: a put sold for a $0.01 bid against a $0.77
mid, locking 50% of a $5k account for 14 days (audit 2026-07-29, finding 10).

Existing entry guards are `liquidity_ok`, `tp_exit_feasible`, `credit_ok`
(a *maximum* credit filter — rejects deep-ITM puts whose premium is really intrinsic),
and the earnings blackout. **None of them measure return on capital.**

### F2 — yield on collateral rises steeply with delta; skew does not offset it

Measured over 25 tickers, ~39,500 ticker-days, puts with DTE 8–15, 2024-01-15 → 2026-07-01
(`scratchpad/skew.py`, medians):

| target delta | implied vol | annualised yield on collateral |
|---|---|---|
| 0.20 | 28.2% | **18.3%** |
| 0.30 | 27.5% | **30.9%** |
| 0.40 | 27.3% | **45.6%** |

Volatility skew across this range is **0.9 vol points** — negligible. It does not rescue
low delta, as was initially hypothesised. Yield on locked capital roughly **doubles** from
0.20 to 0.40 delta.

This is *premium collected*, not money kept. Higher delta means more assignments. Which
side wins is exactly what the grid decides.

Per-ticker spread at 0.30 delta ranges from XLU 8.4% to TSLA 64.4%, tracking each name's
volatility closely — **the market prices danger proportionally.** A gate on "premium per
unit of gamma" would therefore filter almost nothing, and is not proposed.

### F3 — the strategy has never been backtested

Every backtest to date (`run_chop_gate_ab.py`, `run_portfolio_rotation.py`,
`run_regime_gates.py`) uses `ROTATION_TIE_ORDER` — **9 tickers**. The live bot scans
**~547**. The premise "pick the best few out of hundreds every day" has zero backtest
history. With 9 candidates and 1–5 slots the scanner barely chooses; at 547 it rejects
99% daily and nobody has measured whether that choice adds or subtracts value.

### F4 — data coverage has a sharp cliff

341 EOD option parquets on disk. Complete-coverage sets (≥85% of business days present,
no early cutoff):

| set | tickers | window |
|---|---|---|
| wide | **165** | 2024-01-15 → 2026-07-01 (30 months) |
| deep | 11 | 2017 → 2026 (9 years) |

There is nothing in between: 320 of 341 pulls began 2023-12/2024-01, and 176 stopped early
during the July ThetaData throttling incident. **SPY itself is excluded** from the 165 — its
chain has a hole after 2026-04-07; VOO and IVV cover the exposure.

The 30-month window contains real stress: SPY max drawdown **−18.8%**, worst 11-day move
**−13.5%**, QQQ **−22.8%**. Enough to force assignment, covered-call selection and the
basis floor to execute — all three of which have run **zero** times in live paper.

### F6 — 13 of the 165 carry corporate-action discontinuities; owner dropped them

An unadjusted split re-grids the whole strike ladder and makes any position held across it
fiction — silently, with no error. `DEFAULT_CLEAN_START` fences five such dates, but only
for the old 9-ticker set.

**This was already swept once.** Repair-plan item A10e (2026-08-02) scanned all 341 chains:
86 band hits → **26 fossils** (splits, reverse splits, spinoffs, 2 symbol-reuses) vs ~45
real crashes, separated by a ladder-overlap discriminator, plus 13 multi-week pull-gap
drifts filed separately as data quality. **The resulting `verdicts.csv` was not persisted** —
the plan states it must be regenerated before any expansion run. Only the five 9-ticker
fences survived into code.

An independent re-scan of the 165 over this spec's window (overnight underlying ratio
outside 0.74–1.35) flags 15 discontinuities across 13 tickers:

```
APH 2024-06-12 · AVGO 2024-07-15 · DD 2025-11-04 & 2026-06-25 · DOC 2024-03-05
GPRO 2025-07-22 & 2025-08-25 · INTC 2024-08-02 · ORCL 2025-09-10 · SLV 2026-01-30
WMT 2024-02-26 · XLB / XLE / XLK / XLU 2025-12-05
```

13 of 165 is proportionally consistent with A10e's 26 of 341.

**Owner decision 2026-08-04: drop all 13.** Universe becomes **152**. Some of the 13 are
real price moves rather than corporate actions (INTC's −26% earnings crash, SLV, GPRO), so
this discards genuine stress data — accepted deliberately in exchange for not having to
adjudicate each name.

**Still required before the run:** regenerate the A10e sweep over the 152 at its wider
band. This spec's re-scan used a narrower threshold and cannot see modest splits (a 4:3 is
0.75, right on the boundary) or the pull-gap drift class. Anything it flags is dropped too;
the final count may fall below 152 and the runner must print the list it actually used.

### F5 — selection optimises risk and ignores price *(deferred, recorded here)*

The router ranks candidates by **realized** volatility percentile, highest first, and the
weather gate rejects anything above the 75th percentile. Natenberg ch.3 pp.72–74: sell
premium when **implied** volatility is high relative to its own two-year range; selling
into low relative vol is *"a low-probability trade that immediately puts the odds against
you."*

Realized vol measures risk borne. Implied vol measures price received. The bot measures
the first and never the second, though every parquet carries an `iv` column.

Supporting theory: for a Black-Scholes option, `theta ≈ ½·gamma·S²·σ²`, so
`theta/gamma ≈ ½·S²·σ²` — **independent of strike and expiry**. Delta and DTE move a seller
*along* a fair trade-off rather than above it; only implied-above-realized moves you above
it. Consequence: **the parameter arms below are expected to land close together, with
friction as the clearest separator.** That would itself be an informative result rather
than a failed run — it would say the knobs are not where the money is.

**Deferred by owner decision.** Step 4, after this diagnostic.

## Non-goals

- **`cash_yield` stays 0.** Settled owner ruling, restated 2026-08-04. Idle collateral
  earns nothing, in backtests and in live reporting. No hurdle rate anywhere in this spec
  is anchored to a cash or risk-free rate.
- **No changes to the four rules.** Nothing found challenges the strategy itself; the
  findings are about which stocks and at what price.
- **No change to `call_min_strike="basis"`.** It implements Natenberg's "effective purchase
  price when assigned = strike − premium" (p.821) exactly. Leave it.
- **No change to spread crossing.** Entries at bid, exits at ask, both sides against us.
  Stricter than Chan's midprice-in/market-out convention (p.155); keep it.
- **No gamma-scalping, no underlying hedging.** Different strategy, different bot.
- **No selection redesign in this spec.**

---

## Part 1 — minimum yield-on-collateral gate

### The rule

Before opening a new short put, compute what it pays for the cash it locks:

```
annualised_yield = (credit / strike) × (365 / dte)
```

where `credit` is the bid actually received (entries book at bid) and `dte` is calendar
days to the selected contract's expiry. Refuse the entry if this is below
`cfg.min_ann_yield_on_collateral`.

### Why this needs no backtest

It is arithmetic, not a hypothesis. The owner's example — $10 of credit against $100,000
of locked collateral over 11 days — annualises to **0.33%**, against a measured median of
~31% for a normal 0.30-delta 11-day put (F2). That trade is roughly **100× below** the
ordinary opportunity on the same board. No amount of data changes that ranking.

### Threshold

**`min_ann_yield_on_collateral = 0.08`** (8% annualised) in the live/frozen config.

Justified from the observed yield distribution alone (F2), not from any cash rate:
8% sits *below* the least generous legitimate name measured (XLU, 8.4% at 0.30 delta), so
it rejects effectively nothing a human would knowingly trade, while killing every trade of
the kind in F1 by two orders of magnitude. It is a **junk filter, not a tuning knob** —
raising it into the 12–20% range becomes a real strategy choice and is out of scope here.

### Implementation

A new predicate in `src/engine_v2/options/fills.py`, mirroring `credit_ok` term for term:

```python
def yield_ok(credit, strike, dte, cfg):
    """(allowed, reason) for a NEW short-put entry under the collateral-yield floor.

    A put that pays a negligible fraction of the cash it locks is not a premium
    trade -- it is an interest-free loan to the counterparty wearing a premium
    costume. Mirrors credit_ok's shape: floor unset (None) -> always allowed, so
    the default path stays byte-identical.
    """
    floor = getattr(cfg, "min_ann_yield_on_collateral", None)
    if floor is None:
        return True, ""
    if strike <= 0:
        return False, "bad_strike"
    if dte <= 0:
        return False, "bad_dte"
    if (credit / strike) * (365.0 / dte) < floor:
        return False, "yield_below_floor"
    return True, ""
```

New config field on `WheelConfig`, defaulting off, grouped with the other opt-in gates:

```python
min_ann_yield_on_collateral: float | None = None  # reject entry if (credit/strike)*(365/dte) < this
```

Call sites — both, matching where `credit_ok` is already called:

- `portfolio.py:417` region, in the routing-entry loop
- `wheel.py:371` region, in the solo-wheel entry path

Placement is **after `select_contract`**, so the real expiry (not `target_dte`) sets `dte`,
exactly as the earnings blackout does for the same reason.

Behaviour on rejection follows the established A2/A3 convention **exactly**:

- **Veto, never substitute.** Do not search for a different strike or expiry that clears
  the floor. A gated ticker is skipped for the day.
- **Never silent.** Emit warning `(d, "entry_gated_low_yield", tk)`, deduped — the
  `n_slots` while-loop revisits gated tickers on every iteration.
- Applies to **new short-put entries only**. Never to closes, expiry, marks, roll
  destinations, or covered calls.

### Live config

`live/run_daily.FROZEN` sets `min_ann_yield_on_collateral=0.08`. The backtest baseline arm
sets the same value so live and baseline describe one strategy.

### Tests (red first)

1. `yield_ok` returns `(True, "")` when the floor is unset — pins the byte-identical
   default path.
2. Owner's case: credit 0.10, strike 1000, dte 11 → 0.33% → rejected at floor 0.08.
3. Normal case: credit 2.00, strike 222, dte 11 → ~29.9% → allowed at floor 0.08.
4. Boundary: a credit landing exactly on the floor is **allowed** (strict `<` rejects).
5. `strike <= 0` and `dte <= 0` are refused rather than divided by.
6. Portfolio integration: a chain whose only candidate is below the floor produces zero
   entries **and** an `entry_gated_low_yield` warning, deduped to one per (day, ticker).
7. Solo-wheel integration: same, via `wheel.py`.
8. Regression: the existing plain-path digest test stays byte-identical with the floor
   unset.

Mutation check per the standing plan gates: flipping `<` to `<=` in `yield_ok` must fail
test 4; deleting the `portfolio.py` call site must fail test 6.

---

## Part 2 — the pre-registered diagnostic grid

### Binding pre-registration

This grid is **frozen when this document is committed**. All arms are reported raw, in
full, whatever they show. No arm is re-run with adjusted parameters, no arm is dropped for
being unflattering, and no kill rule is pre-committed — the owner judges, consistent with
every prior wheel pre-registration.

### Fixed across all arms

```
universe      152 tickers (the complete-coverage set F4, minus 13 corporate-action
              casualties, F6) -- list frozen in the runner
window        2024-01-15 -> 2026-07-01
capital       $100,000
call_delta    0.50
call_min_strike "basis"
cash_yield    0
selector      "chop"
n_slots       5
chop_max_fast_fall  0.01          (the live down-only tactical gate)
friction      $0.65/contract + fees_per_contract 0.05, cross the spread both sides
liq_max_rel_spread  0.10
min_ann_yield_on_collateral  0.08   (Part 1, on in every strategy arm)
```

**Two declared divergences from the live frozen config**, both forced by the data rather
than chosen:

1. **The liquidity gate runs on one leg, not three.** Live sets
   `liq_min_open_interest=250` and `liq_min_volume=25`, but the EOD parquets carry no
   open-interest or volume columns — only `bid/ask/mid/close/delta/iv/underlying`. Backtest
   configs therefore leave both legs `None` and apply the relative-spread leg alone. This
   is the same declared divergence the liquidity gate already documents in `wheel.py`.
   **Consequence: the grid's fills are more permissive than live.** The audit found this
   gate would have refused 114 of 145 real entries, so the gap is not small — every arm's
   trade count should be read as an upper bound.
2. **`earnings_blackout` is off**, matching live: the gate is built but is not in
   `run_daily.FROZEN`. Enabling it is a separate owner decision, not part of this grid.

### Baseline

`put_delta 0.30, target_dte 11, take_profit 0.60` — the live frozen config plus the new
yield floor.

### Arms: one axis at a time

A full factorial (3 deltas × 4 DTEs × 3 TPs = 36) is a sweep, and a sweep over a single
30-month sample is where overfitting lives. Each arm below varies **one** axis from the
baseline, so each answers one question. Interactions are checked later, on survivors only.

| # | arm | axis | rationale |
|---|---|---|---|
| 1 | baseline 0.30 / 11 / 60% | — | the live strategy |
| 2 | put_delta 0.20 | delta | owner's stated instinct; safest, lowest yield (F2) |
| 3 | put_delta 0.40 | delta | Natenberg's stated ceiling (p.324, \|delta\| ≤ 0.40); highest yield |
| 4 | target_dte 7 | DTE | fastest theta per day, highest friction bill |
| 5 | target_dte 21 | DTE | midpoint between the live 11 and the conventional 30 |
| 6 | target_dte 30 | DTE | conventional wheel tenor; lowest friction and gamma |
| 7 | take_profit 0.50 | TP | fastest capital turnover |
| 8 | take_profit 0.80 | TP | Natenberg's stated number (p.769) |
| 9 | buy-and-hold, equal-weight 165 | benchmark | the owner's standing judging standard |
| 10 | buy-and-hold SPY (VOO proxy) | benchmark | SPY chain is holed after 2026-04; VOO stands in |

**No engine changes are required by this grid.** Every parameter arm is an existing
`WheelConfig` field; the only new field in this spec is Part 1's yield floor. Nothing in
the selection path is touched.

### Removed by owner decision, 2026-08-04

Two control arms were designed and then cut: **weather gate OFF** (bypass
`is_good_renting_weather`) and **no selection** (rank by fixed universe order instead of
volatility percentile). They would have needed two new engine switches (`chop_gate`,
`rank_by`); dropping them keeps the engine untouched.

Recorded so step 4 knows what it does *not* have: **this grid measures no counterfactual
for the weather gate or the ranking.** Every arm here runs the current selection logic, so
nothing in the results can say whether that logic helps, hurts, or does nothing. When
selection is revisited, that evidence still has to be produced — either by adding these
arms back or by some other means.

### Per-arm output

Headline: total return, CAGR, Sharpe, max drawdown, campaigns opened, flat days.

Plus, because a table of totals cannot answer "where does it lose":

- **friction paid** (dollars, and as % of premium collected) — Chan p.155: spread cost
  scales with round trips, so this must be visible per arm, not buried
- **median annualised yield on collateral** at entry
- **median days locked** per closed trade
- **capital utilisation** — % of days with all slots filled
- gate counters already emitted: `entry_gated_illiquid`, `entry_gated_unclosable`,
  `entry_gated_intrinsic`, `entry_gated_earnings`, plus the new `entry_gated_low_yield`

### Per-trade attribution (all arms)

One row per closed campaign leg:

```
ticker, entry_date, exit_date, exit_type {tp | expired | assigned | called_away},
strike, credit, dte_target, dte_actual, filled_delta, iv_at_entry,
weather_state_at_entry {trend, vol_pctile, fast_spread},
collateral_locked, days_locked, annualised_yield_at_entry,
premium_kept, friction_paid, campaign_pnl
```

`iv_at_entry` is carried even though nothing consumes it yet — it is the raw material for
step 4 (F5), and collecting it now costs nothing.

Sorting this table by `campaign_pnl` ascending is the deliverable the owner asked for:
**where the bot loses money.**

### Runner

`scripts/run_config_grid.py`, following the `run_chop_gate_ab.py` pattern. Chains for 165
tickers total ~283MB on disk; load once and share across arms. **Time a single arm before
launching the rest** — if a full arm is slow enough to make the grid impractical, report
the measurement and re-scope with the owner rather than silently trimming arms.

`run_portfolio_wheel` refuses tickers outside `ROTATION_TIE_ORDER` unless an explicit
`universe` is passed; the runner passes the frozen 165 explicitly. `RESERVED_TICKERS`
(XBI EEM EWZ TLT ARKK) were reserved for a pre-registered basket run that never happened
and are members of the 165 — **owner decision needed** on whether that reservation still
binds (see open questions).

### Known limits, stated up front

- **One sample, one 30-month window, one drawdown.** Everything here is in-sample for the
  window. It is a diagnostic, not proof.
- **Corporate actions: 13 tickers dropped, and the sweep must be regenerated.** See F6.
- **The 165 are a data-collection artifact**, not a random sample — they are the tickers
  whose pulls completed. That correlates with chain size and liquidity, and it is why SPY
  is absent. Do not read the universe as representative of the live 547.
- **Backtest fills are EOD quote-based**; the live bot also takes intraday take-profits.
  The two are not the same execution model.

---

## Part 3 — sequencing

1. Yield floor: red-first tests, implement, mutation check, commit. (Part 1)
2. Grid runner: build, time one arm, then run all ten. (Part 2)
3. Read the attribution table together; identify where money is lost.
4. **Then** brainstorm ticker selection against that evidence (F5 is the standing
   hypothesis, not the conclusion).

The bot stays **off** for 1–3, by owner decision 2026-08-04.

## Open questions for the owner

1. **Does the `RESERVED_TICKERS` reservation still bind?** XBI EEM EWZ TLT ARKK were held
   out for a pre-registered basket run that never executed. They sit inside the 165. Either
   include them and retire the reservation, or exclude them and shrink to 160.
2. **`put_delta` 0.30 vs the owner's stated "20 delta"** — unresolved since 2026-08-03. The
   grid settles it on evidence; this spec does not presume the answer.
3. **Add gamma to the attribution row?** Derivable from `iv`/strike/underlying/dte. Cheap,
   but nothing in this spec consumes it.
4. **Do `wheel.py` and `regime_router.py` follow the portfolio engine onto ask-marking?**
   Open since 2026-07-31; unchanged by this spec, but it means the solo and portfolio
   engines still value the same book differently.

## References

- `AUDIT-2026-07-31-full-system.md`, finding 10 (the $0.01-credit trade)
- `2026-08-03-earnings-blackout-design.md` — the veto/warning convention followed here
- Natenberg, *Option Volatility and Pricing*, ch.3 pp.72–74 (relative vol rank), ch.6
  p.324 (delta ceiling, ≤60 days), p.769 (exit at 80%), pp.821–824 (naked puts, basis)
- Chan, *Machine Trading*, ch.5 pp.155–156 (bid-ask economics)
- Coverage measurement: `scratchpad/cov.py`, `scratchpad/cov2.py`, `scratchpad/skew.py`
