# Full-system adversarial audit — live paper wheel bot

**Date:** 2026-07-31
**Baseline:** commit `4cffd72` (plus working-tree fixes that became `7022ade`)
**Method:** nine independent auditors, one per domain, read-only, run in parallel with no
knowledge of each other's work.
**Standard of evidence imposed on every auditor:** a finding is only reportable if you RAN
something that demonstrates it. Reading code is not evidence. A passing test is not proof —
one existing test was found encoding a bug as if it were the spec, and nine deliberate
mutations survived the full 630-test suite.

This document is the durable record. The conversation that produced it is not.

---

## Verdict in one paragraph

The bot's **bookkeeping is exact** and there is **no look-ahead** — the two things that would
have been fatal and unfixable. Everything else is wrong in ways that are fixable but
substantial: 100% of the reported profit rests on an unvalidated fill assumption, two of seven
days in the equity curve are fabricated, the 25 "independent" accounts are an effective sample
of 2.09, half the strategy has never executed, and roughly a dozen failure modes are silent by
construction. **The existing track record cannot be repaired, only discarded.**

---

## What came back CLEAN (independently recomputed, not taken on trust)

These matter as much as the defects. Each was verified by reconstruction from raw data, not by
reading code.

- **The cash ledger is exact.** All 221 trades replayed from starting capital reconcile to
  **zero residual** in all 25 accounts; every one of the 259 `cash_after` values matches an
  independent recomputation to floating-point exactness. Commissions ($0.65/contract, both
  legs) confirmed over 4,497 contract-sides. Sizing is exactly `floor(budget / (strike*100))`
  at all 145 entries. No account ever over-committed collateral (peak utilisation 99.6%),
  proven both empirically over 200 account-days and algebraically.
- **There is no look-ahead.** `vol_pctile` is a trailing 756-day rolling rank, not a
  whole-history percentile. Proven twice: prefix-stability across 40 tickers (0 mismatches),
  and an independent brute-force reimplementation written from the docstring that matched
  **bit-exact over 1,884 days on six tickers, every column**. Then the live picks were rebuilt
  from independent yfinance data: **all 20 picks fall in the prior-day gate set, zero in the
  same-day-only set**, and seven picks would have been *rejected* by a same-day gate — the
  affirmative signature of a bot acting on stale information.
- **Every fill crosses the spread against the bot.** Entries at bid, exits at ask, confirmed by
  independent cash reconstruction across all 259 events with zero mismatches.
- **The basis floor is provably safe.** `floor = basis − premium/shares` is true net basis; a
  call-away at any strike ≥ floor yields campaign net ≥ 0 identically. Randomized sweep: 40
  paths, 73 assignments, 42 call-aways, **zero campaigns netting negative**.
- **Campaign continuity and cash arithmetic through a full lifecycle** (SELL_PUT → ASSIGNED →
  SELL_CALL → CLOSE_CALL → SELL_CALL → CALLED_AWAY) are exact to 1e-9.
- **No cross-account contamination from stepping.** 25 accounts through one shared market:
  chain fingerprints identical before and after, and results identical under reversed and
  shuffled account order. (The one real coupling was the splice — see A-LEAK below, fixed.)
- **Infrastructure:** `state.json` survived 25 real ENOSPC writes with zero corruption; the
  2026-07-29 GitHub-PAT leak fix genuinely works (forced `TimeoutExpired` with a canary token —
  scrubbed); ET gating arithmetic correct at all 13 boundaries including both DST transitions;
  memory peaks at 105MB against 954MB.

---

## GROUP A — Defects that change what the bot DOES

| ID | Severity | Defect |
|---|---|---|
| A1 | CRITICAL | Intraday take-profit fills at the same quote snapshot that triggered it — zero latency, unlimited size |
| A2 | CRITICAL | No liquidity check of any kind; Schwab's OI/volume/size fields are never read |
| A3 | HIGH | No minimum-credit or maximum-spread guard |
| A4 | HIGH | Covered-call basis floor is unreachable inside the 12-strike window |
| A5 | HIGH | Same-day re-entry guard cannot see intraday closes |
| A6 | HIGH | Intraday path blind to a 0.00 bid — the state a decayed put ends in |
| A7 | MEDIUM | Intraday manager has no holiday calendar |
| A8 | MEDIUM | Early assignment is unrepresentable |
| A9 | MEDIUM | Covered call sold in the same session as the assignment |
| A10 | HIGH | Zero corporate-action handling |
| A11 | MEDIUM | `run_daily` has no clock gate of its own |
| A12 | MEDIUM | Equal-split budget strands capital at small account sizes |
| A13 | LOW | Only broker commission modelled |
| A14 | HIGH | Unsettleable expiry becomes a permanent silent zombie; warnings have no consumer |
| A15 | MEDIUM | The 2026-07-31 expiry fix regressed the batch engine's reach-back |

