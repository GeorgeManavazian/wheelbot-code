# Wheel on a basket of ETFs — design

Date: 2026-07-12
Status: pre-registered, awaiting owner review

## The question

Does the wheel work on **choppier, higher-premium underlyings** than SPY?

The SPY wheel result (+58.3% vs SPY buy-hold +193%) is the motivation — but see
"The SPY result is not what we thought it was" below. That number came from a bot
whose expiry selection was statistically indistinguishable from random, so the
prior conclusion is **not established** and must be re-derived before it can be
cited.

## The SPY result is not what we thought it was

`select_strike_by_delta` filters to a DTE window, then ranks **by delta error
alone** — expiry is never a tiebreak. With `dte 25-45`, many (expiry, strike)
pairs sit near 0.30 delta, so the winner is decided by whichever expiry's strike
grid happens to land nearest the target. That is a property of the strike grid,
not a decision.

Measured on the 215 contracts the published SPY run actually sold:

```
DTE at sale:  mean 33.4   median 32   std 6.0   min 25   max 45
uniform-random pick over [25,45] would have std 6.06
```

The bot's expiry choice was **statistically indistinguishable from a die roll.**
The P&L is arithmetically real (real contracts, real prices, reproduced exactly:
final equity $158,267, +58.3%), but it describes *a bot that picks its expiry at
random*, which is nobody's strategy.

**Consequence:** step 1 of this plan is to fix selection and re-baseline SPY. The
claim "the wheel loses to buy-hold SPY" is treated as **unproven** until then.

## Scope

**Basket (9):** GDX, SLV, XOP, XBI, EEM, EWZ, TLT, ARKK, and **QQQ as a control**
that is expected to behave like SPY (a trending index). QQQ is included
specifically so a falsifiable prediction is on the record: if QQQ wins, our
understanding of the strategy is wrong and every other result is suspect.

Tickers were chosen **ex-ante** on structural grounds — high implied vol,
mean-reverting/range-bound asset class *by nature*, liquid options — **not** by
screening for which names realised a choppy 2017-2026. Screening on realised chop
would be look-ahead at the ticker level.

