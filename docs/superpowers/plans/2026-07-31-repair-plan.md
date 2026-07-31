# Repair plan — live paper wheel bot

**Created:** 2026-07-31 · **Status:** IN PROGRESS — 3 of 62 done (A6, A16, A18; A21/A22/E8/A18b/A18c added) · **Bot state:** PAUSED
(timer stopped AND disabled on the VPS)
**Findings source:** `docs/superpowers/AUDIT-2026-07-31-full-system.md`
**Owner decisions:** bot stays paused until done · every recorded finding fixed before day 1 ·
batch review at group boundaries.

> **THIS FILE IS THE SOURCE OF TRUTH FOR PROGRESS.** A fresh session reads it and continues.
> Update the status table after every fix, with the evidence, before moving on. If the
> conversation is lost, nothing else is.

---

## How to resume in a new session

1. Read this file's status table — the first `TODO` row is the next fix.
2. Read the audit entry for that ID in `AUDIT-2026-07-31-full-system.md`.
3. Apply the gate checklist below. Record evidence in the table.
4. The bot must stay paused. Do not deploy until Phase F.

**Resume the bot (only at Phase F):**
```
ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142
sudo systemctl enable --now wheelbot.timer
sudo systemctl start wheelbot.service      # REQUIRED: re-anchors the timer, else no next trigger
systemctl list-timers wheelbot.timer       # NEXT must be populated
```

---

## Execution model

**One writer.** All code changes are made serially by the main session. **No agent ever writes
to the repository.** This is not a preference — concurrent writers caused an injected mutation
to reach the working tree and reverted a production fix twice on 2026-07-31 (see audit P1).

**Agents are read-only, in two roles:**
- **Analysts** — dispatched *before* a hard fix to answer "what should this fix be, and what
  evidence supports it?" They return a spec, never a diff.
- **Skeptics** — dispatched *after* a fix, told to assume it is wrong and prove it by running
  things. Per-fix on Group A; per-group elsewhere.

---

## Hard rules (always)

| | Rule |
|---|---|
| **A1** | **No claim without a receipt.** Never state "verified"/"safe"/"works" without the command and its output. Absent that, label it an opinion. |
| **A2** | **No reasoning-only safety arguments.** To claim something cannot happen, construct the case and show it failing. (Audit P2.) |
| **A3** | **No test is weakened to go green.** A failing test is a bug or a design decision. Design decisions go to the owner. (Audit P3 — the direct cause of three surviving mutations.) |
| **A4** | **A passing suite is not evidence.** Nine deliberate bugs survived all 630 tests. |
| **A5** | **Unverified assumptions are declared out loud**, here and to the owner. |
| **A6** | **Nothing reaches the VPS mid-repair.** One deploy, at Phase F, after full rehearsal. |

---

## Per-fix gates

| # | Gate | Evidence to record |
|---|---|---|
| 1 | Reproduce: write a test that fails **because of this defect** | The failing output (not a typo/import error) |
| 2 | Minimal fix — no opportunistic cleanup riding along | The diff |
| 3 | Full suites, both environments | Pass counts |
| 4 | **Mutation check** — break the fix, prove the test catches it, restore, prove green | Both runs |
| 5 | Line audit — print every changed line, account for each | Functional-diff output |
| 6 | Blast radius — grep every caller of what was touched | The grep and the read |
| 7 | Plain-language entry | The text |

**Extra gates, Group A only (money path):**
- **C1** Fresh read-only skeptic per fix.
- **C2** Read-only dry run against live Schwab data — what would have changed.
- **C3** Before/after stated in dollars against the real book.

**Group boundary:** adversarial sweep · surviving-mutation hunt · regression proof (declaring in
advance any anchor deliberately broken) · owner review.

**Gate failure:** stop, record, fix or escalate. A fix that cannot pass its gates is not done
and goes back to TODO. If tempted to argue a gate is unnecessary, record that argument here —
it is the thought that precedes every skipped check.

---

## Commands

```bash
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
.venv/bin/python -m pytest tests -q                              # 484 backtest (~5 min)
PYTHONPATH=. .venv-live/bin/python -m pytest live/tests -q       # 146 live (~5 s)
```
Baseline at plan creation: **484 + 146 = 630 passing** at commit `7022ade`.

---

## OWNER DECISION 2026-07-31 (post-analyst): PLUMBING FIRST

Four analysts studied the fill model. Their findings **deferred the strategy question rather than
answering it**, and the owner chose: *fix all the plumbing, prove the bot does what it claims,
before deciding anything about the strategy.*

**In scope now (plumbing):** Groups B, C, D, E and every Group A item that is unambiguous
correctness. Plus two new items the analysts found (A16, A17 below).

**Deferred (strategy) — do NOT decide these while repairing:**
- Which fill model. Build the *seam* (A18: one shared fill function), not the choice.
- The size-cap parameter. Capture the *data* (A2), not the cap.
- Whether the 60% take-profit should exist at all.
- Whether to re-run the sweep on the 341-ticker universe, and whether the reserved five are spent.

### What the analysts established (evidence in their reports; summarised here so this file stands alone)