### A1 — 100% of realized P&L rests on one unvalidated fill assumption
`live/intraday.py:38-44`. The manager polls ~78×/day and fills at the **first** snapshot below
threshold, at that same snapshot's ask — a running-minimum selection with no latency, no size
limit, no partial fills.

- 114 intraday closes, **0 EOD closes**. Realized P&L **$81,608.60**, all of it from this path.
- **18% of closes were within one cent** of never triggering (21 of 114 at ≤$0.01 margin).
- +$0.01 on every exit → −$2,208 (−2.7%). Half the leg's own entry spread → **−$24,826 (−30%)**.
  Full spread → **−$49,652 (−61%)**.
- Entry spreads are not small: median $0.18, **median 30% of mid**, max $1.70.
- Running-minimum bias measured on real hourly prints for the bot's own 0.30Δ/11-DTE picks:
  day's last print sits **11% above the day's minimum on average** (p90 29–32%). Applied to the
  114 real exits: −$5,065 (−6%); at p90, −$14,733 (−18%). Hourly bars only — the live manager
  samples 13× finer, so this is a **lower bound**.
- Concentration: top 5 contracts = 91.6% of realized P&L; two DOW contracts = 55%.

**Fix:** fill at the *next* poll's ask, not the triggering one; add configurable slippage ≥ half
the quoted spread; report slippage-adjusted P&L alongside raw.

### A2 — Unlimited size at top-of-book
`portfolio.py:173`, `live/intraday.py:39`. `n = budget // (strike*100)`, no liquidity check.
`live/data.py:56-75` extracts only strike/right/dte/delta/bid/ask/mark — Schwab returns
`openInterest`, `totalVolume`, `bidSize`, `askSize` and none are read.

- Largest single fill: **181 contracts** of DOW 28.0P ($506,800 collateral).
- Aggregate across accounts in one snapshot: **757 contracts of DOW 28.0P across 20 accounts**;
  702 of DOW 29.0P across 22.
- Real hourly volume in the strike the selector picks: GDX median **11/hour** (day median 222).
  757 contracts exceeds the entire traded volume of 97.7% of hours.
- **57.7% of realized P&L ($47,127) came from single fills >25 contracts.**

**Fix:** pull OI/volume/sizes in `chain_from_json`; reject below a floor; cap `n` at a fraction
of quoted size or recent ADV; model size-dependent slippage.

### A3 — No minimum-credit or maximum-spread guard, producing unclosable positions
`live/data.py:66-68`, `portfolio.py:169-174`.

Real trade, `5k_N2`, 2026-07-24: **WBD 25.0P sold for a $0.01 bid against a $0.77 mid**
(implied ask $1.53). Gross $1.00, commission $0.65, **net $0.35**, against **$2,500 collateral —
49.5% of the account** — and an instant $152 mark-to-market loss.

**It cannot be closed.** TP threshold = 0.4 × $0.01 = **$0.004**, below the $0.01 minimum tick,
so `mark.ask <= threshold` can never be satisfied. Three RIG legs (credit $0.03, trigger
$0.012) need a locked $0.01×$0.01 market. **All four are still open.**

Also: 11 entries with bid ≤ $0.10 locked $39,350 of collateral for $271 of premium. DECK was
sold at 35% of mid across 10 accounts — $6,750 of instant mark loss.

**Fix:** reject `bid < min_credit`, `(ask−bid)/mid > max_spread_frac`, or `0.4*bid < 0.02`
(unclosable by construction).

### A4 — The covered-call leg is unreachable after a real drawdown
`live/data.py:78,87` (`strike_count=12`), `portfolio.py:142-147`.

The 12-strike window is 6 below and 6 above spot — verified against the real Schwab fixture,
reaching **+3.8% above spot**. The basis floor sits *above* spot after the drop that caused the
assignment. Replayed over real full-width chains (51 tickers, 4,411 ticker-days):

| post-assignment drawdown | full chain writes a call | 12-strike window | blocked |
|---|---|---|---|
| 2% | 71.0% | 70.9% | 0.1% |
| 5% | 59.8% | 44.6% | 15.2% |
| **10%** | **36.3%** | **5.3%** | **31.0%** |
| 15% | 15.1% | 1.0% | 14.1% |

