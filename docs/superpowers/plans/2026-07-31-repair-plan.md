# Repair plan — live paper wheel bot

**Created:** 2026-07-31 · **Status:** NOT STARTED · **Bot state:** PAUSED (timer stopped AND
disabled on the VPS)
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
| A1 | Intraday fill realism — fill at next poll, add slippage | CRIT | TODO | |
| A2 | Liquidity: pull OI/volume/sizes, reject illiquid, cap size | CRIT | TODO | |
| A3 | Minimum credit / maximum spread / unclosable-by-construction guard | HIGH | TODO | |
| A4 | Covered-call window — reach the basis floor | HIGH | TODO | |
| A5 | Same-day re-entry guard must see intraday closes | HIGH | TODO | |
| A6 | Intraday must see a 0.00 bid (align with `rows_from_quotes`) | HIGH | TODO | |
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

---

## Log

| Date | Entry |
|---|---|
| 2026-07-31 | Audit completed (9 domains). Bot paused: timer stopped and **disabled**. 8 fixes shipped as `4cffd72` + `7022ade`. Plan created; no repair work started. |
| 2026-07-31 | Note: today's 9 expiring legs (TMO 512.5P ×9, RIG 4.5P ×8) are **unsettled** because the bot was paused before the EOD run. They settle correctly on resume via the late-expiry path at the expiry day's own close. Not a lost day. |