- **Resting GTC limit: REJECTED.** It is a strict *superset* of the current rule, not a
  restriction — 219 campaigns fill where the live rule does not, 0 the other way, over 3,159
  campaigns. It was recommended on a false premise. It also forces the take-profit threshold onto
  a $0.01 tick grid it does not fit on **65.6% of P&L** (WBD's threshold is $0.004, unenterable),
  and its correctness depends on process uptime the bot does not have.
- **Next-poll fill: REJECTED as a correction.** Measured three ways, the expected adverse move at
  the 5-minute cadence is under $0.0001/share; p90 is at or below one tick. On the live book it is
  **+0.1%**, bootstrap −8.5% to +8.4%. It adds noise and removes no bias.
- **Spread-fraction (k≈0.5, $0.01 floor) is the supported correction** — costs −5.8%, band −3% to
  −12%. Derived from real through-the-offer trades (k ∈ [0.13, 1.29], geometric mean 0.41) and the
  tick grid (k=0.10 is inert on 62.5% of fills; k=0.50 on 8.7%). A flat tick rule is *wrong* —
  +1 tick costs 27.0% of AGNC's P&L and 0.24% of AMZN's, a 113× severity range.
- **Size is the dominant distortion, not price.** Grid-wide cap `n ≤ min(0.20×ADV20, bidSize)`
  behind a gate of rel-spread ≤ 0.10, OI ≥ 250, volume ≥ 25. The gate alone blocks **114 of 145
  entries and 90.4% of contracts**. DOW 28P requested 757 contracts against ~84/day ADV — **9×**.
  Either the size was unattainable (cap it: 90%+ of P&L vanishes) or it was paid for (price it:
  **−54%**). Per-account capping is insufficient — it still puts 258 contracts, 3.06× ADV, into one
  strike in one instant.
- **The 60% take-profit does not survive realistic fills.** Real production engine, 9-ticker
  in-sample universe: with +$0.05 exit slippage, TP60 returns $115,540 against **$127,940 for
  holding to expiry** (N=1); $32,945 vs $34,215 at N=5. The parameter surface is a sawtooth
  (97/148/121/150/141/128k across TP 50/60/70/80/90/none) and the ranking *inverts* with the fill
  model, so 60% was never identified independently of the assumption it was chosen under. TP beats
  hold on 7.4% of campaigns and loses on 69.2%, with an identical worst case.
  **Caveat: 9 tickers, and the same 9 the parameter was tuned on. 341 tickers of EOD data exist
  and were not used for this sweep.**
- **Benchmark caveat.** SPY buy-and-hold over the same window returned $192,674 on $100k, beating
  every arm — but the wheel sits largely in cash, so this is not capital-matched or risk-adjusted.
  Directionally right, overstated as presented.
- **There is no distribution in the live results.** $81,608 is **13 exit decisions on 11 distinct
  contracts**; two DOW legs are 55%; and **66.5% was booked on sessions where the intraday manager
  observed under 60% of the trading day** (RTH coverage 70.2% overall; 07-22 26%, 07-31 37%).

### New defects found by the analysts

| ID | Sev | Defect |
|---|---|---|
| A16 | **CRITICAL** | The EOD chain is pulled at 17:00 ET, **after the options close**. Entries are sold at the bid of a post-close quote — measured **3.0–4.0× wider** than the same options during the session (live median rel-spread 29.8% vs 7.4% historical intraday). TMO 512.5P was quoted 8.00 × 15.30. This corrupts entry credits AND strike/delta selection, and every slippage figure in the audit is anchored on a spread nobody could cross. |
| A17 | HIGH | `live/intraday.py:45` sets `pos["short"] = None` unconditionally and the state schema has no working-order/partial-fill representation. Any fill model that can partially fill is unrepresentable. In the hour the limit was first touched, 55.4% of hours traded fewer than 172 contracts — below the bot's largest real fill of 181. |
| A18 | HIGH | Four engines, four inline fill expressions: `portfolio.py:96` (EOD ask), `live/intraday.py:38-43` (live ask), `wheel.py:171-176` and `regime_router.py:120-125` (hourly trade print, fill at the NEXT bar). The BATCH rule fires on 81.5% of campaigns and the LIVE rule on 76.7% — **159 campaigns (5.3%) the backtest takes profit on that live can never close**. Until one shared fill function exists, no A/B across engines is comparable and the 60% parameter has no provenance. Filed as a known divergence in `specs/2026-07-11-wheel-04-intraday-design.md:13`, never sized. |
| A19 | MEDIUM | `live/data.py` discards `openInterest`, `totalVolume`, `bidSize`, `askSize` from every Schwab response. Capturing them is a prerequisite for any size-aware model and replaces the entire ADV proxy chain (currently ×3.6 typical error) with measurement. Cheap, and it should start immediately so data accrues. |

## Order of work, and why

1. **Group A (trading behaviour)** — changes what the bot does with capital. Highest value,
   hardest, most gates. Do it while attention is freshest.
2. **Group B (data quality)** — Group A's fixes are only as good as their inputs, and B1 (bad
   prices) corrupts the selector that feeds everything in A.
3. **Group D (infrastructure)** — silent failures. Independent of A and B, so it is safe to do
   after; but must land before day 1 or we cannot trust that day 1 is even running.