Driven through the real `LiveMarket` on the real fixture: assigned at 74 → call written;
assigned at 77, 80 or 90 → `select_contract` returns `None`, **zero calls, forever**. The
income half of the wheel never starts, the slot is dead until the stock recovers, and the only
trace is `days_shares_uncovered`, which **has no reader anywhere**.

**Fix:** widen `strike_count` for CALL-phase holdings, or a second bounded pull centred on the
basis floor. Alert when `select_contract` returns None for a CALL-phase position with a floor.

### A5 — Same-day churn: the anti-churn guard cannot see intraday closes
`portfolio.py:84,101,171` vs `live/intraday.py:45`. `closed_today` is built fresh inside
`step_one_day` and populated only by the EOD branch. Since 114/114 closes are intraday, the
guard is defeated for 100% of this bot's exits.

Reproduced end to end with the real code: closed GDX 68.0P at 10:15 for $0.37, EOD run at 17:00
**re-sold the identical contract for $0.94** — position unchanged, **phantom $779 booked**, and
the TP threshold resets against the new higher credit so it can be harvested again. Zero live
instances so far, by luck (07-23 and 07-27 pulled no chains; other days the router ranked
elsewhere).

**Fix:** persist intraday closes in `PortfolioState` (e.g. `closed_on: {date: [contracts]}`);
`manage_intraday` writes, `step_one_day` seeds `closed_today` from it.

### A6 — Intraday blind to a zero bid
`live/marks.py:145` requires `bid > 0 and ask > 0`; `live/held_legs.py:71` (the 07-31 EOD fix)
correctly requires only `ask > 0`. The unfixed path is the one producing 100% of realized P&L.

Reproduced: market 0.00 × 0.42 against a $3.20 trigger → `contract_quotes` returns `{}`,
`manage_intraday` books nothing, position stays open. **45% of the bot's own picks decay to
ask ≤ $0.05 before expiry** (DOW 31%, WMT 48%, AGNC 84%).

**Fix:** align `contract_quotes` with `rows_from_quotes` — require `ask > 0`, allow `bid >= 0`.

### A7 — Intraday runs on market holidays
`live/run_intraday.py:27-30`. `market_is_open` returns **True** for 2026-07-03, 09-07, 11-26 and
12-25. Driven on Thanksgiving with a stale quote it booked `CLOSE_PUT @ 0.39`. `quoteTime` is
never checked anywhere in `live/marks.py`. ~9 weekday holidays/yr; a trade dated on a day the
exchange never opened is unreconcilable against any statement.

### A8 — Early assignment unrepresentable
Both engines resolve only at `d >= c.expiry`. American puts are exercisable any day. Measured
over 5,169 campaign-entries the bot's own selector would open: **20% reach a day where the
holder is strictly better off exercising early**; 31% finish ITM.

### A9 — Covered call sold in the same session as the assignment
`portfolio.py:119-124` then `:138-152`, one pass. Assignment notice arrives after Friday's
close; shares are not deliverable until Monday. Banks an extra session of premium at Friday
prices, once per campaign.

### A10 — Zero corporate-action handling
No split/dividend/delisting code exists (`grep` finds only comments). Schwab price history is
split-adjusted; stored `strike`, `shares`, `basis` are not.

Simulated 2:1 split under 9× XYZ 100P with the stock at 104 → adjusted close 52.50: the bot
booked **ASSIGNED 900 shares @ $100 with the stock at $52.50**, a fabricated **$42,750** hole.
Reality: OCC restates to 18 contracts @ 50, the put expires worthless. With shares already held,
a 2:1 split mis-marks equity by the split ratio (15,000 vs a true 25,000) and pushes the floor
to ~2× the stock price, making A4 permanent.

### A11 — No clock gate inside `run_daily`
Confirmed the 2026-07-24 event: `catchup-2026-07-24.log` timestamped **04:13 ET** booked every
07-24 entry from 07-23's closing quotes. `is_trading_day` blocks pre-market but returns True
from 09:31 onward, so a **mid-session run still books a full paper day**. The only real gate is
`HM >= 1700` in the shell wrapper, outside the program.

### A12 — Equal-split budget strands capital at small sizes
`portfolio.py:152`, `:183`. $5k with N=5 → $1,000/slot → any strike >$10 sizes to `n=0`, and the
`break` abandons remaining slots without reclaiming cash.

