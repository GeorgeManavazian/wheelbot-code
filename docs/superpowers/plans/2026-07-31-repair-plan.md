# Repair plan — live paper wheel bot

**Created:** 2026-07-31 · **Status:** IN PROGRESS — 22 of 67 done (A2-A7, A11, A12, A14, A16-A19, A22, B1-B5, B7, B9, B10; A21/E8/A18b/A18c/A17b/A3b/A4b/C16/A23 added) · Group B remaining: B6 (owner: universe re-vet), B8, B11 · **Bot state:** PAUSED
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

**OWNER RULING 2026-08-01 — the 25 accounts are ALTERNATE UNIVERSES, not one pooled
brokerage.** Each account is an independent counterfactual; liquidity and size are judged per
account against the market, NEVER aggregated across accounts. This retires the audit's
cross-account stacking argument ("258 contracts into one strike across accounts"); the
per-account form survives (one account's 258 vs ~84/day ADV is still fantasy inside its own
universe). The deferred size-cap decision must be framed per-account.

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
| A19 | Capture `openInterest`/`totalVolume`/`bidSize`/`askSize` from the Schwab response | MED | **DONE** | see A19 evidence block below |
| A23 | `--smoke` isolation imperfect: its zombie path appends to the REAL `gaps.jsonl` and sends a real alert on a pull failure (state/trades/snapshots correctly go to `_smoke`) | LOW | TODO | filed from the A11 skeptic, 2026-08-01 |
| A21 | `merge_held_legs` quote pull happens at 17:00, post-close (`run_daily.py:192` → `held_legs.py:113`) — same staleness class as A16 but marks/dashboard only, not entries | MED | TODO | Split out of A16 by owner decision 2026-07-31. Affects held-leg marks, snapshot equity (`snapshots.py:26`), and the EOD TP branch (`portfolio.py:94-101`); the intraday TP already runs on RTH quotes. |
| A3b | Covered-call unclosability gate (owner 2026-08-01: **middle path** — A3 arithmetic guard extended to calls; calls stay EXEMPT from the A2 liquidity gate) | MED | **DONE** | see A3b evidence block below. Skeptic CORRECT-BUT-INCOMPLETE → 2 amendments same session (router quiet-run pin killing the surviving warn-always mutant; wheel/router uncovered-day pins). **C16b filed** (skeptic F1: a daily-refused call logs but never emails — needs an N-consecutive-days escalation; two-tier vs A4's unreachable email, declared). **F4 declared:** pre-A3b backtest numbers involving calls are non-comparable (SEEN-ticker drift up to ±$10k final cash from removed sub-floor call paths; goldens unaffected — they sell zero calls). |
| A22 | `chain_frame` stamps `from_date`/`to_date` from the **box (UTC) clock**, not ET | MED | **DONE** | folded with B7 into the Group B batch; both pull functions frozen-clock tested | Found by the A16 analyst. `obs_date` is threaded correctly; only the request dates are wrong. Dormant on the new RTH snapshot path (UTC date == ET date at 15:xx ET) but live on `--smoke` and any manual post-19:00 pull. |
| A17 | Represent partial fills / working-order state | HIGH | **DONE** | see A17 evidence block below. Follow-up filed: **A17b** — `run_intraday.py` saves state only `if trades`; a working order placed without an immediate fill would not persist. Inert until a fill model creates working orders; must land with that model. |
| A1 | ~~Intraday fill realism~~ **DEFERRED — strategy decision, see owner decision above** | CRIT | DEFERRED (owner) | resting limit and next-poll both rejected on evidence; spread-fraction k≈0.5 is the supported candidate |
| A2 | Liquidity gate (rel-spread/OI/volume) — **gate yes, cap parameter DEFERRED** | CRIT | **DONE** | see A2 evidence block below. Cap parameter still deferred (per-account per the 2026-08-01 alternate-universes ruling). **Provisional owner decisions (dialog declined; veto cheap):** covered calls NOT gated · rel-spread = (ask−bid)/computed midpoint @ 0.10 · gate ON in FROZEN, dataclass defaults OFF. |
| A3 | Minimum credit / maximum spread / unclosable-by-construction guard | HIGH | **DONE** | see A3 evidence block below. Max-spread clause **discharged by A2** (no parameter-free residual). The RIG 70.2%/VALE 26.0%/AGNC 22.4% surrender evidence is the TP policy on a micro-credit population → **moved to A20** (percentage cut = strategy). New row **A3b**: covered-call side (22.6% of backtest call entries sell at ≤ $0.02 vs 1.05% of puts; refusing one leaves shares naked → owner decision, not guessed overnight). | **Sized during A6** (skeptic, real trade log, 145 SELL_PUTs, `commission_per_contract=0.65`): on micro-credit names a leg that used to expire free is now bought back at the $0.01 tick, surrendering **RIG 70.2% / VALE 26.0% / AGNC 22.4%** of banked premium (RIG: $6.60 to close $9.40 banked). 144 of 145 entries have a TP trigger reachable at the minimum tick. This is the minimum-credit case in dollars. |
| A20 | **TP=0.60 is out of sample on the population A6 admits** | HIGH | **BLOCKED (owner)** — this IS the deferred "should TP=0.60 exist" strategy question; not decidable in the overnight run. Skipped 2026-08-01, not forgotten. | **+ evidence moved from A3 (2026-08-01):** the tick-close surrender fraction (t*m+c)/(credit*m−c) is a pure per-contract credit curve — 122% @ $0.02, 89% @ $0.025, 70.2% @ $0.03 (RIG), 22.4% @ $0.08 (AGNC), 18.6% @ $0.095 (the A2-implied floor), 4.2% @ $0.40. Any cut other than the 100% crossing ($0.023, now enforced by A3) is a strategy parameter and belongs to this row's TP decision. Realized: cheapest close ever printed $0.03; AGNC legs kept only 50.3% of banked. Found by the A6 skeptic. `src/engine_v2/options/chain.py:42` and `live/data.py:66-67` both filter `bid > 0`, so a `0.00 x 0.01` row is **unrepresentable in the backtest chain** — the frozen TP policy was never measured over it. Not created by A6 (`rows_from_quotes` opened it for EOD on 07-31), but A6 extends it to the path producing 100% of realized P&L. |
| A4 | Covered-call window — reach the basis floor | HIGH | **DONE** | see A4 evidence block below. Follow-ups: **A4b** (solo backtest engines keep the silent covered-call no-op — warning is live-path only), **C16** filed (surface `days_shares_uncovered` on the dashboard — it still has no live reader). **A3b now urgent** (skeptic F4): the splice converts "unreachable" days into deep-OTM micro-credit call sales with NO liquidity/unclosability gating — the exact population A3b asks about, now growing. Answer A3b soon. |
| A5 | Same-day re-entry guard must see intraday closes | HIGH | **DONE** | see A5 evidence block below. Skeptic-found sibling gap (EOD closes left no self-note for the crash-retry window) amended in the same fix. | **A6 is an amplifier for this** — `portfolio.py:84` rebuilds `closed_today = set()` inside `step_one_day` and `manage_intraday` persists nothing, so every intraday close is invisible to the guard, and A6 exists to increase intraday closes. Bound: the newly-admitted population is deep-OTM/near-worthless, least likely to be re-selected at 0.30 delta, so the marginal exposure from A6 alone is probably small. |
| A6 | Intraday must see a 0.00 bid (align with `rows_from_quotes`) | HIGH | **DONE** | see A6 evidence block below |
| A7 | Intraday holiday + quote-freshness gate | MED | **DONE** | see A7 evidence block below. Minutes-scale intra-session staleness deliberately left to **C1**; EOD held-leg staleness stays **A21**; dashboard marks (`live_marks`/`live_asks`) still show prior-session prices on holidays — display-only, noted for **A18b**. |
| A8 | Early assignment modelling | MED | TODO | |
| A9 | Defer the covered call one session after assignment | MED | TODO | |
| A10 | Corporate actions — at minimum detect and refuse | HIGH | TODO | |
| A11 | Clock gate inside `run_daily` (refuse before 17:00 ET without `--force`) | MED | **DONE** | see A11 evidence block below. Boundary tightened 16:00→17:00 per its skeptic (Schwab's bar isn't settled until ~17:00 and `already_stepped` locks a half-baked day in). New row filed: **A23** — `--smoke` isolation is imperfect (its zombie path writes the REAL gaps.jsonl + a real email on a pull failure; pre-existing, skeptic F5). Cosmetic: run_health strings still say "20:00 window" (stale, noted). |
| A12 | Budget allocation must not strand capital at small sizes | MED | **DONE** (+ owner amendment 2026-08-01) | see A12 evidence block below. Skeptic-F3 routing preference resolved by owner 2026-08-01: fallback flipped to **richest-ranked-first at any k**, sized at the largest k (least concentration) that affords it. Fresh skeptic on the flip: **SURVIVES** (4,000-trial differential fuzz vs HEAD, phantom money 0/8,000 runs, fallback n=1 in all 1,286 fallback trials, equal-split path identical in all 2,714 non-fallback trials, gate resurrection impossible, 4 mutants killed incl. exact-old-behavior revert). Declared (skeptic F8): fallback route_events record only the chosen name, so the audit referee cannot detect a wrong fallback pick — the two new tests in `test_budget_split.py` are the defense. |
| A13 | Model exchange/OCC/regulatory and assignment fees | LOW | TODO | |
| A14 | Surface `StepResult.warnings`; bound the unsettleable-expiry refusal | HIGH | **DONE** | see A14 evidence block below. **Incident logged there too:** the A14 skeptic's probe wrote test states into the LOCAL frozen archive (`data/live/accounts/100k_N1..N6`) via the WHEELBOT_STATE_DIR import-time trap — self-reported, recovered same session (N6 deleted; N1 byte-exact from `data/live-synced@a8ea1f8`; N2-N5 nearest-frozen, one intraday session off; polluted snapshot rows stripped). Canonical VPS store untouched; Phase F resets all accounts anyway. Process rule saved to memory: main()-touching probes need the env pre-set in a fresh interpreter. |
| A15 | Restore a bounded reach-back for the batch engine | MED | TODO | |

### Group B — what the bot SEES

| ID | Fix | Sev | Status | Evidence |
|---|---|---|---|---|
| B1 | Drop non-positive/non-finite closes; report them; alert if recent | CRIT | **DONE** | Group B evidence block below. "Alert if recent" satisfied via B2 (an all-garbage feed raises) + the F5 declared degrade. |
| B2 | Empty/stale payload is a failure, not a holiday | CRIT | **DONE** | Group B evidence block below |
| B3 | One malformed contract must not discard the ticker | MED | **DONE** | per-contract AND per-expiry-group containment (skeptic F1 amendment) |
| B4 | `zombie_check`: held legs as a third, fully-required population | HIGH | **DONE** | `held_marks_failed`: wholesale = endpoint ANSWERED for nothing (skeptic F2: a 0×0-but-answered book must not wedge the night). `no_chain`-wholesale hole declared (F8, matches stated scope). Wiring untested (declared, E8-class). |
| B5 | Held ticker outside `UNIVERSE` must be an error, not silence | HIGH | **DONE** | better than an error: the held-outside ticker is PULLED (closes + chain, marks + TP restored); ratio denominators corrected to the pulled population (skeptic F3). `is_trading_day` denominator drift declared (negligible at 542-name scale, zero at smoke scale). |
| B6 | Re-vet the universe; add a `_RETIRED` frozenset with a test | HIGH | TODO | |
| B7 | `chain_frame` must use the ET obs date, not the box clock | MED | **DONE** | folded with A22; both pull functions ET-stamped, frozen-clock tested |
| B8 | Truncated history must be recorded and logged | MED | TODO | |
| B9 | Reject `underlyingPrice <= 0` | MED | **DONE** | Group B evidence block below |
| B10 | Skip non-standard / `multiplier != 100` contracts | LOW | **DONE** | string-"100" coercion proven safe; `nonStandard="true"`-string hole declared theoretical (Schwab emits real booleans). |
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
| C16 | Surface `days_shares_uncovered` per account on the dashboard (incremented + persisted, zero live readers) | MED | TODO | filed from the A4 analyst, 2026-08-01 |
| C16b | Escalation for the A3b refusal class: N consecutive `call_gated_unclosable` days on one ticker → email (today it logs daily, forever, and never emails — while A4's floor-above-window class emails daily; same physical condition, two tiers). Needs persisted per-ticker consecutive-day state; owner picks N. | MED | TODO | filed from the A3b skeptic F1, 2026-08-01. Measured: SLV router-BASE(basis) backtest shows 295 gated-warning days (multi-week naked stretches are real, not hypothetical); live book currently has zero CALL-phase holdings so the class is prospective. |
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

## Group B batch — evidence (B1 B2 B3 B4 B5 B7 B9 B10 + A22, completed 2026-08-01, overnight)

**In plain language.** The bot's data intake believed everything it was handed. Now: fake prices
(zero/negative/NaN/infinite closes) are thrown out loudly (B1); a blank or all-garbage price
feed RAISES instead of impersonating a holiday (B2); one scribbled contract — or a scribbled
expiry heading — skips that line/group, never the whole ticker (B3+F1); an impossible underlying
price is refused (B9); split-adjusted/non-standard contracts and wrong multipliers are skipped
(B10); a ticker the bot still HOLDS but dropped from its shopping list keeps getting prices and
a chain, so its marks and take-profit never freeze (B5); a wholesale held-book quote failure is
a FAILED run that retries — but a worthless-yet-answered 0×0 book is not mistaken for an outage
(B4+F2); and both chain requests stamp their date window from Eastern time, never the UTC box
clock that is already "tomorrow" from 19:00-20:00 ET (A22/B7).

| Gate | Evidence |
|---|---|
| 1 Reproduce | 10 red tests, one per defect (garbage closes leaked · empty payload quiet · malformed contract killed the ticker · garbage expiry group killed the ticker · und≤0 admitted · nonStandard admitted · held-outside frozen · wholesale-failure predicate absent · UTC-tomorrow request window). |
| 2 Minimal fix | `data.py` (filters/raises/ET dates), `market_live.py` (universe∪held pull), `run_daily.py` (`held_marks_failed` + wiring + pulled-population denominators). |
| 3 Suites | `529` backtest + `192` live = **721**. |
| 4 Mutation | 7 mutants killed cache-safe (keep-everything closes · empty-is-quiet · und<0-only · rows-raise-again · box-clock · B4-never-fails · held-extras-dropped · nonstandard-admitted) + 2 amendment mutants (unquoted-counts-as-failure · expiry-group-unguarded). |
| 5 Line audit | All fail-loud or fail-clean conversions; no admission loosened anywhere (B10/B3 only tighten). One pre-existing test gained a `merge_held_legs` stub — group skeptic verified it was REQUIRED isolation (the import-time store trap) and its original assertions are intact. |
| 6 Blast radius | `closes_from_json`/`chain_from_json` consumers: LiveMarket, snapshot runner, smoke tool — all verified. `zombie_check` signature untouched; three run_daily call sites now pass the pulled population. |
| C1 Skeptic (group) | **CORRECT-BUT-INCOMPLETE** → all three findings **amended + mutant-killed**: F1 per-expiry-group containment · F2 answered-vs-quoted distinction (the 0×0 night-wedge killed) · F3 ratio denominators. Declared: F5 mass today-bar corruption reads as a quiet holiday (strict improvement over booking marks at 0.0; misclassification is silent — noted); F8 `no_chain`-wholesale stays print-only per B4's scope; `is_trading_day` denominator drift negligible at production scale. Verified clean: B5 end-to-end through step + RTH snapshot phase; A22 on BOTH pull functions under frozen UTC-tomorrow clocks; suites re-run independently. |
| C2 Dry run | Not applicable (pure data-shape fixes; the live pulls already run through these functions daily — C2 for A16/A19/A4 exercised the same paths against live Schwab). |
| C3 Dollars | Prevention-class. Historicals from the audit: 2026-07-24 (an outage stamped as a completed day) is B2's class; the TMO frozen-mark $153k-divergence lesson is B5's class for retired-but-held names. |

---

## A12 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** The bot split its allowance evenly into N piggy banks; if no contract was
cheap enough for one bank's share it bought NOTHING — $5k/N5 offered $1,000 a slot, afforded
nothing, and sat 82% idle (one campaign in seven sessions) while the whole pot could buy a $3k
contract. The capital×N grid was measuring affordability, not N. Now: even split first (byte-
identical whenever anything fits — that's what the fingerprints and 270 options tests pin),
and only when NOTHING fits does the money pour into fewer banks, down to one. Concentration
only when the alternative is idleness.

| Gate | Evidence |
|---|---|
| 1 Reproduce | $5k/N5 vs a $30 strike → `AssertionError: A12: $5k sat idle while a $3k contract was listed`. |
| 2 Minimal fix | Pool-then-size restructure of the routing loop; the k=empty_slots pass IS the old budget; all A2/A3/regime gates untouched; route_events shape unchanged (both consumers verified). |
| 3 Suites | `529` backtest + `182` live = **711**; fingerprints identical. |
| 4 Mutation | 2 mutants killed (no-fallback · always-concentrate), independently re-killed by the skeptic. |
| 5 Line audit | Committed formula untouched (PUT collateral only — pre-existing semantics). Warning dedup added post-skeptic (F4). |
| 6 Blast radius | route_events consumers: audit referee + one test, both shape-compatible; fallback events record the affordable subset (observability note, declared). |
| C1 Skeptic | **SURVIVES.** Head-to-head vs HEAD across 5 scenarios: no phantom money (collateral ≤ cash in every scenario, premium-crediting matches old semantics); best-AFFORDABLE wins under sort; audit-shape day goes 0% → 72% deployed; oversizing impossible at fallback (proved n=1 bound); CALL-phase shares correctly outside `committed` both versions; byte-identical when affordable. F3 recorded as the owner note above; F4 deduped. |
| C2 Dry run | Not a market-data fix. The four $5k accounts are the live population this activates for on resume. |
| C3 Dollars | Historical: 5k_N5 ran 18.0% mean utilization with $4,105 idle — the grid's N-comparison was confounded for every $5k tier. Prospective: small tiers deploy or honestly idle, never fake-idle. |

**Owner amendment 2026-08-01 (in-person):** fallback pick order flipped from least-concentration to **richest-ranked-first at any k** (skeptic-F3 example: $2,000 on the worst-ranked vs $3,000 possible on the best — the bot now takes the $3,000). Sizing stays least-concentration-that-fits (n=1 bound preserved). Gates: red test (`test_fallback_buys_richest_ranked_not_cheapest`, failed with CHEAP-not-RICH) + sizing pin (`test_fallback_sizes_at_least_concentration_that_fits`) → minimal diff in the fallback block only → suites 530+192=722 → 2 size-changing mutants killed cache-safe (reversed pick order; ascending k) → line audit + blast radius (route_events consumers shape-verified) → fresh skeptic **SURVIVES** (findings F1–F10 in its report; F8 declared: audit referee blind to fallback pick order, tests are the defense). Goldens sell no calls/unchanged — equal-split path proven identical in 2,714/2,714 non-fallback fuzz trials.

---

## A14 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** The engine writes worry-notes (`warnings`) about everything it couldn't
do — the worst being "this leg is past expiry and I cannot settle it because its closing price
never arrived." The daily runner used to surface only the gate/covered-call notes and throw the
rest away, so a stuck leg (delisting, corporate action) could sit broken forever in silence.
Now EVERY note kind reaches the run log (generic by reason, so future kinds can't slip back
into the void), and stuck expiries escalate: one email per day naming each leg and how many
days late it is.

| Gate | Evidence |
|---|---|
| 1 Reproduce | Unsettleable TMO leg stepped through `paper_step` → warning present in the result but ABSENT from stdout → red. |
| 2 Minimal fix | Generic reason-printer in `paper_step` + `collect_unsettled()` + one deduped daily alert in `main()`. |
| 3 Suites | `526` backtest + `182` live = **708**. |
| 4 Mutation | 2 mutants killed (surfacing void restored · collector blinded). |
| 5 Line audit | Additive. Skeptic F1 (Contract subjects printed as raw dataclass repr; the intended branch was dead behind `map(str)`) — **amended** with explicit formatting; its caution honored (naive de-`map(str)` would crash sorting None against str). |
| 6 Blast radius | Print set `_handled` failure mode is a double-print — loud in the safe direction (skeptic F4, accepted). `expiry_unsettleable` produced at exactly one site inside `d >= expiry` → lateness ≥ 0 by construction; contracts always dataclasses post-load (skeptic-verified). |
| C1 Skeptic | **SURVIVES.** All warning shapes through one step without crash; 6 accounts × same stuck leg → ONE alert, leg named once, max lateness; clean day → zero alerts; retry tick cannot re-spam (already_stepped empties the collector); new tests proven red on HEAD. Its venv note: the "1 failed" backtest it saw was the suite run under the WRONG interpreter (pandas alias drift) — the real `.venv` run is 526 green. |
| C2 Dry run | The real TMO 512.5P leg (unsettled since the pause, by design) is exactly what the first post-resume run will now name and alert on — the mechanism will demonstrate itself on day 1. |
| C3 Dollars | Escalation-only ($0 change). Value: a leg stuck by delisting/corporate action now costs at most one day of silence instead of unbounded. |

**INCIDENT (disclosed in full).** The A14 skeptic violated its read-only constraint by accident:
`WHEELBOT_STATE_DIR` binds at import time, its harness set the env after import, and
`run_daily.main()` wrote test states into the local FROZEN archive (`data/live/accounts/`),
including creating `100k_N6`. It self-reported with recovery commands and wrote nothing further.
Remediated by the main session same hour: N6 deleted · N1 `state.json`+`.prev` restored
byte-exact from `data/live-synced@a8ea1f8` · N2–N5 restored from the same commit (nearest-frozen;
one VPS intraday session newer than the 07-28 cutover freeze — display archive only) · the five
appended 2026-08-01 snapshot rows stripped. The canonical store (VPS + GitHub mirror) was never
touched. Process rule saved to session memory: any probe reaching `main()` must pre-set the env
in a fresh interpreter. This is audit-P1's lesson re-learned in miniature — recorded, not hidden.

---

## A11 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** On 2026-07-24 a catch-up run fired at 04:13 in the morning and booked an
entire trading day using the previous day's prices — nothing inside the program stops it from
doing homework before the answers are posted. Now the decision run refuses to step before
17:00 ET (settled-close time; the only clock rule the shell wrapper enforced from outside),
exits nonzero so no done-marker is written and the normal 17:00–23:30 retry window proceeds as
designed. `--smoke` (throwaway connectivity check) and `--force` (manual backfill) bypass it;
`--force` provably bypasses ONLY this gate (grepped: one conditional).

| Gate | Evidence |
|---|---|
| 1 Reproduce | Frozen clock at 04:13 → old code proceeds to build a Schwab client (sentinel raised) → test red; fixed code returns 2 before any client exists. |
| 2 Minimal fix | One gate + `--force` flag + refusal message; boundary 16:59/17:00 pinned by frozen-clock tests. |
| 3 Suites | `526` backtest + `180` live = **706**. |
| 4 Mutation | 2 mutants killed (gate off · refusal-writes-marker rc 0). |
| 5 Line audit | Gate sits before the client build (a refused run touches nothing). Incomplete-snapshot test gained `--force` — necessary, not a weakening (suite runs pre-17:00; and it now pins that `--force` does NOT skip the snapshot gate). |
| 6 Blast radius | `args.force` read exactly once. Tick marker written only on rc 0 (read); dead-man's switch records an all-refused day as `no_run` + alert (skeptic-verified path). |
| C1 Skeptic | **SURVIVES.** Boundary proven by execution; global-datetime monkeypatch checked for leaks both orderings (none; noted for future xdist); pathological-clock day ends recorded not lost; `--smoke` store isolation verified for state/trades/snapshots with the gaps.jsonl hole filed as A23. Its F1 (16:00 vs 17:00 data-settled) **adopted** — gate tightened + boundary test. |
| C2 Dry run | Real 04:26 ET invocation refused instantly, rc 2, no client build — the gate observed working against the live clock. |
| C3 Dollars | Historical: the 2026-07-24 class — a full fabricated day booked at stale quotes across 25 accounts, permanently stamped complete. Prospective: unreachable from any manual or drifted-cron run. |

---

## A7 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** On ~9 weekday holidays a year the exchange never opens, but the bot's
clock gate deliberately knows no holidays, and Schwab happily repeats *yesterday's* prices — so
the bot once booked a Thanksgiving trade against Wednesday's book. Now every live quote's date
tag is checked: stamped on a previous session's ET date (or missing, or unconvertible) → thrown
out like an absent quote, loudly, and the take-profit simply waits for the EOD run. Parameter-
free — no vendored holiday calendar to rot; the data itself says "this price is an echo."

| Gate | Evidence |
|---|---|
| 1 Reproduce | Prior-session-stamped quote served by old code → `AssertionError: A7: a prior-session quote must be refused, not traded on` (behavioral; fixture stamped with a fixed past session). |
| 2 Minimal fix | ~20 lines in `contract_quotes` (stamp → ET date → same-session check, per-leg try/except, printed refusals). Nothing else touched. |
| 3 Suites | `526` backtest + `178` live = **704**. |
| 4 Mutation | 3 mutants killed cache-safe: prior-session admitted · unjudgeable-book admitted · (revived) negative-bid guard deleted — the last one is the skeptic's F2 proof, see below. |
| 5 Line audit | Gate sits AFTER the bid/ask admission clauses (their order is the A6-amendment lesson, untouched). Four pre-existing fixture payloads gained fresh stamps — assertions unchanged, verified additions-only by the skeptic. |
| 6 Blast radius | `contract_quotes` has one production caller (`run_intraday`); refusal == absent quote == leg skipped, the pre-existing degrade path. `held_legs`/`live_marks` deliberately untouched (A21/A18b). Backtest imports nothing from `live/` — its green is declared trivially so. |
| C1 Skeptic | **CORRECT-BUT-INCOMPLETE** → all three findings **amended + verified same session**: F1 pathological timestamps (inf / 1e16 / unit drift) raised out of the whole account's tick — now per-leg refusals (test spans all three shapes); F2 my fixture stamps had silently vacated three old refusal tests — proven with a surviving negative-bid mutant, fixtures stamped, mutant re-run and killed; F3 duplicate `_FakeClient` shadowed the original and killed `.json()`-branch coverage — renamed. Proven clean: DST/UTC-midnight conversions both seasons; no false refusal reachable inside the 9:30-16:00 window (nearest midnight ≥9.5h away); log noise bounded to the holiday's own file with no spurious alert greps; new tests non-vacuous against pre-A7 code. |
| C2 Dry run | Not applicable live tonight (market shut — every real quote would correctly refuse; that IS the gate working, but proves only the refusing half). The serving half is pinned by the fresh-stamp tests. |
| C3 Dollars | Historical: the Thanksgiving `CLOSE_PUT @ 0.39` class — trades dated on days the exchange never opened, unreconcilable against any statement. Prospective: ~9 days/yr of phantom-trade risk closed. |

---

## A5 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** The bot has a morning robot (intraday manager) and a 5pm robot (EOD
step). The morning robot could buy a position back to lock profit, and the 5pm robot — with no
memory of the morning — would sell the *exact same contract* again the same day. Worse every
day, because A6/A16 exist to increase morning closes. Now every close leaves a date-stamped
sticky note on the account (`intraday_closed`, round-tripped through the state file), the 5pm
robot reads today's notes into its anti-churn set, and yesterday's notes correctly stop
counting. The skeptic then proved the 5pm robot didn't leave notes for ITSELF either (a
crash-between-save-and-snapshot retry re-sold its own close) — amended: EOD closes are recorded
in the same list.

| Gate | Evidence |
|---|---|
| 1 Reproduce | Intraday close at 10:00 → same-day step → `AssertionError: A5: EOD step re-sold the contract the intraday manager closed today`; plus the round-trip variant (guard evaporated on reload). |
| 2 Minimal fix | `PortfolioState.intraday_closed` (default-empty; optional in the state file — last_ask precedent, legacy files byte-identical) · append-with-prune in `manage_intraday` and (amendment) the EOD TP branch · seed `closed_today` from today's entries in `step_one_day` · round-trip in `state.py`. |
| 3 Suites | `526` backtest + `174` live = **700** (final post-amendment run). |
| 4 Mutation | 4 mutants killed cache-safe: seeding removed · stale dates seed too · never persisted · EOD self-note dropped. |
| 5 Line audit | All additive. Declared: the list carries stale entries indefinitely when no new close prunes them (harmless — date check; cosmetic). Partial fills append `c` while the leg lives (A17 interplay) — probed harmless: TP/expiry are structurally ungated by `closed_today`, only entry/covered-call read it. |
| 6 Blast radius | `intraday_closed` touched only by `portfolio.py`, `live/intraday.py`, `live/state.py` (repo-wide grep). Backtest engines never touch `PortfolioState`. Snapshots unaffected. Fingerprints byte-identical — **declared trivially so** (every harness scenario runs the seed over an empty list); the proof is the 5 tests + the skeptic's mutant-equivalent probes. |
| C1 Skeptic | **COULD-NOT-BREAK.** Contract equality across the JSON round trip holds under adversarial variants (int strike, np.float64, isoformat); a time-component expiry would break set membership but is unreachable (every chain expiry is normalized; every real state file carries midnight expiries — grepped). No unintended blocking: PUT close doesn't block same-strike CALL; other tickers unaffected; partial-close interplay harmless; 23:30 same-day retry holds, next-day releases; tick window confirmed never midnight-crossing. Its F1 (EOD self-note) **amended + mutant-killed**; F2 (stale entries persist) cosmetic, declared. |
| C2 Dry run | Not a market-data fix. The live book's four held legs are all PUT-phase with no intraday closes recorded — the guard activates the first day the intraday manager closes anything after resume. |
| C3 Dollars | $0 today (bot paused). Historical shape: every same-day churn pair was sell-at-bid + buy-at-ask on the same contract — paying the full spread twice for nothing; frequency scales with intraday TP activity, which A6/A16 deliberately increased. |

---

## A4 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** After the drawdown that gets the bot assigned, the rent it must charge
(the basis floor) sits ABOVE the top of its price list — the 12-strike window reaches ~+3.8%
over spot, the floor needs +10-15%. So the covered call could never be selected, the income half
of the wheel never started, the shares sat naked, and nothing said a word (TMO class, months).
Fix: for CALL-phase holdings the snapshot pass now asks Schwab for **every listed OTM call**
(no width guess — a count-based fix would need strike_count≈100 per the 10,810-ticker-day
measurement) and staples the strikes above the window into the chain, additively only; the
engine warns `covered_call_unreachable` whenever a floor still cannot be reached, `paper_step`
prints it, and all 25 accounts fold into ONE alert email. A daily-repeating single-ticker alert
doubles as a live corporate-action tripwire (A10: splits push the floor to ~2× spot).

| Gate | Evidence |
|---|---|
| 1 Reproduce | Floor 79 vs window top 72.5 → `AssertionError: A4: shares sit naked and the engine said nothing` (no trade AND no warning on old code). |
| 2 Minimal fix | `otm_call_frame` (CALL+OTM, same date window — CALL-only is load-bearing: ALL would add far-OTM puts as entry candidates) · `additive_call_rows` splice filter · floors-from-state in the snapshot runner + coverage print · engine warning · one deduped alert. Backtest engines untouched (A4b). |
| 3 Suites | `521` backtest + `174` live = **695**. |
| 4 Mutation | 4 mutants killed cache-safe: warning silenced · splice admits new expiries · `>`→`>=` at the window top · delta-sanity guard dropped. |
| 5 Line audit | All additive; primary 12-strike pull untouched (normal-case floor-below-spot provably unchanged — splice adds only strictly-above-top rows whose deltas sit further from target). **Fingerprint caveat declared honestly (skeptic F5): the byte-identical fingerprints prove NOTHING for this change — instrumented, the modified branch executes zero times in that harness.** The proof is the test suite + skeptic probes instead. |
| 6 Blast radius | `otm_call_frame`: 1 caller (snapshot runner). `additive_call_rows`: 1 caller + tests. Warnings: paper_step + main alert. Put selection proven bit-identical pre/post splice (skeptic). Snapshot round-trip proven (spliced rows carry all `_CHAIN_COLS` + A19 columns). |
| C1 Skeptic | **CORRECT-BUT-INCOMPLETE** → all three real findings **amended same session**: F1 garbage-delta hijack (a far-OTM spliced row claiming delta≈0.49 would win selection even with the floor reachable — monotonicity guard added: spliced |delta| must sit strictly below the primary's per-expiry min, + test + mutant) · F2 one corrupt state file (`premium: null`) crashed the WHOLE snapshot runner vs the 17:00 step's per-account isolation (per-position try/except added) · F3 `covered_call_no_mark` proven dead code (a selected contract always marks) — branch deleted, test tightened. F4 recorded as the A3b escalation above. Everything else held: round-trip, alert-exactly-once across 25 accounts incl. exception paths, all degrade paths fail soft, no put-selection pollution. |
| C2 Dry run | Live Schwab GDX: 12-strike window top **+3.9%** over spot; OTM pull top **+43.0%** (106 strike), 90 call rows, **66 additive rows** through the real splice filter. The analyst's one unverified assumption (does Schwab honor `strike_range=OTM` unbounded) — verified live. |
| C3 Dollars | Today: $0 (zero CALL-phase holdings in the real book — 4 held tickers, all PUT phase). Historical shape: at a 10% post-assignment drawdown the old window blocked the covered call on **31.0%** of 4,411 real ticker-days (audit); every blocked day was rent never collected on shares already owned. |

**Declared (A1/A5):** the floor-reaching calls are micro-credit (median bid $0.07 at 10% dd,
$0.04 at 15%) and now sell UNGATED — that is the pending A3b owner decision, made more frequent
by this fix; flagged, not decided. Parquet strike grids are ±15-banded so the width measurement
extrapolates beyond (exact where checkable). `strike_count=N` → N/2 per side established on one
fixture + the audit's independent figure.

---

## A3 — evidence (completed 2026-08-01, overnight autonomous run)

**In plain language.** The bot sold a WBD put for 1¢ whose "winning exit" required buying back
below half a cent — a price that does not exist (prices move in whole cents), and even the
cheapest real buy-back loses money after both commissions ($0.35 banked, $1.65 to close). The
new guard computes, from the config's own arithmetic, the cheapest sale whose take-profit exit
is (a) a real price and (b) not a guaranteed loss — `credit ≥ max(tick/(1−tp),
2·commission/(tp·mult))`, $0.025 under FROZEN — and refuses anything cheaper, loudly.
Parameter-free (no new config field): both conjuncts are zero-crossings, not judgement calls.
The judgement-call part of the old A3 row (what % surrender is acceptable) moved to A20; the
max-spread part was already discharged by A2.

| Gate | Evidence |
|---|---|
| 1 Reproduce | The real WBD shape (bid $0.01, ask $1.53, tp 0.60) → `AssertionError: A3: engine sold an entry whose TP exit is unsatisfiable / assert ['SELL_PUT'] == []`. |
| 2 Minimal fix | `MIN_TICK`/`tp_exit_floor`/`tp_exit_feasible` in `fills.py` (co-located with the TP arithmetic they mirror) + veto at the three put-entry sites and the roll destination + `paper_step` print extended. Calls untouched (A3b). |
| 3 Suites | `517` backtest + `170` live = **687**. Goldens inert by construction (min golden-fixture entry bid $0.79). |
| 4 Mutation | 4 mutants killed cache-safe (commission conjunct dropped · `>=`→`>` · inertness dropped · portfolio wiring off) + the skeptic independently killed 4 more on a scratch copy and CAUGHT one survivor (tp≤0 leg unpinned) → fixed + pinned. |
| 5 Line audit | Predicate + 4 vetoes + warnings + comments. **Declared delta:** always-on guard vetoes ~68 micro-credit entries across pre-existing backtest-suite runs (all $0.01–$0.02 credits — exactly the WBD class) without breaking any assertion; skeptic verified no affected test's purpose rested on a vetoed trade (F6, executed diff: 2 SLV entries in the selector tests, run ends slightly higher). |
| 6 Blast radius | `tp_exit_feasible`: 4 production call sites + tests. No config field → nothing else to audit. `run_intraday`/snapshot unaffected (no entry paths). |
| C1 Skeptic | **COULD-NOT-BREAK.** 370,800-point brute force against the engine's own `try_take_profit` + `sell_proceeds`: **0 soundness violations** (every admitted entry has a real profitable exit; every refusal at repo configs is genuinely broken). Cross-engine verdict identity on borderline chains; roll veto atomic (proceeds None before any arithmetic — proven by runtime-patch diff); both-gates precedence exact (one warning each case); NaN/np.float64/None edges fail safe. Findings F1 (tp≤0 semantics gap — **amended**: infinite floor, refuse-all, pinned) and F4 (silent roll vetoes — **amended**: `roll_gated_illiquid`/`roll_gated_unclosable` warnings + test). F2 declared as comment (over-refusal band exists only at tp ≤ 0.43, unused; conservative-only). F5: veto-count metric differs analyst-vs-skeptic (83 vs 68, different counters) — shape identical, both all-micro-credit. |
| C2 Dry run | Not a market-pull fix; the live population is already refused by A2 in FROZEN (subsumption 3.8×, valid for tp ∈ [0.137, 0.895]). The guard's production value is invariance under any future A2 re-tuning; its backtest value is being the ONLY gate there (A2's OI/volume legs cannot run on historical data). |
| C3 Dollars | Historical: exactly **1 of 145** real entries (the WBD 25P: banked $0.35, tick-close $1.65, net −$1.30/contract — a guaranteed loss sold as income). Prospective: the class can never be sold again under ANY config with a TP exit. |

**Provisional decisions (owner asleep, veto cheap):** P1 always-on, no config field (parameter-free; a knob would double-count A2's R). P2 puts + roll destinations only, calls filed as A3b with the 22.6% number. P3 home = `fills.py`. P4 worst-accepted-fill conjunct (not best-case). **Declared:** tick=$0.01 assumed (permissive-only — under-refuses nickel-tick classes, never over-refuses); no price-improvement model (consistent with the A18 seam).

---

## A3b — evidence (completed 2026-08-01, owner in session)

**In plain language.** The bot could still rent out shares it was stuck holding for a penny — a
deal whose "exit early" door literally does not exist (the buy-back price would be below the
smallest printable price, and fees eat more than the penny earned). A3 built that arithmetic
check for step-1 promises (puts); the owner's middle-path ruling extends it to covered calls at
all three production sites, while calls stay EXEMPT from the A2 liquidity bouncer — refusing a
call leaves shares naked, so only arithmetic impossibility may refuse one. Refused days are
loud (`call_gated_unclosable`, surfaced by the A14 generic log path) and still count as
shares-uncovered days.

| Gate | Evidence |
|---|---|
| 1 Reproduce | 4 red tests failing on exactly the defect (`engine wrote a covered call whose TP exit is unsatisfiable`; 1¢ and 2.4¢ calls sold by portfolio, wheel, router). |
| 2 Minimal fix | `feasible = mark is None or tp_exit_feasible(mark.bid, cfg)[0]` + warning + sale-skip at the 3 SELL_CALL sites (`portfolio.py`, `wheel.py`, `regime_router.py`). No config knob (same P1 ruling as A3). No live-code change needed — A14's generic path surfaces the new kind (skeptic-executed proof). |
| 3 Suites | `539` backtest + `192` live = **731** green. Goldens inert — both golden configs sell zero calls (verified independently by me and the skeptic). |
| 4 Mutation | 3 wiring mutants (gate forced open per engine) killed by the new tests, cache-safe + size-changing; skeptic added 4 more kills (sale-proceeds-anyway ×3, gate-on-ask, router-only inversion) and CAUGHT one survivor (`R_warn_always` — router warning spam passed the whole suite) → amended with a router quiet-run pin, mutant re-killed. |
| 5 Line audit | 3 sites × (feasible line + warning + condition); test file. Declared: refusals do NOT join A4's naked-shares daily email (listed-but-junk ≠ not-listed; C16b filed for escalation). |
| 6 Blast radius | `tp_exit_feasible` callers: puts (A3) + 3 call sites + roll; exactly three SELL_CALL emission sites exist in src/+live/ (skeptic grep — intraday engines delegate). Warning consumers: run_daily `_handled` generic path prints the new kind; `naked_all` email untouched. State purity: refused-day cash/premium/equity bit-identical to a no-call-listed control in all three engines (skeptic-executed). |
| C1 Skeptic | **CORRECT-BUT-INCOMPLETE** → both incompletenesses amended same session (F2 router quiet pin, F3 wheel/router uncovered-day pins). F1 → C16b (escalation rule, owner-facing). Boundaries exact in all 3 engines incl. tp=0 → refuse-all and comm-conjunct binding. Old-vs-new on real SEEN chains: every divergence's first cause is a removed sub-floor call; new engines sell ZERO sub-floor calls; final-cash drift up to ±$10k declared (F4) — pre-A3b call backtests non-comparable. |
| C2 Dry run | Live chain snapshots are VPS-only (absent locally, declared). Against the fresh state mirror (synced 2026-07-31): all 25 accounts hold zero CALL-phase positions — the guard is a no-op on today's real book; no holding is left unwritable. |
| C3 Dollars | Realized: **$0.00 either way** — 264 real trades contain zero SELL_CALL ever (no assignment has occurred live). Prospective: backtest sub-floor share 134/713 (18.8%) of FROZEN-solo call entries across SEEN tickers; the class the A4 splice grows (median 4-7¢) passes the floor — only true pocket lint dies. |

---

## A2 — evidence (completed 2026-08-01)

**In plain language.** The bot ordered 757 crates from a shop that sells 84 a day, and the
backtest pretended someone filled it. Now a bouncer checks three things before any NEW short
put (or roll destination) is sold: spread ≤ 10% of the midpoint, ≥ 250 contracts of open
interest, ≥ 25 traded today. Fail any → veto, loudly logged — never a substitution (a
row-filter would have silently moved the sold delta 0.28 → 0.40; measured). The bouncer never
touches closes, expiry, marks, held-leg rows, or covered calls (provisional). Backtest data has
no OI/volume columns, so backtest configs can only run the spread leg — a declared one-sentence
divergence. Thresholds live on WheelConfig (default-off, the chop-gate idiom); production is ON
in FROZEN. Judged per account, never aggregated (alternate-universes ruling).

| Gate | Evidence |
|---|---|
| 1 Reproduce | `AssertionError: A2: engine sold an entry the liquidity gate must refuse / assert ['SELL_PUT'] == []` on a 36%-spread, 5-OI, 0-volume put with production thresholds. |
| 2 Minimal fix | `liquidity_ok` predicate in `select.py` + veto at 3 put-entry sites + roll destination + 3 `WheelConfig` fields + FROZEN values + `paper_step` gate print (was discarding `result.warnings` wholesale — a fully-gated day would have been invisible). |
| 3 Suites | `509` backtest + `170` live = **679**, on freshly-compiled bytecode (see incident below). Goldens intact. |
| 4 Mutation | 9 mutants killed: gate-always-passes · unmeasurable-= -tradeable · NaN-slips-through · silent-gate (wiring) + crossed-book-guard-deleted · `>`→`>=` · `<`→`<=` · row-missing-passes · mid-column-denominator (body; added after the skeptic showed all five body mutants survived the original tests). |
| 5 Line audit | select.py +52 (predicate), 3 engines (veto + F2 warnings), config, paper_step print, tests. Declared deltas: solo engines now emit `entry_gated_illiquid` warnings (empty when gate off — fingerprints identical); portfolio warning deduped across slot iterations (skeptic F4). |
| 6 Blast radius | `liquidity_ok`: 4 production call sites + tests. `liq_*` fields: predicate + FROZEN only. Gate-off = byte-identical proven twice (my fingerprints + skeptic's HEAD-tree digest). `run_intraday`/snapshot runner import FROZEN but consume no liq keys (skeptic F10, executed). |
| C1 Skeptic | **CORRECT-BUT-INCOMPLETE** — trading path could not be broken: FROZEN end-to-end on real GDX fixtures (gate warning reaches stdout; refusal correct), no candidate shadowing (rich-illiquid A vetoed → liquid B still entered), roll veto atomic (no half-executed roll; leg ran to expiry with correct cash), predicate edges fail closed (crossed, zero, penny, negative books), held-only duplicate latent-only, intraday byte-identical under FROZEN. Incompleteness = the five surviving body mutants (F1) and silent solo-engine vetoes (F2) — **both amended + mutant-verified same session**. |
| C2 Dry run | Live Schwab GDX, post-close book: the contract the bot would have selected (71.5P) **refused at 26.4% rel-spread**; 4/48 put rows pass. Production gates against the RTH snapshot instead, where the historical entry-like median is 4.78% (63.6% of 50-ticker candidates pass) — the gate bites ghosts and thin names, not daylight liquidity. |
| C3 Dollars | Prospective. The audit sized the gate at refusing **114/145 historical entries and 90.4% of contracts** — figures NOT reproducible from this repo (measuring script + OI source external; declared). $0 change to any existing book; the reset discards history anyway. |

**Penny-put consequence, stated out loud:** a book at the minimum tick below ~$0.105 mid can
never pass a 10% spread gate — the gate categorically refuses the micro-credit class. Aligned
with A3's evidence (RIG surrendering 70.2% of banked premium to the closing tick), not in
conflict; but it reshapes which cheap names ever enter, and the owner should know.

**Incident, disclosed (gate-4 integrity):** during mutation testing, a mutant whose text was
byte-for-byte the same length as the original, restored within the same second, left Python
serving **stale mutant bytecode** while the source file was correct — pyc invalidation keys on
(mtime, size). One "restored green" run was meaningless; caught because the M5 body test kept
failing on correct-looking source. Remedied: all `__pycache__` purged, M5 re-run with a
size-changing mutant (killed), both suites re-run on fresh bytecode (679 green), fingerprints
re-verified. Process rule adopted for every future gate-4: mutants must change file size, and
bytecode caches are purged between apply/restore. Recorded in session memory.

**Declared (A1/A5):** audit's rel-spread denominator unknown (midpoint chosen, provisional);
114/145 + 90.4% + ~84/day-ADV unreproducible from repo; RTH-snapshot `totalVolume` assumed
intraday-cumulative and `openInterest` effectively prior-session (OPRA morning publish) —
acceptable for floors, unverified.

---

## A17 — evidence (completed 2026-07-31)

**In plain language.** The state schema had two words for a sold option — "have it" or "gone" —
and no way to write "bought back 4 of the 10, 6 still short". Since 55.4% of hours traded fewer
contracts than the bot's largest instant fill (181), any honest fill model constantly needs that
sentence. This gives the schema the grammar without choosing the model (owner ruling): the seam
reports `filled_contracts`; one shared bookkeeping function (`portfolio.close_short_fill`, used
by the EOD engine and the live intraday manager) decrements the leg and normalizes zero to
`short = None`; the two solo backtest engines decrement inline; expiry re-reads the size so a
same-day partial + expiry settles the remaining contracts, not the pre-fill count (I3);
`opened_contracts` / `working_order` round-trip as optional state fields (the `last_ask`
precedent — **all 25 account files load untouched, zero migration**). The instant-fill default
reduces exactly to the old path.

| Gate | Evidence |
|---|---|
| 1 Reproduce | Duck-typed partial `FillDecision` injected via monkeypatch → `AssertionError: A17: a 4-of-10 fill must book 4 contracts, not the whole leg` and the same-day-expiry sibling; instant-fill guard test correctly PASSED on old code. |
| 2 Minimal fix | `FillDecision.filled_contracts` (trailing default; A18 equality/positional pins safe) · `close_short_fill` helper + 2 call sites · inline decrement + `n` re-read in wheel/router · optional state/snapshot round-trip. Fill model untouched; `run_intraday` save-condition deferred (A17b). |
| 3 Suites | `501` backtest + `170` live = **671** (662 + 9 new tests). |
| 4 Mutation | 4 mutants killed: helper always-fully-closes → 2 fail · stale-`n` re-read removed → 1 · `opened_contracts` dropped on save → 1 · seam reports 0 filled → 1. |
| 5 Line audit | Every change is capacity or the I3 re-read; the re-read is unreachable-today (a full fill nulls `short` before the expiry block) — proven inert by fingerprints. Declared delta: `closed_today` now gains the contract only on a FULL close (identical under instant fill; correct under partials — the contract is still held). |
| 6 Blast radius | `close_short_fill`: 2 callers. `filled_contracts` readers: helper + 2 inline engine sites. State keys: written only when present; `secret_guard`, `sync`, dashboard `monitor` all proven to swallow them (skeptic). |
| C1 Skeptic | **COULD-NOT-BREAK** (behavior). Independent old-tree reproduction: 4-engine fingerprints identical; roll+stop+gates+hourly real-chain runs → identical SHA over 2,916 trades; intraday helper parity incl. dict-shaped contracts + wall-clock stamps to the second; forced 4-of-10 partial → assign 6 → covered-call on 600 shares with correct basis floor → equity to the penny; residual finalizer buys back remaining size; live surface (old files, new keys, dashboard, secret_guard) all clean; `git diff -- tests/` = 74 insertions, 0 deletions. Two capacity notes → **both amended**: F4 ghost-fill (`filled_contracts<=0` now raises; test + suites green) and F5 no-writer (`close_short_fill` stamps `opened_contracts` on the first partial; test). Fingerprints re-verified identical post-amendment. |
| C2 Dry run | Not applicable as a market pull — the capacity is unreachable from live data by design; the skeptic's forced-partial probes on the real engine stack are the execution evidence. |
| C3 Dollars | **$0.00 by construction** — fingerprints byte-identical pre/post (including trade timestamps). Value: the fill-model decision (deferred) is now unblocked on representation. |

**Declared (A1/A5):** close bookkeeping exists in three copies (helper + two solo-engine inline
decrements, whose `campaign_premium` locals don't fit the pos-dict helper) — a future fill-model
change must touch all three; noted here so it cannot be missed. `working_order` has no writer
and no lifecycle — it is a reserved, round-tripped slot only.

---

## A19 — evidence (completed 2026-07-31)

**In plain language.** Schwab's daily option report includes how many contracts exist
(openInterest), how many traded today (totalVolume), and how big the waiting orders are
(bidSize/askSize). The bot threw all four in the trash and later guessed crowd size with an ADV
proxy carrying ~3.6× typical error. Now the four numbers are written down — chain rows and
spliced held-leg rows both — and nothing reads them yet on purpose: the liquidity gate that will
consume them is A2, a deferred owner decision. They accrue from deploy day; Schwab keeps no chain
history, so every uncaptured day is unmeasurable forever.

| Gate | Evidence |
|---|---|
| 1 Reproduce | New value-asserting test → `AssertionError: A19: open_interest discarded from the chain`. Real assertion. |
| 2 Minimal fix | 4 columns appended to `_CHAIN_COLS` + `_num`-coerced capture in `data._rows_for` and `held_legs.rows_from_quotes`. Nothing reads them (proven, C1). |
| 3 Suites | `495` backtest + `167` live = **662** (662 = 661 + 1 new test; backtest imports nothing from `live/`). |
| 4 Mutation | 3 mutants killed: chain field dropped → 1 fail · held-leg capture stripped → 1 fail · quote key renamed (`totalVolume`→`volumeTotal`) → 1 fail (the skeptic-F5 amendment test). |
| 5 Line audit | +46/−1 across `data.py`, `held_legs.py`, tests. All capture + comments; no admission rule, price, or condition touched. |
| 6 Blast radius | `_CHAIN_COLS` consumers: `chain_from_json` (source), `chain_store` validation (new snapshots carry the cols), held-leg column-contract test, `add_chain_rows` (union-of-columns, unaffected). Engines ignore unknown columns — proven by execution, not assumed. |
| C1 Skeptic | **COULD-NOT-BREAK.** Capture-only proven: `step_one_day` byte-identical with columns present / hand-stripped / all-None (trades, cash, equity, positions). Dtype-poison (all-missing, `"NaN"` strings → object columns) harmless through select/mark/splice/step. Snapshot round-trip exact incl. dtypes for float64 / object / mixed-NaN variants. |
| C2 Dry run | Live Schwab GDX chain: **109/109 rows populate all four fields** with real values (e.g. 75P: OI 2,970, vol 2,317, 1,203×43). |
| C3 Dollars | $0.00 — capture-only by construction (C1). Value accrues as data for A2's gate, which the analysts sized as blocking 114/145 entries and 90.4% of contracts once built. |

**Skeptic findings, disposition:** F4 (MED, operational) — a pre-A19 snapshot read by post-A19
code fails validation and gaps the day *declared, not silent*; risk exists only if deployed
mid-afternoon between a snapshot pass and its 17:00 run → **Phase F note: deploy while paused
(as decided anyway), risk zero**. F5 (LOW) — quote-path values were untested → **fixed as an
amendment**: the test payload now carries the four fields at the quote node and asserts values;
key-rename mutant killed. Schwab's real quote-node placement remains unverifiable offline
(declared; chain path IS value-verified live, and held rows are mark-only, bounding the risk).
F6 (LOW, declared) — mixed-liquidity snapshots serialize `NaN` tokens (non-RFC JSON); the whole
pipeline is Python (`json.load` accepts), but a strict-JSON tool touching the state repo would
choke — recorded, not fixed.

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
| 2026-08-01 | **A3b DONE** (owner in session — middle path). A3 arithmetic guard extended to covered calls at all 3 engines; calls stay exempt from A2. 8 tests; 7 mutants killed incl. the skeptic-caught router warn-always survivor (amended same session with a router quiet pin + uncovered-day pins). C16b filed (refusal class logs but never emails — escalation needed). F4 declared: pre-A3b call backtests non-comparable (±$10k SEEN-ticker drift from removed sub-floor paths; goldens sell no calls, unaffected). Realized dollars $0.00 both ways (zero SELL_CALL ever live). Suites 539+192=**731**. |
| 2026-08-01 | **OWNER DECISIONS — GROUP D (ELI10 dialog #2):** (1) **D2 = informational alert** on every holiday-classified weekday, no vendored NYSE calendar. (2) **D5 = warn daily from 3 days runway**, any day/hour; distinct once-daily EXPIRED alert; unreadable-token-file alerts. (3) **D10 = split markers** (`.dailyran` = run completed, `.synced` = mirror updated; sync-only retry each tick). (4) **D14 = GitHub Actions mirror-freshness check ONLY** — owner declined the healthchecks.io dead-man URL (no new accounts); same-day mid-session death detection therefore rests on D1's nightly post-hoc liveness scan + next-morning GitHub check, declared. Group D analyst spec (all rows re-verified against current tree, receipts) delivered 2026-08-01; execution order = enablers → alert/health Python (D3→D12→D4→D2) → sync/state (D8+D13+D11) → tick rewrite (D9+D1+D6+D10+D5+D7 units) → D14+heartbeat. |
| 2026-08-01 | **OWNER DECISIONS (morning review, ELI10 dialog):** (1) **A3b = middle path** — extend the A3 unclosability-arithmetic guard to covered calls (refuse calls whose winning TP exit is impossible/net-negative), but do NOT apply the A2 liquidity gate to calls; naked-share days accepted over guaranteed-dead exits. (2) **A2 provisional trio BLESSED as built** (covered-call liquidity exemption — consistent with A3b ruling; rel-spread = gap÷midpoint @ 0.10 kept after pass-rate walkthrough; ON-in-FROZEN / OFF-in-defaults idiom kept). (3) **A12 fallback routing = richest-premium-first** (flip from least-concentration; owner accepts concentration for income when only one purchase fits). (4) Next work = **Group D**. |
| 2026-07-31 | Audit completed (9 domains). Bot paused: timer stopped and **disabled**. 8 fixes shipped as `4cffd72` + `7022ade`. Plan created; no repair work started. |
| 2026-07-31 | **A6 DONE.** Found the tree dirty and the live suite RED with a prior session's unfinished A6 change; owner ruled finish-A6-before-A16. All 7 gates + C1/C2/C3 recorded above. Skeptic forced an amendment (two regressions the reorder introduced). New findings filed: **A20** (TP=0.60 out of sample on the admitted population), plus evidence added to A3, A5, A18. Suites 484+151=635. Nothing deployed — bot stays paused. |
| 2026-07-31 | Note: today's 9 expiring legs (TMO 512.5P ×9, RIG 4.5P ×8) are **unsettled** because the bot was paused before the EOD run. They settle correctly on resume via the late-expiry path at the expiry day's own close. Not a lost day. |
| 2026-07-31 | **A16 DONE** (laptop restarted mid-session first; tree was clean, nothing lost). Analyst spec → 3 owner decisions → all 7 gates + C1/C2/C3 recorded above. Skeptic (CORRECT-BUT-INCOMPLETE) forced 4 amendments, each tested + mutation-killed. New rows filed: **A21** (held-leg quotes post-close), **A22** (UTC `from_date` in `chain_frame`), **E8** (runner-main wiring tests). Suites 484+166=**650**. Nothing deployed — bot stays paused; the tick-script window block reaches the VPS only at Phase F. |
| 2026-07-31 | **A18 DONE.** Analyst spec (4-engine map, 12 named silent-change risks) → seam `fills.py` → 4 TP call-sites migrated, zero behavior change proven by pre/post fingerprints (byte-identical, all 4 engines) + skeptic old-vs-new tree differential (**COULD-NOT-BREAK**, 29 adversarial scenarios + real-chain roll/stop/gates runs + the unmigrated fifth copy as referee: 0 mismatches on 384+13 real fills). Follow-ups filed: **A18b** (marks cleanup), **A18c** (fifth copy). Suites 495+166=**661**. Bot stays paused. |
| 2026-08-01 | **GROUP B BATCH DONE** (B1 B2 B3 B4 B5 B7 B9 B10 + A22, overnight). 10 red tests → fixes → 9 mutants killed → group skeptic CORRECT-BUT-INCOMPLETE → 3 amendments same session (expiry-group containment; answered-vs-quoted so a worthless 0×0 held book cannot wedge the night; pulled-population denominators). B6 left for the owner (universe re-vet is judgment); B8/B11 remain. Suites 529+192=**721**. |
| 2026-08-01 | **A12 DONE** (overnight). Equal split first, k-descent to concentration only when the alternative is idleness; skeptic SURVIVES (5-scenario head-to-head vs HEAD; audit-shape day 0%→72% deployed; no phantom money) with F3 recorded as an owner routing-preference note and F4 deduped. Suites 529+182=**711**. |
| 2026-08-01 | **A14 DONE** (overnight). Every warning kind now reaches the log; stuck expiries escalate via one daily alert. Skeptic SURVIVES + F1 formatting amended. **Skeptic pollution incident on the local frozen archive disclosed + remediated in the A14 evidence block** (canonical VPS store untouched); WHEELBOT_STATE_DIR import-trap rule saved to memory. Suites 526+182=**708**. |
| 2026-08-01 | **A11 DONE** (overnight). Clock gate ≥17:00 (skeptic-tightened from 16:00: settled-close semantics + already_stepped lock-in hazard), frozen-clock boundary tests, real 04:26 refusal observed. A23 filed (smoke gaps leak). Suites 526+180=**706**. |
| 2026-08-01 | **A7 DONE** (overnight). Parameter-free session-date gate on live quotes (no holiday calendar to rot); skeptic CORRECT-BUT-INCOMPLETE → 3 amendments same session (pathological-timestamp per-leg refusal; three vacated fixtures re-stamped + mutant re-killed; test-double unshadowed). Suites 526+178=**704**. |
| 2026-08-01 | **A5 DONE** (overnight). Date-stamped close notes on the state seed the anti-churn set; skeptic COULD-NOT-BREAK (adversarial round-trip equality, retry windows, partial-close interplay all held) and its one real finding — the EOD step left no note for itself in the crash-retry window — amended + mutant-killed same session. Suites 526+174=**700**. |
| 2026-08-01 | **A4 DONE** (overnight). OTM-call splice for CALL-phase holdings (no width guess; live-verified +43% reach vs +3.9%) + loud warnings + one deduped alert. Skeptic CORRECT-BUT-INCOMPLETE → 3 amendments same session (delta-sanity guard, per-position crash isolation, dead branch deleted). Fingerprint-caveat disclosed (harness never executes the branch — proof is tests+probes). **A3b escalated:** splice grows the ungated micro-credit call population; owner should rule soon. A4b + C16 filed. Suites 521+174=**695**. |
| 2026-08-01 | **A3 DONE** (overnight run). Analyst found the parameter-free core (TP-exit feasibility: reachable at the tick AND not net-negative at the worst accepted fill) and proved A2 subsumes it 3.8× in production — its value is invariance + backtest coverage. Skeptic **COULD-NOT-BREAK** (370,800-point brute force, 0 violations); F1/F4 amended same session (tp=0 refuse-all; loud roll vetoes). Max-spread clause discharged by A2; surrender curve moved to A20; **A3b** filed (covered-call side, owner decision). Suites 517+170=**687**. |
| 2026-08-01 | **A2 DONE.** Owner rulings: 25 accounts = alternate universes (size judged per account, never aggregated); 3 provisional decisions (covered calls ungated, midpoint denominator @0.10, ON-in-FROZEN idiom) — dialog declined, veto cheap. Analyst spec (veto-not-filter proven by measured delta drift; backtest parquets have NO OI/volume) → predicate + 4 veto sites + observability. Skeptic CORRECT-BUT-INCOMPLETE → both gaps amended (5 body-mutant killers, solo-engine warnings). **Stale-pyc incident** during gate 4 disclosed in the evidence block; process rule adopted. Suites 509+170=**679** on fresh bytecode. |
| 2026-07-31 | **A17 DONE.** Analyst spec (schema map, 9 invariants, migration story) → capacity landed: `filled_contracts` on the seam, shared `close_short_fill`, I3 stale-size re-read, optional `opened_contracts`/`working_order` round-trip — zero migration, fingerprints byte-identical pre/post. Skeptic **COULD-NOT-BREAK**; its two capacity notes (ghost-fill guard, opened_contracts writer) amended + tested same session. Follow-up **A17b** filed. Suites 501+170=**671**. |
| 2026-07-31 | **A19 DONE.** Red test → capture in chain + held-leg rows → skeptic **COULD-NOT-BREAK** (capture-only proven by execution three ways) → F5 amendment (quote-path value assertions + key-rename mutant). Live C2: GDX 109/109 rows populate. **Phase F note added: F4 deploy-timing** — deploy only while paused or a same-day pre-A19 snapshot gaps the day (declared). Suites 495+167=**662**. |