4. **Group C (reporting)** — deliberately last. It changes nothing about correctness, and the
   reset discards the history it would have described.
5. **Group E (test hardening)** — folded into each group as its fixes land, plus a dedicated
   pass on the known-weak equivalence tests.

---

## Status

Legend: `TODO` · `WIP` · `DONE` · `BLOCKED` · `DEFERRED (owner)`

### Group A — what the bot DOES

| ID | Fix | Sev | Status | Evidence |
|---|---|---|---|---|
| A16 | **Pull the chain during RTH, not at 17:00 after the close** | CRIT | **DONE** | see A16 evidence block below. **Owner decisions 2026-07-31:** (1) snapshot missing on a trading day → **skip the day + gap record + alert**, never fall back to a post-close pull; (2) half days (~3/yr) → **accepted as-is**, no half-day calendar — those snapshots will be post-close, a disclosed bounded corruption; (3) held-leg quote pull stays post-close → filed as **A21**, not folded in. |
| A18 | One shared fill function across all four engines (seam only, no model choice) | HIGH | **DONE** | see A18 evidence block below. Follow-ups filed: **A18b** (dedupe `_num`, delete dead `live_marks`/`_mark_from_quote`, align `live_asks`' admission or document it), **A18c** (migrate or retire the fifth copy in `scripts/audit_defense_execution.py` — currently kept as an independent referee and it verified the seam with 0 mismatches on 384 intraday + 13 EOD fills). Original note preserved: **Also four ADMISSION rules, measured during A6** on identical payloads — `0.00 x 0.00`: `contract_quotes` skip / `live_asks` skip / `_mark_from_quote` None. bid `None`, ask `0.05`: skip / **admits 0.05** / None. bid `-0.01`, ask `0.05`: skip / **admits 0.05** / **0.02**. So the dashboard prices a liability the intraday manager refuses to act on. Also: `live_marks` and `_mark_from_quote` have **zero production callers** — dead code, tests only. And `_num` still exists twice (`live/marks.py:25`, `live/data.py:32`, byte-identical). |
| A19 | Capture `openInterest`/`totalVolume`/`bidSize`/`askSize` from the Schwab response | MED | TODO | |
| A21 | `merge_held_legs` quote pull happens at 17:00, post-close (`run_daily.py:192` → `held_legs.py:113`) — same staleness class as A16 but marks/dashboard only, not entries | MED | TODO | Split out of A16 by owner decision 2026-07-31. Affects held-leg marks, snapshot equity (`snapshots.py:26`), and the EOD TP branch (`portfolio.py:94-101`); the intraday TP already runs on RTH quotes. |
| A22 | `chain_frame` stamps `from_date`/`to_date` from the **box (UTC) clock**, not ET (`live/data.py:81 dt.date.today()`) — on the UTC VPS any retry from 19:00-20:00 ET onward requests **tomorrow's** expiry window, shifting the DTE band the selector uses | MED | TODO | Found by the A16 analyst. `obs_date` is threaded correctly; only the request dates are wrong. Dormant on the new RTH snapshot path (UTC date == ET date at 15:xx ET) but live on `--smoke` and any manual post-19:00 pull. |
| A17 | Represent partial fills / working-order state | HIGH | TODO | |
| A1 | ~~Intraday fill realism~~ **DEFERRED — strategy decision, see owner decision above** | CRIT | DEFERRED (owner) | resting limit and next-poll both rejected on evidence; spread-fraction k≈0.5 is the supported candidate |
| A2 | Liquidity gate (rel-spread/OI/volume) — **gate yes, cap parameter DEFERRED** | CRIT | TODO | grid-wide cap value is a strategy decision |
| A3 | Minimum credit / maximum spread / unclosable-by-construction guard | HIGH | TODO | **Sized during A6** (skeptic, real trade log, 145 SELL_PUTs, `commission_per_contract=0.65`): on micro-credit names a leg that used to expire free is now bought back at the $0.01 tick, surrendering **RIG 70.2% / VALE 26.0% / AGNC 22.4%** of banked premium (RIG: $6.60 to close $9.40 banked). 144 of 145 entries have a TP trigger reachable at the minimum tick. This is the minimum-credit case in dollars. |
| A20 | **TP=0.60 is out of sample on the population A6 admits** | HIGH | TODO | Found by the A6 skeptic. `src/engine_v2/options/chain.py:42` and `live/data.py:66-67` both filter `bid > 0`, so a `0.00 x 0.01` row is **unrepresentable in the backtest chain** — the frozen TP policy was never measured over it. Not created by A6 (`rows_from_quotes` opened it for EOD on 07-31), but A6 extends it to the path producing 100% of realized P&L. |
| A4 | Covered-call window — reach the basis floor | HIGH | TODO | |
| A5 | Same-day re-entry guard must see intraday closes | HIGH | TODO | **A6 is an amplifier for this** — `portfolio.py:84` rebuilds `closed_today = set()` inside `step_one_day` and `manage_intraday` persists nothing, so every intraday close is invisible to the guard, and A6 exists to increase intraday closes. Bound: the newly-admitted population is deep-OTM/near-worthless, least likely to be re-selected at 0.30 delta, so the marginal exposure from A6 alone is probably small. |
| A6 | Intraday must see a 0.00 bid (align with `rows_from_quotes`) | HIGH | **DONE** | see A6 evidence block below |
| A7 | Intraday holiday + quote-freshness gate | MED | TODO | |
| A8 | Early assignment modelling | MED | TODO | |
| A9 | Defer the covered call one session after assignment | MED | TODO | |
| A10 | Corporate actions — at minimum detect and refuse | HIGH | TODO | |
| A11 | Clock gate inside `run_daily` (refuse before 16:00 ET without `--force`) | MED | TODO | |
| A12 | Budget allocation must not strand capital at small sizes | MED | TODO | |
| A13 | Model exchange/OCC/regulatory and assignment fees | LOW | TODO | |
| A14 | Surface `StepResult.warnings`; bound the unsettleable-expiry refusal | HIGH | TODO | |
| A15 | Restore a bounded reach-back for the batch engine | MED | TODO | |

### Group B — what the bot SEES

| ID | Fix | Sev | Status | Evidence |
|---|---|---|---|---|
| B1 | Drop non-positive/non-finite closes; report them; alert if recent | CRIT | TODO | |
| B2 | Empty/stale payload is a failure, not a holiday | CRIT | TODO | |
| B3 | One malformed contract must not discard the ticker | MED | TODO | |
| B4 | `zombie_check`: held legs as a third, fully-required population | HIGH | TODO | |
| B5 | Held ticker outside `UNIVERSE` must be an error, not silence | HIGH | TODO | |
| B6 | Re-vet the universe; add a `_RETIRED` frozenset with a test | HIGH | TODO | |
| B7 | `chain_frame` must use the ET obs date, not the box clock | MED | TODO | |
| B8 | Truncated history must be recorded and logged | MED | TODO | |
| B9 | Reject `underlyingPrice <= 0` | MED | TODO | |
| B10 | Skip non-standard / `multiplier != 100` contracts | LOW | TODO | |
| B11 | Warn when the selected strike is the extreme of the window | MED | TODO | |

### Group C — what the OWNER SEES

| ID | Fix | Sev | Status | Evidence |
|---|---|---|---|---|
| C1 | Record per-leg quote timestamps; flag/refuse bars with a stale book | CRIT | TODO | |
| C2 | Two disclosure classes; render the reason, not just the date | CRIT | TODO | |
| C3 | Banner must render `records`, not `dates` | HIGH | TODO | |
| C4 | Suppress Sharpe below ~30 obs; show ±SE; subtract rf; fix annualisation | HIGH | TODO | |
| C5 | Campaign-level win rate on realized cash; show wins/losses/open | HIGH | TODO | |
| C6 | Buy-and-hold benchmark (SPY + equal-weight of names traded) | HIGH | TODO | |
| C7 | Header the grid with effective-N, distinct decisions, top-underlying share | HIGH | TODO | |
| C8 | One source of truth for both dashboard pages, or stamp as-of dates | MED | TODO | |
| C9 | Dashboard must add share value for covered positions | CRIT | TODO | |
| C10 | Drawdown/returns measured from capital, not the first snapshot | MED | TODO | |
| C11 | Stamp `mark_basis` in every snapshot; annotate the changeover | MED | TODO | |
| C12 | Deduplicate the equity index | LOW | TODO | |
| C13 | Fix the `append_gap` kwarg collision on the partial-failure path | HIGH | TODO | |
| C14 | Fold only `correction: True` records | HIGH | TODO | |
| C15 | Normalise dates in `append_gap`/`append_correction` | MED | TODO | |

### Group D — whether it RUNS

| ID | Fix | Sev | Status | Evidence |
|---|---|---|---|---|
| D1 | Dead-man's switch for the intraday manager; check its exit code | CRIT | TODO | |
| D2 | Stale-but-successful feed must not be read as a holiday | CRIT | TODO | |
| D3 | Propagate `send_alert` failure into exit codes; no marker on failure | CRIT | TODO | |
| D4 | Scan forward from last known-alive, not a fixed 10-day window | HIGH | TODO | |
| D5 | Ungate the token nag from market hours; lower to ~4 days; escalate past zero | HIGH | TODO | |
| D6 | Run the health check first, or as its own unit | HIGH | TODO | |
| D7 | `OnCalendar=*:0/5` on the timer; add `OnFailure=` | HIGH | TODO | |
| D8 | `secret_guard`: literal token check; abort on unscannable files; more shapes | HIGH | TODO | |
| D9 | Tick must exit non-zero on failure | MED | TODO | |
| D10 | Check intraday sync result; write the marker after the sync | MED | TODO | |
| D11 | `.prev` only on the EOD write, or dated backups; fsync the directory | MED | TODO | |
| D12 | Missed-day email must name the missed date and the real deadline | MED | TODO | |
| D13 | `.gitignore` in the synced tree; clean up orphaned `.tmp` | LOW | TODO | |
| D14 | **External heartbeat off the VPS** (subsumes D1, D2, D3, D4, D6, D7) | HIGH | TODO | |

### Group E — test hardening

| ID | Fix | Status | Evidence |
|---|---|---|---|
| E1 | Pin the equity mark exactly for a covered position (shares **and** short) | TODO | |
| E2 | Replace the weakened equivalence assertions with an exact divergence assertion | TODO | |
| E3 | Cover the dashboard's live-quote path | TODO | |
| E4 | Give `--smoke` a non-empty position set so it exercises the merge | TODO | |
| E5 | Assert on behaviour, not fixtures, in the two flagged tests | TODO | |
| E6 | Rewrite `test_all_modules_follow_the_override` so it stops proving the opposite | TODO | |
| E7 | Re-run all nine surviving mutations; every one must now be killed | TODO | |
| E8 | `run_chain_snapshot.main()` wiring tests (zombie wiring, partial-exit-1, save-recheck call site, `--force`) — predicates are tested, the wiring is executed only by the A16 skeptic's S1 run and the C2 dry run | TODO | filed from skeptic F6, 2026-07-31 |

---

## A6 — evidence (completed 2026-07-31)

**In plain language.** The bot sells a put and wants to buy it back cheap once it has decayed.
When such a put becomes worthless the market quotes it *"nobody will pay anything for it, but you
can buy it back for a penny"* — `0.00 x 0.01`. `contract_quotes` threw that quote away because the
BID was zero, so the intraday manager was blind to exactly the leg it exists to close. The EOD
sibling `held_legs.rows_from_quotes` had already been fixed for this, so the two functions
disagreed about the same contract. A zero bid is a price; a zero ask is not, and refusing `ask <= 0`
stays load-bearing — an ask of zero satisfies `ask <= (1-TP)*credit` for **any** credit and would
close the leg for free (defect C1, 2026-07-18).

**Started from an unfinished uncommitted change** left in the working tree by a prior session,
with the live suite RED (`test_marks.py:72` still asserted the old rule). Owner ruled: finish A6
first rather than start A16 on a red tree. The superseded assertion was replaced with a comment
recording that it *was the defect, not the spec* — the new tests assert both directions.

| Gate | Evidence |
|---|---|
| 1 Reproduce | `marks.py` reverted to HEAD, new tests kept → `AssertionError: a 0.00 x 0.01 market is worthless, not absent / assert 'RIG' in {}` at `test_marks.py:111`. `1 failed, 10 passed`. Real assertion, not an import error. The two guard-preservation tests correctly PASS on old code. |
| 2 Minimal fix | `bid <= 0 or ask <= 0` → `ask <= 0 or bid < 0`, plus `_num` coercion on bid/ask/mark (see amendment below). |
| 3 Suites | `484 passed` (backtest, 295s) + `151 passed` (live, 4.4s) = **635**, from a 630 baseline + 5 new tests. |
| 4 Mutation | 4 mutants, all killed: old rule → 2 fail · no guard → 3 fail · drop `bid < 0` → 1 fail · drop `_num` → 2 fail. Restored → `151 passed`. |
| 5 Line audit | Functional diff is one condition, one coercion, one `mid` expression, the `_num` move, two docstrings. **Declared delta:** a NEGATIVE exchange `mark` now falls back to the midpoint instead of being used as `mid`. `.mid` is dead on this path (proven, C1 below) and this matches `rows_from_quotes:72`. |
| 6 Blast radius | One production caller, `run_intraday.py:53`. `_num` now shared with `held_legs`'s four call sites (byte-identical function). `live/data.py:32` keeps a third identical copy — recorded under A18, not touched. |
| C1 Skeptic | Read-only agent, verdict **CORRECT-BUT-INCOMPLETE**. Could not break the central claim: replaced `Mark` with a tripwire whose `.bid`/`.mid` raise, drove `manage_intraday` under the real `FROZEN` config across TP-fires / no-fire / CALL-phase → *"attributes read during manage_intraday: NONE"*. `buy_cost` (`wheel.py:80`) reads only `mark.ask`. Also confirmed C1 never fired in production: across 114 real `CLOSE_PUT`s the minimum booked price is `$0.03`, zero closes at `$0.00`. |
| C2 Dry run | Read-only, live Schwab, 15:31 ET 2026-07-31, all 4 held legs. HAL `0.28 x 0.30` and WBD `0.06 x 0.10` unaffected. **RIG `0.00 x 0.01` and TMO `0.00 x 4.80` were both invisible to the old rule** — the two legs the bot most needed to see. TMO quoted `0.00 x 4.80` is itself a live sighting of A16. |
| C3 Dollars | **$0.00 today.** The $13.20 that would close RIG's 8 contracts does NOT apply: both newly-visible legs expire today and `intraday.py:33` skips expiry-day legs by design. Value is on future non-expiry days. Stated because the first draft of this number was $13.20 and that would have been wrong. |

**Amendment forced by the skeptic — two regressions the original diff introduced:**
1. Testing `ask <= 0` *before* `bid <= 0` removed the short-circuit that used to swallow a
   non-numeric ask. A single malformed leg then raised `TypeError` out of `run_intraday.py:66`'s
   per-account handler and **suppressed take-profit for every other position in that account for
   that tick**. Demonstrated: `NEW rule -> RAISED TypeError` where `OLD rule -> DOW take-profit
   DID fire`.
2. A NaN ask was newly admitted (the old `bid <= 0` clause caught it by accident).
Both fixed by routing bid/ask/mark through `_num` — the coercion `rows_from_quotes` already used,
which is the parity A6 claimed but did not originally reach.

**One test of mine was wrong and was corrected, not weakened (A3):** it asserted a numeric string
`"0.05"` must be refused. `rows_from_quotes` coerces it to `(0.0, 0.05, 0.025)`, so coercing is the
spec; the assertion mis-stated it. Refusal is still asserted for `""`, `None`, `"abc"`, NaN, `{}`,
`[]`, `"None"`.

**Not re-run after the amendment:** a second skeptic pass. The amendment is itself skeptic-derived
and mutation-tested, but this is declared, not claimed as verified (A1/A5).

---

## A18 — evidence (completed 2026-07-31)

**In plain language.** Four engines each carried a hand-written copy of the take-profit rule
("when do we buy the sold option back cheap"), and the copies disagreed — the backtest's rule
fires on 81.5% of campaigns, the live rule on 76.7%, so 159 campaigns (5.3%) the backtest wins
are physically unclosable live. Now one shared card, `src/engine_v2/options/fills.py
try_take_profit`, and all four cashiers read from it. Seam only: each engine's exact old rule is
preserved behind a named mode (`quote` = at the ask; `print` = hourly trade print, decide bar i,
fill bar i+1). Which rule is *right* stays the deferred strategy question — but the divergence
now has a name in code and a test that documents it, instead of being four accidents.

**Gate-1 adaptation, declared:** for a zero-behavior-change refactor the "failing test" gate is
replaced by *manufactured fingerprints* — both engines without a surviving byte-anchor (engine 1's
was deliberately broken by the audit; engine 2 never had one) were digested over deterministic
grids + real chains BEFORE the change and re-digested after. That is a stronger reproduce for
this defect class: the defect is the absence of a seam, and the risk is behavior change.

| Gate | Evidence |
|---|---|
| 1 Reproduce (adapted) | Pre-seam SHA256 fingerprints: engine 1 (step_one_day TP grid, 40 cases — identical hash under py3.9 AND py3.12), engine 2 (manage_intraday, 720-case quote grid), engines 3+4 (real SPY/GDX chains, EOD + synthetic hourly bars). Harness in session scratchpad (`a18_digest.py`). |
| 2 Minimal fix | New `fills.py` + the four TP call-sites swapped + dead `buy_cost` import dropped from the router. Entries, roll, stop, expiry, equity-mark basis, admission rules: all untouched by design. |
| 3 Suites | `495 passed` (backtest, 282s; includes both frozen SHA goldens) + `166 passed` (live) = **661**, from 650 + 11 new seam tests. |
| 4 Mutation | 5 seam mutants, all killed — ask→bid (4 backtest + 1 live failures), `<=`→`<` (both suites), same-bar fill (4), phantom-0.00 bars admitted (3), expiry-guard dropped (1; the live engine's own redundant guard held, as designed). Skeptic independently ran 5 more mutants of its own — all killed. |
| 5 Line audit | Engines: −80/+53 (four copies deleted, one call each). Declared deltas: `tp_fired` local removed (zero remaining references, grepped); `key` now computed on every held-short day even when TP disabled (dead work, proven behavior-neutral); router counters now keyed off `dec.via`. |
| 6 Blast radius | `try_take_profit`: exactly 4 production callers. `buy_cost` remains for roll/stop/residual paths (unmigrated by design) + `test_wheel_parts` pin. Fifth copy in `scripts/audit_defense_execution.py` knowingly unmigrated → A18c. `fills.py` imports nothing from the package except stdlib — no cycle; all entry points import-clean under both interpreters. |
| C1 Skeptic | Verdict **COULD-NOT-BREAK**. Old-tree-vs-new-tree differential (git archive of HEAD): 21 adversarial backtest scenarios (NaN/negative/exact-threshold quotes and bars, unsorted/multi-day/wrong-key/expiry-day bars, tp=0/1/None) → `BACKTEST_IDENTICAL`; 8 live cases incl. dict-shaped contracts with string expiries from old state files → `LIVE_IDENTICAL`; real-chain roll+stop+gates+hourly runs → identical SHA over 2,916 trades incl. gate_events; router counter placement proven identical; tests not weakened (`git diff HEAD -- tests/` empty except the new file). |
| C2 Dry run | The unmigrated fifth copy run as an independent referee against the migrated engines on real GDX data: `audit_defense_execution` exit 0 — 138 rolls + 39 stops + 318 gate events, 0 mismatches; `--hourly`: **384 intraday TPs + 13 EOD TPs verified, 0 mismatches**. |
| C3 Dollars | **$0.00 by construction** — byte-identical fingerprints mean not one fill, price, or timestamp changed. The dollar value is optionality: every future fill-model A/B now changes ONE function, and the 159-campaign divergence is measurable at a single seam. |

**Declared (A1/A5):** `fills.py` duplicates `buy_cost`'s arithmetic (cycle avoidance); the pin is
`test_quote_fill_matches_buy_cost_exactly`, not shared code. The seam computes `thresh` before
the mark-presence check — order differs from one old call site; unreachable difference today
(credit is always a float), flagged by the skeptic as opinion. numpy-scalar cash contamination on
the print path is pre-existing and now *pinned* by `test_print_cost_keeps_numpy_scalar_dtype`
rather than accidental.

---

## A16 — evidence (completed 2026-07-31)

**In plain language.** The bot made its evening decisions by reading option prices at 5pm — an
hour after the options market closed, when quotes are leftover chalkboard ghosts measured 3–4×
wider than anything tradeable. Now a snapshot pass runs *during* market hours (15:20–15:50 ET),
photographs the live price boards, and saves them; the 5pm decision run reads the photo instead
of the dead board. Split, not moved, because the decision step genuinely needs the official 4pm
close (expiry settlement, equity marks) and the candidate list is provably identical at both
times (it depends only on prior-session data).

**New pieces:** `live/chain_store.py` (JSON-per-day snapshot store, obs-keyed, atomic write,
corrupt/stale → None), `live/run_chain_snapshot.py` (RTH runner, window + save-time clock gates),
`_live_market` gains a REQUIRED `chains` arg naming the chain source out loud, `run_daily.main`
gains missing/incomplete-snapshot gates, `wheelbot_tick.sh` gains the 15:20–15:55 window block
(marker only on exit 0).

| Gate | Evidence |
|---|---|
| 1 Reproduce | New test asserting the daily market never pulls chains live → `AssertionError: A16: run-time market pulled option chains live (post-close book) / assert ['GDX'] == []`. Real behavioral failure, not an import error. |
| 2 Minimal fix | The five pieces above; nothing else rode along (A22, the UTC `from_date` bug the analyst found, deliberately NOT fixed here — filed as its own row). |
| 3 Suites | `484 passed` (backtest, 286s) + `166 passed` (live, 4.6s) = **650**, from the 635 post-A6 baseline + 15 new tests. |
| 4 Mutation | 8 mutants, all killed: ignore-snapshot → 2 fail · silent-empty-chain → 1 · holiday-judged-before-dead-feed → 1 · stale-obs served → 1 · window past close → 2 · row-validation dropped → 1 · incomplete-branch off → 1 · save-grace widened to 17:00 → 1. Restored → 166 green. |
| 5 Line audit | run_daily +112 (seam + classifier + two gate branches), tick +16 (window block), 4 new files, tests +110/new. **Declared deltas:** (a) `--smoke` still pulls chains live by design (throwaway connectivity check); (b) a missing snapshot day exits 0 and writes the marker — deliberate, no retry can rebuild an RTH snapshot; (c) snapshot files land in `data/live/chains/` and sync with the state repo, ~130 KB/day measured. |
| 6 Blast radius | `_live_market`: exactly 2 production callers (run_daily.main, snapshot runner), both explicit. `chain_frame`: reachable only via the gated seam + manual `smoke_pull.py` (carries A22, recorded). Gap reason strings are free-form — no consumer changes. Health check: skeptic proved no double-alert on gap days. |
| C1 Skeptic | Verdict **CORRECT-BUT-INCOMPLETE**. Could not break the core: fresh vs saved+loaded chains through real `step_one_day` + FROZEN config are **byte-identical** (entry, covered-call basis floor, TP, held-leg splice with `held_only`/delta coercion); every staleness vector refused (wrong day, tampered obs, weekend `--force` leftover, midnight). Four demonstrated bad-day edges (F1–F4) **fixed as amendments, each with a test and a killed mutant** — see below. F5 (empty-frame dtype drift, proven inert) declared in the store docstring. F6 (runner-main wiring untested) filed as **E8**. |
| C2 Dry run | Live Schwab, 2026-07-31: runner **refused at 17:08 ET, exit 1** (the very pull the old code did daily). `--force` into a scratch store: 547 closes → 11/11 candidate chains → 130 KB snapshot; loaded back, wrong-day load → None. |
| C3 Dollars | **$0.00 today** — bot paused, no entries stepped. Retro-measurement impossible: Schwab has no historical chain endpoint. Prospective, measured from the C2 snapshot itself: the 17:15 book across 392 candidate put rows shows **median rel-spread 28.3%, p90 100%** vs the 7.4% intraday historical median — independently reproducing the audit's 29.8% figure. Every future entry credit and delta selection was being priced off that book. |

**Amendments forced by the skeptic (all four demonstrated by execution, then fixed):**
1. **F1** — a garbage-but-valid-JSON snapshot either crashed every 17:00 retry tick or served a
   NaN-filled frame. Load now validates per-ticker rows (list of dicts carrying every engine
   column) and returns None on anything else.
2. **F2** — a candidate absent from a *present* snapshot tripped the zombie path: exit 1 retried
   and alerted every 5 min until 23:30, diagnosed "lapsed token", and could never succeed (the
   snapshot is immutable after close). Now classified like the missing-snapshot case:
   holiday-aware, one alert, gap reason `chain_snapshot_incomplete`, exit 0.
3. **F3** — a sub-threshold chain failure at 15:2x froze that ticker (and its held leg's TP) for
   the day with ~30 min of window left. The runner now saves the partial snapshot but exits 1,
   so every remaining in-window tick retries a cleaner pull that overwrites it.
4. **F4** — nothing re-checked the clock after the pull started: a legal 15:55 start finishing
   ~16:02+ blessed post-close quotes as RTH. Window close tightened 15:55 → 15:50 and the save
   re-checks the clock (16:05 grace = the closing book seconds late, not the 17:00 ghost).

**Declared, not verified (A1/A5):** intraday Schwab chain latency is assumed comparable to the
measured post-close ~7 min — first real window will tell; delta drift RTH-vs-post-close was
never measured (the audit measured spreads); `run_chain_snapshot.main()` wiring has no automated
test (E8).

---

## Phase F — before day 1

| # | Gate | Status |
|---|---|---|
| F1 | Full-day rehearsal, all accounts, throwaway store, live data, nothing written to real state | TODO |
| F2 | Forced assignment test on a scratch account: expiry → assignment → basis floor → covered call → called away, on real data, ledger shown to owner | TODO |
| F3 | Dashboard actually opened and read (never done as of 2026-07-31) | TODO |
| F4 | Fresh nine-domain audit of the repaired system, from scratch | TODO |
| F5 | Deploy; verify Mac/VPS byte-identical by checksum | TODO |
| F6 | Re-enable timer; run the service once to re-anchor; confirm `NEXT` exists | TODO |
| F7 | Reset all 25 accounts; day 1 begins | TODO |

---

## Open questions for the owner

| # | Question | Status |
|---|---|---|
| Q1 | Do `wheel.py` and `regime_router.py` follow `portfolio.py` onto ask-marking? Restores equivalence but breaks the solo digest anchor the wheel project rests on. | OPEN |
| Q2 | What slippage model for A1 — next-poll fill, fixed ticks, half-spread, or a combination? (Analyst agent to propose; owner decides.) | OPEN |
| Q3 | Confirm `call_min_strike="basis"` is intended as *net* basis, so calls are legitimately written below the assignment strike once premium accrues. | OPEN |
| Q4 | Keep 547 tickers / 25 accounts / a separate intraday engine, or shrink the surface? Rejected once (owner chose to fix everything) — revisit only if the repair proves impractical. | CLOSED — fix everything |
| Q5 | Re-run the take-profit sweep on the **341-ticker** EOD universe (reserved five excluded) rather than the 9 in-sample tickers? The damning result stands on 9 tickers — the same 9 the parameter was tuned on. | OPEN — deferred with the strategy question |
| Q6 | Are the reserved five (XBI, EEM, EWZ, TLT, ARKK) now spent? Two analysts included them — they were measuring microstructure (spreads, fill rates, volumes), not selecting a strategy, but per-model P&L was computed on campaigns containing them. Disclosed 2026-07-31; owner has not ruled. | OPEN |
| Q7 | Does the intraday manager stay a separate engine at all? Its RTH coverage is 70.2%, it has died mid-session 4×, it has no dead-man's switch, and it produces 100% of realized P&L from 13 decisions clustered 09:35–12:00 — i.e. it is an overnight-gap harvester, not an intraday manager. | OPEN |

---

## Log

| Date | Entry |
|---|---|
| 2026-07-31 | Audit completed (9 domains). Bot paused: timer stopped and **disabled**. 8 fixes shipped as `4cffd72` + `7022ade`. Plan created; no repair work started. |
| 2026-07-31 | **A6 DONE.** Found the tree dirty and the live suite RED with a prior session's unfinished A6 change; owner ruled finish-A6-before-A16. All 7 gates + C1/C2/C3 recorded above. Skeptic forced an amendment (two regressions the reorder introduced). New findings filed: **A20** (TP=0.60 out of sample on the admitted population), plus evidence added to A3, A5, A18. Suites 484+151=635. Nothing deployed — bot stays paused. |
| 2026-07-31 | Note: today's 9 expiring legs (TMO 512.5P ×9, RIG 4.5P ×8) are **unsettled** because the bot was paused before the EOD run. They settle correctly on resume via the late-expiry path at the expiry day's own close. Not a lost day. |
| 2026-07-31 | **A16 DONE** (laptop restarted mid-session first; tree was clean, nothing lost). Analyst spec → 3 owner decisions → all 7 gates + C1/C2/C3 recorded above. Skeptic (CORRECT-BUT-INCOMPLETE) forced 4 amendments, each tested + mutation-killed. New rows filed: **A21** (held-leg quotes post-close), **A22** (UTC `from_date` in `chain_frame`), **E8** (runner-main wiring tests). Suites 484+166=**650**. Nothing deployed — bot stays paused; the tick-script window block reaches the VPS only at Phase F. |
| 2026-07-31 | **A18 DONE.** Analyst spec (4-engine map, 12 named silent-change risks) → seam `fills.py` → 4 TP call-sites migrated, zero behavior change proven by pre/post fingerprints (byte-identical, all 4 engines) + skeptic old-vs-new tree differential (**COULD-NOT-BREAK**, 29 adversarial scenarios + real-chain roll/stop/gates runs + the unmigrated fifth copy as referee: 0 mismatches on 384+13 real fills). Follow-ups filed: **A18b** (marks cleanup), **A18c** (fifth copy). Suites 495+166=**661**. Bot stays paused. |