| account | mean util | campaigns in 7 sessions | idle cash |
|---|---|---|---|
| `5k_N5` | **18.0%** | **1** | $4,105 |
| `5k_N4` | 31.1% | 2 | $4,108 |
| `100k_N5` | 84.8% | 11 | $5,071 |

The capital×N grid is not measuring N at $5k — it is measuring which slots are big enough to
buy anything, which confounds the comparison the grid exists to make.

### A13 — Only broker commission modelled
4,497 contracts × $0.65 = $2,923 modelled. Exchange/OCC/regulatory ≈ **$1,349 unmodelled (1.7%
of realized P&L)**. Assignment/exercise fees ($5–25/event) also unmodelled.

### A14 — Unsettleable expiry becomes a permanent zombie
Introduced by the 2026-07-31 settlement fix. Correctly refuses to settle without the expiry
day's own close — but there is **no recovery path and no consumer of the warning**.
`StepResult.warnings` has **zero readers** in `live/` or `dashboard/`. Driven 30 sessions with
the bar never returning: warned 30/30, leg still open, slot still consumed, nothing reported.
On a `500k_N3` account that is $166k frozen invisibly.

### A15 — The same fix regressed the batch engine
`settle_price`'s on-or-before reach-back is now dead code. A BATCH chain missing rows on an
expiry date now strands the leg where it previously settled at the prior close. No test covers
it. Live behaviour is correct; batch needs a bounded fallback.

---

## GROUP B — Defects that change what the bot SEES

| ID | Severity | Defect |
|---|---|---|
| B1 | CRITICAL | Zero and negative closes reach the regime computation — 18 tickers, every run |
| B2 | CRITICAL | An HTTP-200 empty payload (or ≥51% stale) is recorded as a market holiday |
| B3 | MEDIUM | One malformed contract discards the entire ticker's chain |
| B4 | HIGH | `zombie_check` still blind to a total held-book failure |
| B5 | HIGH | A held ticker not in `UNIVERSE` is completely invisible |
| B6 | HIGH | WBA removed as delisted, silently re-added; universe never re-vetted |
| B7 | MEDIUM | `chain_frame` derives its date window from the UTC box clock |
| B8 | MEDIUM | Truncated history silently excludes a ticker forever |
| B9 | MEDIUM | `underlyingPrice` guard accepts 0 and negative |
| B10 | LOW | Adjusted/non-standard options taken blind, priced as 100-lots |
| B11 | MEDIUM | The strike window shifts put entry to a higher delta, always toward more risk |

### B1 — Bad prices are reaching the regime computation right now
`live/data.py:20` applies no validation; `regime/state.py:25` drops NaN but keeps 0 and
negatives; `:32` computes `log(c/c.shift(1))`.

Production logs, **byte-identical across six runs on six different days**:
`ZNNNNZNNZNNNNNZZNN` — where `Z` = `log(0)` (zero close) and `N` = `log(negative)`, verified by
driving the real function. **5 tickers carry a zero close and 13 a negative close** — 18 of 542
(3.3%), every single run, and neither `skipped_closes` nor any alert mentions them.

One zero print, measured on real GDX history:
- **22 consecutive regime rows silently deleted** (`rv` NaN for 21 sessions → `vol_pctile` NaN →
  `dropna`), so `_row_before` serves a frozen row for ~14 days then `None`, and the ticker
  vanishes from routing.
- `fast_spread` goes −0.461% → **−6.963%** on the bad day (down-only gate slams shut), then
  flips to a fake rally **+8.849%** on days 9–19.
- `50d/200d` stays contaminated for **199 further sessions**.
- Up to **27 gate decisions flipped** over the next 320 sessions; trend label changed on 11.

**Fix:** drop non-positive/non-finite closes in `closes_from_json`, record `(ticker, date,
value)`, log once per run, and alert if a bad print lands inside the trailing 200 sessions.

### B2 — Empty payload read as a holiday, day destroyed silently
Schwab's documented empty response (`{"empty": true, "candles": []}`) is neither an exception
nor a skip: the ticker lands in `_closes` with a zero-length series, `zombie_check` sees
`skipped_closes == 0`, `is_trading_day` sees 0/N → False, `main()` returns 0, the tick writes
`.dailyran`, and `health.py:58` skips that date forever.

| stale share | skipped | zombie_check | is_trading_day | outcome |
|---|---|---|---|---|
| 49% | 0 | False | True | trades normally on stale data |
| **51%** | 0 | False | False | **"not a trading session" → exit 0, marker written, NO gap, NO alert** |
| 100% | 0 | False | False | same |