**Excluded:** USO (structural contango decay — the ETF itself bleeds, which would
confound the strategy result), SPY (owner's call; already run).

## Data

| Feed | Endpoint | Carries | Purpose |
|---|---|---|---|
| EOD greeks | `/v3/option/history/greeks/eod` | bid, ask, mid, **delta**, IV, underlying | strike selection + fills + daily marks |
| Hourly OHLC | `/v3/option/history/ohlc` | open/high/low/close/volume/count/vwap — **no greeks, no quotes** | timing the take-profit between daily closes |

Both are required; neither substitutes for the other. Strike selection needs
delta (EOD only); fills need bid/ask (EOD only).

- EOD pull: 2017+, ±30 strikes, 50-day contract window. `scripts/pull_basket.sh`.
- Hourly pull: 7 years (2019-07+, **captures the COVID crash** — the single most
  informative period for a short-vol book), ±15 strikes, 50-day contract window,
  so any DTE in 0-50 is testable after the fact.
  `scripts/pull_intraday_basket.py`, queued behind the EOD pull.
- **Deadline: ThetaData Options STANDARD lapses ~2026-07-25.** Data not pulled by
  then is gone permanently.

**Known data limits, stated up front:**
- Hourly bars are **traded prices, not quotes**. Intraday take-profit fills are
  therefore modelled on trades, which is *optimistic* relative to crossing a
  spread. This must be said in the results, not quietly banked.
- ±15 strikes clips **far-OTM + long-dated** together (a sub-10-delta 45-DTE
  contract may fall outside the band). Not needed for the frozen config.
- **True daily-expiry 0DTE is a SPY/QQQ-only game.** GDX/XOP/XBI/EEM/EWZ/ARKK list
  weekly (Friday) expiries only; SLV/TLT roughly twice-weekly. No pull changes
  this — the contracts were never listed.

## Engine changes (all required before any run)

### 1. Selection: expiry first, then strike

The bug. Replace delta-ranked-over-a-window with a two-stage, deterministic rule:

1. Of the expiries **visible on that date**, take the one nearest `target_dte`.
2. Within that expiry, take the strike whose |delta| is nearest `target_delta`.

Guards (derived from `target_dte` by one fixed rule, **identical for every ticker,
never tuned**):

```
floor   = max(5, target_dte - 2)     # refuse expiry stubs (a risk preference)
ceiling = target_dte + 3             # refuse a monthly when the weekly is absent
```

Target 7 → **5-10**: exactly the weekday spread a Friday-only calendar can produce
(Fri 7, Thu 8, Wed 9, Tue 10), and nothing shorter. Target 30 → 28-33.
If the nearest visible expiry falls outside the band, **sit in cash.**

The band is **derived per-day from that day's chain only** — never from the full
expiration history. Deriving it from all history would use 2026 knowledge to make
a 2019 decision (look-ahead). The dashboard displays the derived band read-only.

`dte` is **calendar days** (`chain.py:31`), so target 7 = Friday-to-Friday.

### 2. `select.py:17` hardcodes `Contract("SPY", ...)`

Every contract is labelled SPY regardless of ticker. Cosmetic today (nothing looks
a contract up *by* root), but it makes nine tickers' trade logs indistinguishable
and would collide if two tickers ever share a chain. Parameterize the root.

### 3. `report.py::spy_buy_hold` does not use SPY

It derives the benchmark from **whatever chain it is handed**. On GDX, "vs SPY
buy-hold" silently means "vs GDX buy-hold" — the strategy racing itself. Accidentally
correct on SPY runs, wrong on every other. Load SPY's series separately.

### 4. Fill timing

Today the engine reads the closing ask and fills at that same closing ask
(same-bar fill). The CL bot deliberately refuses this (signal close *t*, fill open
*t+1*).

Copying the CL bot exactly would be **worse** here: option quotes at 09:30 are the
worst-priced moment of the day. Instead, once hourly data lands: **decide on bar
*t*, fill on bar *t+1*.** No peeking, and a sane fill moment.

### 5. Cash yield knob

`run_wheel` never accrues interest on collateral. Add `cash_yield`, **default
0.0** — the Schwab sweep pays ~nothing, so 0% is the truthful default for this
owner and keeps the result conservative. Knob exists for when that changes.

### 6. Dashboard

- Ticker dropdown (data paths currently hardcoded to SPY files).
- Take-profit becomes a **slider, 1–100%**. 100% ⇒ threshold $0 ⇒ never fires ⇒
  hold to expiry (works naturally; `chain.py:42` already drops zero-bid contracts).
- DTE min/max removed as inputs; **Target DTE** is the only DTE knob. Derived band
  shown read-only.

## Frozen config (pre-registered — no sweep in round one)

```
put_delta            0.20
call_delta           0.20
target_dte           7        (weeklies — how the owner actually trades)
dte floor / ceiling  5 / 14   (derived: 5, 2 x target)
take_profit_pct      0.50
starting_capital     $100,000 (uniform across tickers so contract lumpiness does
                               not confound the comparison; winner re-run at the
                               owner's real capital)
commission           $0.65 / contract
fills                cross the spread
cash_yield           0.0
```

**One config. Nine tickers. Every result reported.** 9 tickers × 8 configs = 64
cells is a machine for manufacturing a winner out of luck. If the frozen config
shows signal, *then* sweep the survivors, with the multiple-testing luck line
attached.

## Benchmarks

Two, because they answer different questions:

1. **Buy-hold the same underlying** — does the wheel add anything *on this name*?
2. **Buy-hold SPY** — is it worth doing at all, versus the zero-effort alternative?

## Report format (pre-registered)

Per ticker, all nine, no cherry-picking:

- **Plain P&L in dollars** (not CAGR — owner's rule #3), full window and year-by-year
- Max drawdown, Sharpe
- vs buy-hold underlying, vs buy-hold SPY
- Premium **collected** vs premium **kept** (the SPY run collected $130,673 and
  kept $58k — the gap is the whole story)
- Assignment count, called-away count
- **Realised DTE distribution** — proves selection is now deterministic
- **Days sat in cash** — if a ticker is flat 40% of the time because weeklies did
  not exist yet, that changes what the benchmark comparison even means

## Anti-fooling rules

- Config, metrics and report format are **frozen in this document before any run.**
- All nine tickers reported. No selecting a winner after the fact.
- **Any result is audited hostilely in both directions** — if the wheel looks
  terrible, the bug hunt in our own code is exactly as aggressive as it would be
  if it looked amazing.
- An **independent auditor with no exposure to the prior literature** reviews the
  engine changes and the results.
- Raw numbers are shown to the owner **before** any interpretation is offered.

## Out of scope (owner's calls, parked)

- Stop-loss and rolling — test the plain strategy first; a stop on a short put
  fires at the bottom by construction (Chan: *"stops fill at much worse prices,
  realizing the catastrophic loss rather than avoiding it"*).
- Put-write-only variant (delete the covered-call leg) — a later knob.
- IV-rank / IV-percentile entry filter.
- Any parameter sweep.

## Order of work

1. Engine fixes 1–6 (+ tests).
2. **Audit run — SPY @ 30-delta / target 30 DTE** with the fixed selector. This is
   the config the published run *believed* it was testing, and it is the only run
   that answers "was +58.3% an artifact of the random-expiry bug, or was the
   conclusion real?" One comparison, not a sweep.
3. **SPY @ the frozen basket config (20-delta / target 7)** — SPY as a tenth basket
   member, so the weekly strategy on SPY sits alongside the other nine names.
   Distinct from step 2; do not conflate the two numbers.
4. Basket run at the frozen weekly config, once the pulls land.
5. Report. Raw numbers first.

## Open questions

- Schwab: can a money-market / T-bill position collateralize a cash-secured put,
  and what does swept cash actually earn? Decides whether `cash_yield` stays 0.
- Real capital number, for the reality-check re-run of any survivor.

## Amendment 2026-07-12b — defense variants (pre-registered before any run)

Loss anatomy on GDX (frozen config, 678 trades) located the damage: options legs
+$255,858, post-assignment shares **−$211,100** — all 10 worst trades are
"Called away" share positions, slow grinds not gaps. Mechanism: after assignment
above market, a 7-DTE 20-delta call sits near the depressed price, below cost
basis; any exit locks the loss. QQQ is dropped from the basket analysis
(redundant with SPY-at-basket-config as trending-index control) but stays in the
data pull, dead last.

Five runs per ticker, frozen base config, no tuning knobs on the defenses:

1. **plain** — the wheel as-is (control).
2. **no-calls-below-basis** (`call_min_strike: "basis"`) — post-assignment calls
   only at strike ≥ assignment strike; within the selected expiry, among strikes
   ≥ basis take the one nearest target delta; none available → hold shares, no call.
3. **roll-puts** (`roll_puts: true`) — on expiry day with spot < strike, buy the
   put back at the ask and sell a fresh target-delta/target-DTE put same day;
   never take assignment.
4. **liquidate-at-assignment** (`liquidate_assignment: true`) — take assignment,
   sell all shares at that day's close, return to puts (pure put-write).
5. **put-stop** (`put_stop_mult: 3.0`) — close the put when its EOD ask ≥ 3× the
   credit received. EOD marks only until the hourly re-run; gap caveat applies
   (stops fill at the bottom — owner's own Chan note).

Report all five side by side per ticker: P&L, Sharpe, maxDD, options-leg P&L vs
shares-leg P&L, assignments, trade count. **All results reported; no best-cell
selection.** Every run re-executed with intraday TP when hourly data lands.

## Amendment 2026-07-12c — basket run has two pre-registered arms

Owner decision (before any basket ticker beyond GDX/SLV/XOP/SPY was seen):
the frozen basket run executes **two arms per ticker**:

1. **plain** — the original frozen config, unchanged.
2. **call>=basis** — same config + `call_min_strike: "basis"`. Mechanism was
   identified from loss anatomy and pre-registered (amendment 2026-07-12b)
   BEFORE the defense matrix ran; XBI/EEM/EWZ/TLT/ARKK are out-of-sample for it.

Every row reported. No third arm, no tuning, no post-hoc additions. Both arms
re-run with intraday TP when hourly data lands.