This is the 2026-07-24 outcome through a door both new guards were built to close.

### B3 — One malformed contract discards 168
`live/data.py:69-74`: `float(ct["strikePrice"])` and `int(ct["daysToExpiration"])` are unguarded
(unlike delta/bid/ask/mark which use `_num`). `strikePrice=None` → TypeError → the whole
ticker's chain is skipped. On a held ticker, that position goes unmarked for the day.

### B4 — `zombie_check` blind doors
`market_live.py:49` builds the chain population as `(held | good) & set(self._closes)` — so a
held ticker whose *closes* failed is removed from the chain population entirely and can never
appear as a chain failure.

All of these pass as a healthy run: every held ticker's chain fails; every held ticker's closes
fail; half the universe stale-but-200; 49.9% of closes + 49% of chains both fail. End-to-end
with GDX held and its close pull failing: `skipped_closes 1/547 (0.18%)`, chains 0/0,
`zombie_check → False`, `is_trading_day → True`, and the entire real book unmarked.

**Fix:** judge **held legs** as a third, separately-gated population. It is small, known in
advance, and 100% of it is required for correctness.

### B5 — A held ticker outside `UNIVERSE` is invisible
`market_live.py:35-54` iterates only `self._universe` for closes and intersects chains with
`set(self._closes)`. A held ticker outside it gets no closes, no chain, **no skip, no alert** —
`spot` returns the caller's fallback, `settle_price` returns None, the position freezes
permanently. Reachable: the 07-18 expansion **dropped 17 tickers**; the 07-31 reserved-ticker
exclusion dropped 5 more.

### B6 — A delisting fix was silently reverted
`ea9a4f4` (07-17) "drop WBA (delisted 2025-08, take-private)". `b153596` (07-18) put it back.
**It is still there.** The file says "re-vet quarterly"; the last content change was the commit
that undid the vetting. `SQ`, `GOLD`, `X` did not come back; WBA did.

### B7 — Box clock in the chain window
`live/data.py:81` uses `dt.date.today()` (UTC on the VPS) while `run_daily.py:169` correctly
uses ET. Any EOD retry after 20:00 ET (EDT) / 19:00 ET (EST) — the exact window the tick was
widened for — sets `from_date = obs + 1`, **excluding every contract expiring on the trading
day** and shifting `daysToExpiration` by one.

### B8–B11
Truncated history (`len(c) <= 200`) makes a ticker permanently ineligible with zero signal.
`underlyingPrice` guard is `if und is None`, so 0.0 and −1.0 pass. `contracts[0]` takes adjusted
options blind — a `multiplier=10` contract priced as a 100-lot ($10.35 booked vs $0.45 real,
$7,000 collateral reserved vs $700 real obligation) — and its OCC root (`GDX1`) is
unreproducible by `occ_symbol`, so `merge_held_legs` could never quote it either. The 12-strike
window picks a different strike than the full chain on **3.24% of ticker-days, with the window
delta HIGHER (more assignment risk) 100% of the time** (mean error 0.046, max 0.196).

---

## GROUP C — Defects in what the OWNER SEES

| ID | Severity | Defect |
|---|---|---|
| C1 | CRITICAL | The equity curve is not a mark-to-market — frozen marks |
| C2 | CRITICAL | The gap banner asserts the opposite of the truth |
| C3 | HIGH | `append_correction`'s output is structurally undisplayable |
| C4 | HIGH | Every printed Sharpe is statistically indistinguishable from zero |
| C5 | HIGH | Win rate is guaranteed 100%, and wrong in the other direction too |
| C6 | HIGH | No buy-and-hold benchmark anywhere |
| C7 | HIGH | The 25-account grid presents as 25 results; effective N is 2.09 |
| C8 | MEDIUM | The two dashboard pages disagree by $8,330 |
| C9 | CRITICAL | The dashboard omits share value whenever a covered call is open |
| C10 | MEDIUM | Max drawdown measured from the first snapshot, not from capital |
| C11 | MEDIUM | The mark basis changed mid-series with nothing recording it |
| C12 | LOW | The duplicate 2026-07-24 row is never deduplicated |
| C13 | HIGH | `append_gap` raises TypeError on the partial-failure path |
| C14 | HIGH | The gaps fold silently deletes a distinct real record |
| C15 | MEDIUM | Date-format drift defeats the correction fold |

### C1 — The equity curve is not a mark-to-market
Per-leg mark history across all seven snapshots:

| leg | 07-20 | 07-22 | 07-24 | 07-27 | 07-28 | 07-29 | 07-30 | frozen |
|---|---|---|---|---|---|---|---|---|
| TMO@512.5 | 11.65 | 11.30 | 11.30 | 11.30 | 11.30 | 11.30 | 11.30 | **5/6** |
| RIG@4.5 | 0.05 | 0.05 | 0.06 | 0.06 | 0.06 | 0.06 | 0.06 | 5/6 |

Fraction of the short book carrying a copied mark: **07-27 = 100%**, 07-29 = 67.8%,
07-30 = 18.9%. TMO's underlying moved **526.46 → 576.77 (+9.6%)** while its put stayed pinned.
The reported +$23,110 move on 07-27 contains **zero** price information. A frozen leg
contributes zero variance while cash contributes drift — this is the root of the Sharpe values.

### C2 — The banner asserts the opposite of the truth
`dashboard/monitor.py:146-149` renders: *"These days were never traded and cannot be
backfilled."*

- **2026-07-23 is labelled `no_run`** — and it is the day the DOW campaign was closed for
  **$25,616, 30.3% of all realized P&L** (22 intraday closes, `intraday-2026-07-23.log`).
- **2026-07-24 is labelled a missed day** — and it is the day the WMT campaign was *opened* in
  19 of 25 accounts off 3–6 day old prices, producing **$12,805, 15.2%**.

Together **45.5% of realized P&L was booked on days the banner says were never traded.**

### C3–C5, C10 — the statistics
`append_correction` works but `gap_banner` interpolates only `count` and `dates`; the corrected
reason is computed and discarded — **0% reaches the owner**, and there were zero production
callers.

Sharpe: **11.39 from 7 observations, SE 6.7**, all 25 confidence intervals straddle zero, every
p ≥ 0.091. No risk-free rate subtracted (it is an information ratio mislabelled). `√252` scaling
assumes one bar per session on a curve with three sessions missing. Deduplicating 07-24 alone
moves 500k_N1 from 11.39 → 12.78; removing both blackout days → 22.57.

Win rate: 114 closes, **0 expiries, 0 assignments** — the loss branch has never been sampled.
Separately `_win_rate` counts `ASSIGNED` as a permanent loss even where the basis floor
guarantees the campaign nets ≥ 0.

Max drawdown starts at the first snapshot, which is already **−0.333% below capital**, hiding
the day-one mark loss. Restoring it moves aggregate Sharpe **9.75 → 6.28**.

### C6–C7 — benchmark and independence
No benchmark code exists in `live/` or `dashboard/`. Computed independently for 07-20 → 07-30:
strategy **+1.596%**, SPY **−0.054%**, equal-weight of the 15 names actually traded **+0.561%**.
But the cross-sectional sd of those 15 names' 10-day returns is **5.36pp** — a 1.6pp result is
**0.30 sd of one name's noise**.

The grid: 145 SELL_PUTs = **20 distinct (date, ticker, strike, expiry) decisions**, average
replication **7.2×**. Mean pairwise correlation 0.458 → **effective independent accounts =
2.09**. Four distinct entry days, 15 underlyings of 542. **DOW = 55.4% of realized P&L**; the
top-ranked N=1 accounts are 79–100% DOW.

Also: headline Return measures from starting capital while the curve, drawdown and Sharpe
measure from the first snapshot — **+1.258% vs +1.596%**, unlabelled.

### C9 — The dashboard will hide the stock on the first assignment
`dashboard/monitor.py:85-115`: `equity_positions += spot * sh` sits only in the *bare shares*
branch. A position with shares **and** a short call takes the `if short:` branch, subtracts the
liability, and never adds the stock.

Measured: dashboard $85,010 vs engine $100,010 — **−$15,000, exactly shares × spot**. The
rendered row also shows `Contracts: 1, Right: C`, so **the 100 shares are invisible on the
page**. Latent only because nothing has been assigned; fires on day one of the first assignment.
`test_dashboard_calc.py::test_assigned_shares_use_spot` sets `"short": None`, so the
shares-and-short combination is untested.

### C13–C15 — the gap ledger
`run_daily.py:290` calls `append_gap(f"{day}#accounts", ..., date=day)` → **TypeError: got
multiple values for argument 'date'**. The partial-failure branch is the only place a
per-account hole is disclosed, and **it throws before writing** — at the end of `main()`, after
all accounts stepped, so a successful day exits with a traceback.

The fold (`latest[str(r["date"])] = r`) is applied to *all* records, not just corrections: two
different real failure modes on one date → the earlier one **silently vanishes**. And it matches
date strings exactly, so `"2026-07-24"` vs `"2026-07-24 00:00:00"` vs `"2026-07-24T00:00:00"`
all miss — inverting the intent, leaving the wrong reason displayed and the day counted twice.

---

## GROUP D — Defects in whether it RUNS AT ALL

| ID | Severity | Defect |
|---|---|---|
| D1 | CRITICAL | No dead-man's switch for the intraday manager; it has already died 4× silently |
| D2 | CRITICAL | A stale-but-successful feed is classified as a holiday and stamped complete |
| D3 | CRITICAL | Every alert failure is discarded; the suppression marker is written anyway |
| D4 | HIGH | An outage longer than the 10-day lookback returns "ok" and records nothing |
| D5 | HIGH | Two of fourteen Schwab login times give ZERO warning before expiry |
| D6 | HIGH | The health check is last in a linear script under `set -u` |
| D7 | HIGH | The timer has no wall-clock anchor; `Persistent=true` is inert |
| D8 | HIGH | `secret_guard` misses 9 of 12 credential shapes and silently skips files >2MB |
| D9 | MEDIUM | The tick always exits 0, so systemd can never see a bad run |
| D10 | MEDIUM | Intraday sync failure discarded; EOD marker written before sync |
| D11 | MEDIUM | `.prev` is one *save* deep (minutes), not one *day* |
| D12 | MEDIUM | Missed-day email names the wrong date and a deadline wrong by 3.5h |
| D13 | LOW | No `.gitignore` in the synced tree; orphaned `.tmp` files get force-pushed |
| D14 | — | **No external heartbeat** — the meta-fix that subsumes D1–D4, D6, D7 |

### D1 — The intraday manager is unwatched, and has already failed silently four times
`wheelbot_tick.sh:78` detects trouble with a case-sensitive `grep -q "ERROR"`, matching only the
per-account handler at `run_intraday.py:67`. Any failure outside that loop prints a traceback
containing no literal `ERROR`, and **the exit code is never checked**.

Exercised through the real script: hard crash rc=1 → no alert, no marker, tick exits 0.
Python missing rc=127 → same. TP booked but sync fails → same.

From the real logs — **it stopped mid-session on four days with no alert anywhere**:

| date | ticks | last | expected |
|---|---|---|---|
| 2026-07-22 | 6 | **14:00** | ~75 |
| 2026-07-23 | 40 | **12:55** | ~75 |
| 2026-07-24 | 41 | **13:09** | ~75 |
| 2026-07-27 | 55 | **15:42** | ~75 |

`grep -c intraday live/health.py live/run_health.py` → **0 0**. Since this manager produces
100% of realized P&L, it can be dead for weeks while the EOD run succeeds and the dashboard
looks green.

### D3 — Alert failures are discarded, and the "already alerted" marker is written anyway
`send_alert` returns False on six distinct config faults (missing file, empty file, JSON list,
missing key, non-numeric port, chmod 000). All seven production call sites ignore the return
value; `run_notify.py` exits **0 regardless**; `wheelbot_tick.sh` uses that 0 to write the
one-shot suppression marker. There is **no liveness check on the alerting path at all**. One
revoked Gmail app password silences the entire notification system with no indication.

### D5 — The Schwab nag blanks the weekend
`WARN_AT_DAYS = 5.5` on a 7-day token, but the nag is gated to `DOW ≤ 5 && HM ≥ 1700`. A Monday
10:00 login reaches 5.5 days on Saturday ~22:00; Sat/Sun are gated out; the next eligible tick
is Monday 17:00 — **7 hours after the token is already dead**. Best case across all 14 login
times is 36h runway; two cases are **zero**.

### D7 — The timer has no wall-clock anchor
`OnBootSec=2min`, `OnUnitActiveSec=5min`, `Persistent=true`, **no `OnCalendar=`**. Per
`systemd.timer(5)`, `Persistent=` only has effect with `OnCalendar=`, so it is decoration.
`systemctl stop` leaves NO next trigger — **hit live during this session's deploy** — and
recovery is a manual `systemctl start wheelbot.service`. Measured phase slip: mean period
5.18–6.85 min, so every window is entered by luck; the 23:45–23:59 health window got only **2**
opportunities on 07-29.

### D8 — `secret_guard` misses 9 of 12 credential shapes
Missed: Schwab access token, Schwab `app_key`, `Bearer` header in a traceback, SMTP password,
a line-wrapped PAT, an encrypted private key, AWS/OCI keys, a PAT in a >2MB log, a PAT in a
binary blob. The size skip is a bare `continue` with no log line, and `dashboard.log` is already
at **72.5% of the 2,000,000-byte ceiling**. `_scrub` knows the literal live token but
`secret_guard` does not — it only pattern-matches.

### D14 — The pattern behind all of it
Every individual guard works. What fails is the **space between** guards: `zombie_check` and
`is_trading_day` each work and a stale feed falls between them; the EOD dead-man's switch works
and the intraday engine has none; the alerts are well written and nothing checks whether they
are alive. **The system that reports the failure lives inside the system that failed.** One
external heartbeat — something off the VPS expecting a daily "I stepped N accounts, closed M
legs, pushed at T" — subsumes D1, D2, D3, D4, D6 and D7.

---

## GROUP E — The tests are weaker than they look

**Nine deliberate mutations survived the full 630-test suite:**

| # | file:line | mutation | suite |
|---|---|---|---|
| 1 | `portfolio.py:225` | assigned-share book value × 0.99 | 476 backtest |
| 2 | `portfolio.py:225` | assigned-share book value × **0.5** | 476 backtest |
| 3 | `portfolio.py:226` | subtract $1 from equity whenever shares held | 476 backtest |
| 4 | `held_legs.py:69` | delete the `bid < 0` rejection | 139 live |
| 5 | `held_legs.py:74-76` | ignore Schwab's `daysToExpiration` | 139 live |
| 6 | `held_legs.py:112-114` | hardcode `unquoted = []` | 139 live |
| 7 | `held_legs.py:42` | reduce the dedup key to `(ticker,)` | 139 live |
| 8 | `held_legs.py:80` | hardcode `delta: None` on every spliced row | 139 live |
| 9 | `dashboard/monitor.py:66-67` | revert the live pull from ask to mid | 139 live |

**E1.** Mutation 2 is the headline: **halving the value of every assigned share is undetectable.**
**E2.** The 2026-07-31 equivalence-test rewrite is the cause for #1–#3. `assert (port.equity <=
solo.equity).all()` plus `.max() > 0` says only "portfolio equity is somewhere below solo, by
some amount, at least once" — any bug that lowers it, by any magnitude, on any subset of days,
passes. The prior `equals()` would have killed all three instantly.
**E3.** The dashboard's live-quote path is untested (#9).
**E4.** `--smoke` holds zero positions, so it cannot reach the merge, the splice, `add_chain_rows`
or the print/alert branches at all.
**E5.** Tests that assert on fixtures rather than behaviour:
`test_held_contracts_dedupes_the_same_leg_across_accounts` (built around two *different*
tickers, so the strike/expiry/right components of the key have zero coverage) and
`test_row_built_for_a_leg_outside_the_chain_window`.
**E6.** `live/tests/test_paths.py::test_all_modules_follow_the_override` passes only because it
calls `importlib.reload()` — something no production code does. **The test proves the opposite
of its name.**

---

## Fixed and deployed on the day (commits `4cffd72`, `7022ade`)

1. Take-profit going blind on strikes outside the 12-strike window (`live/held_legs.py`).
2. Marking the short book at mid instead of ask (owner decision B).
3. Reserved out-of-sample tickers live and tradeable in the 547-name universe.
4. Mark-only spliced rows selectable as new entries across accounts (**introduced same day**).
5. Expiry settling against a carried price.
6. An empty-but-not-None chain receiving a synthetic one-row chain.
7. A spliced row with a null delta crashing `select_contract` for every account on that ticker.
8. `gaps.jsonl`: 07-27 filed, 07-24 re-filed via a new non-destructive correction mechanism.

---

## Process findings

**P1.** An auditor left an injected mutation (`equity_val ... - (1.0 if shares_val else 0.0)`)
in the working tree and reverted an unrelated production fix **twice** by restoring
`portfolio.py` from its own stale copy. Caught only by diffing every changed line against HEAD
before deploy. **Never run concurrent writers on one tree.**

**P2.** The splice defect (A-LEAK, item 4 above) was introduced because its safety was argued by
reasoning — "the holder skips that ticker, so it is safe" — which is true for the holder and
false for the other 24 accounts. A fresh auditor disproved it with a ten-line script.
**Reasoning is not evidence.**

**P3.** Two equivalence tests were weakened to make them pass after a deliberate behaviour
change. That single act is the direct cause of surviving mutations #1–#3. **A test that fails
is either a bug or a design decision; it is never a thing to loosen.**
