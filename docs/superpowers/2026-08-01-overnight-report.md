# Overnight Repair Report — 2026-08-01

Owner-facing, all-ELI10. Full gate-level evidence lives in
`docs/superpowers/plans/2026-07-31-repair-plan.md` (one block per fix).
Written by the overnight autonomous session; every decision made without you is
marked PROVISIONAL and is cheap to veto.

## TL;DR

While you slept: **10 more fixes/batches done, committed, and adversarially
verified** (each with red-test-first, both suites, cache-safe mutation checks,
and a skeptic agent ordered to break it). Plan stands at **22 of 67 rows done**.
Test suites grew from 630 (repair start) to **721, all green**. Bot still
paused, VPS untouched. Two process incidents occurred, both disclosed and
remediated below. Nothing was deployed.

## Scoreboard

| Fix | What | Skeptic verdict | Commit |
|---|---|---|---|
| A3 | Never sell an entry whose winning exit is impossible | COULD-NOT-BREAK (370,800-point brute force) | `65f97de` |
| A4 | Covered call can reach the basis floor again | CORRECT-BUT-INCOMPLETE → 3 amendments | `ee230eb` |
| A5 | 5pm robot can no longer re-sell what the 10am robot closed | COULD-NOT-BREAK | `6993a03` |
| A7 | Holiday ghost-book gate (no more Thanksgiving trades) | CORRECT-BUT-INCOMPLETE → 3 amendments | `aae6307` |
| A11 | No homework before the answers are posted (clock gate) | SURVIVES → boundary tightened | `a040fb1` |
| A14 | Every warning reaches the log; stuck legs email you daily | SURVIVES → 1 amendment | `a49a1e2` |
| A12 | Small accounts stop stranding capital | SURVIVES (5-scenario head-to-head) | `d035bf0` |
| Group B | 9 data-quality rows (B1-B5, B7, B9, B10, A22) | CORRECT-BUT-INCOMPLETE → 3 amendments | `e077463` |

(A2 — the liquidity bouncer — was finished just before you slept, `87687ea`.)

## The fixes, like you're 10

### A3 — the "unclosable promise" guard (`65f97de`)
**Problem:** the bot sold a WBD put for 1¢ whose take-profit exit needed a
buy-back below half a cent — a price that cannot exist (prices move in whole
cents) — and even the cheapest real exit lost money after fees. A guaranteed
loss, sold as income.
**Fix:** before selling, compute (from the config's own arithmetic, no new
knob) the cheapest sale whose winning exit is a real price AND profitable
after both commissions. Refuse anything cheaper, loudly. Rolls into such
contracts refused too.
**Good:** skeptic brute-forced 370,800 cases against the engine's own math:
zero soundness violations. In production A2's bouncer already covers this
3.8×; the guard's value is surviving any future A2 re-tune, and being the ONLY
such gate in backtests.
**Bad/watch:** slightly over-strict at take-profit dials below 43% (unused);
covered calls exempt — that question (A3b) is on your desk and got more urgent
(see A4).

### A4 — the unreachable covered call (`ee230eb`)
**Problem:** after the drop that gets the bot assigned, the "rent" it must
charge sits above the top of its 12-strike price list (reaches only +3.9%
over spot). Result: covered call never written, shares naked, silent for
months — blocked on 31% of real 10%-drawdown days.
**Fix:** for assigned holdings, the snapshot pass now asks Schwab for EVERY
listed above-market call (live-verified: reaches +43% vs +3.9%) and staples
the missing strikes in — additively, with a sanity guard so a lying quote
can't hijack selection. Any day the rent still isn't listed: one email naming
the ticker. That email repeating daily for one ticker = a corporate-action
tripwire for free.
**Good:** live-verified against real GDX; 66 rows through the real filter.
**Bad/watch:** the newly reachable calls are micro-credit (median 4-7¢) and
sell UNGATED — that's the pending **A3b decision, now more frequent. Answer
it soon.**

### A5 — the two robots' shared memory (`6993a03`)
**Problem:** the morning robot buys a position back to lock profit; the 5pm
robot, with no memory of the morning, sells the exact same contract again the
same day — paying the spread twice for nothing.
**Fix:** every close leaves a date-stamped sticky note on the account that
survives disk round-trips; the 5pm robot reads today's notes; yesterday's
notes expire. The skeptic then proved the 5pm robot didn't leave notes for
ITSELF (a crash-retry window could repeat its own trade) — fixed same hour.
**Good:** skeptic tried int-vs-float strikes, weird timestamps, half-closed
positions, midnight retries — held every time.
**Bad/watch:** old sticky notes sit ignored in the file forever (cosmetic).

### A7 — the holiday ghost-book gate (`aae6307`)
**Problem:** on ~9 weekday holidays/yr the market never opens but Schwab
repeats yesterday's prices; the bot once booked a Thanksgiving trade against
Wednesday's book — a trade dated on a day the exchange never opened.
**Fix:** every live quote's own date tag is checked; stamped on a previous
session (or missing/crazy) → thrown out like an absent quote. Parameter-free
— no holiday calendar to rot.
**Good:** date math proven right across DST/UTC-midnight edges; no false
refusal reachable during trading hours.
**Bad/watch:** none live. The skeptic caught my test fixtures accidentally
putting three OLD guard-tests to sleep — proved with a surviving mutant, fixed,
mutant re-killed. Dashboard displays still show holiday echoes (display-only,
filed).

### A11 — no homework before answers are posted (`a040fb1`)
**Problem:** a 04:13 AM catch-up run booked an entire trading day from the
prior day's quotes. Nothing inside the program stops early runs.
**Fix:** the decision run refuses before 17:00 ET (skeptic tightened from my
16:00 — prices aren't *settled* until ~17:00, and a half-baked 16:05 day
would lock itself in). Exit code leaves no done-marker; the normal evening
window retries. `--force`/`--smoke` bypass, and `--force` provably bypasses
ONLY this gate.
**Good:** watched it refuse a real 04:26 run instantly.
**Bad/watch:** practice-mode (`--smoke`) can scribble one note in the real
gaps ledger on a failed pull — pre-existing, filed as A23.

### A14 — no more silent worry-notes (`a49a1e2`)
**Problem:** the engine writes warnings ("this leg is past expiry and I can't
settle it — the closing price never arrived"), and the daily runner threw most
of them away. A leg stuck by a delisting could sit broken forever, silently.
**Fix:** every warning kind reaches the log (generically — future kinds can't
slip into the void), and stuck expiries email you once a day with the leg and
how many days late it is.
**Good:** the real TMO leg (unsettled since the pause, by design) will
demonstrate this on resume day 1.
**Bad/watch:** this fix's skeptic caused Incident 2 (below) — handled.

### A12 — the five piggy banks (`d035bf0`)
**Problem:** $5k split into 5 piggy banks = $1,000 each; no contract that
cheap; bot bought NOTHING while the whole pot could afford one $3k contract.
The $5k accounts ran 18% utilization — your capital×N grid was measuring
affordability, not N.
**Fix:** even split first (byte-identical when anything fits — proven); only
when NOTHING fits does the money pour into fewer banks, down to one.
**Good:** the audit-shaped day goes 0% → 72% deployed; no phantom money
provable in every scenario.
**Bad/watch (your call, one-line change):** when money is tight it buys the
*cheapest fitting* name, not the *richest-premium* one. Spec-conformant;
flag if you want richest-first.

### Group B — the data intake stops believing everything (`e077463`)
**Problem(s):** fake prices ($0/negative/NaN closes) poisoned the trend math;
a blank feed impersonated a holiday (the 2026-07-24 lost-day class); one
scribbled contract or expiry heading threw away a whole ticker; impossible
underlying prices admitted; split-adjusted contracts flowed into ×100
arithmetic; a still-held ticker dropped from the shopping list froze silently
forever (the TMO class); a wholesale held-book quote failure completed the day
with every take-profit suspended; and the chain request dates came from the
UTC box clock — already "tomorrow" from 7pm ET, silently shifting which
expiries the bot shops.
**Fix:** ten red tests, ten tightenings: drop garbage loudly, raise on empty
feeds (so the failure-detector can see them), contain malformation per
line/group, refuse impossible markets, always pull what you hold, and stamp
request dates from Eastern.
**Good:** the group skeptic's three findings all fixed the same session — the
best one: a held book quoted 0×0 (worthless — exactly when the take-profit
matters!) must NOT be mistaken for an outage; before the amendment it would
have rung the failure alarm every 5 minutes all night with zero accounts
trading.
**Bad/watch:** if >50% of tickers' *latest* bar is garbage while history is
fine, the day reads as a quiet holiday (still strictly better than the old
behavior of booking marks at $0.00 — declared, not hidden).

## Incidents (both disclosed in the plan, neither reached the VPS)

1. **Stale-bytecode trap (during A2, pre-sleep, re-verified overnight).** A
   mutation test whose mutant was byte-for-byte the same file size, restored
   in the same second, left Python running the mutant while the file looked
   fixed — one "restored green" was fake. Caught, purged, re-verified, and a
   permanent rule adopted (mutants must change file size + caches purged).
   Saved to memory.
2. **A14 skeptic wrote to the LOCAL frozen archive.** The read-only skeptic
   set the store env var after import (it binds at import time) and its probe
   wrote test states into `data/live/accounts/100k_N1..N5` + created N6. It
   self-reported with recovery commands. Remediated: N6 deleted; N1 restored
   byte-exact from the synced repo; N2–N5 restored to nearest-frozen (one VPS
   intraday session newer than the cutover freeze — display archive only);
   the five polluted snapshot rows stripped. **The canonical VPS/GitHub store
   was never touched**, and Phase F resets all accounts anyway. Rule saved to
   memory so no future agent repeats it.

## Decisions I made for you (all PROVISIONAL, all cheap to flip)

1. A3: guard always-on, no config knob (it's arithmetic, not judgment).
2. A3/A4: covered calls stay exempt from all gates → consolidated into **A3b**.
3. A4: splice filter is strictly-additive-above-the-window (provable
   non-regression) rather than everything-Schwab-lists.
4. A11: clock gate at 17:00 (settled close), not 16:00.
5. A12: fallback prefers least-concentration (cheapest fitting name) over
   richest premium.
6. B4: "wholesale failure" = endpoint answered for NOTHING; an answered 0×0
   book is a real (worthless) market, not an outage.
7. Group A rows A8 (early assignment), A9 (defer covered call), A10
   (corporate actions), A13 (fees — breaks the golden anchors), A15, B6
   (universe re-vet), B8, B11: **deliberately NOT guessed overnight** — each
   needs either your judgment or anchor-breaking you should witness.
8. A20 marked BLOCKED-owner (it IS the deferred "should TP=0.60 exist"
   question).

## Waiting on you (ranked)

1. **A3b — gate covered calls or not?** 22.6% of backtest call entries sell
   at ≤ 2¢; refusing one leaves shares naked; A4 made the question daily.
2. **A2 provisional trio** standing from yesterday (covered-call exemption /
   midpoint@0.10 / ON-in-FROZEN idiom) — veto or bless.
3. **A12 routing preference** at fallback (cheapest-fitting vs richest-premium).
4. **B6** — universe re-vet + `_RETIRED` frozenset (pure judgment call).
5. Group-boundary review of Group A (the plan requires your review at group
   boundaries — most of Group A is now done).

## The board

- Done (22/67): A2-A7, A11, A12, A14, A16-A19, A22, B1-B5, B7, B9, B10.
- Blocked on owner: A20, B6, A3b (+ the provisional vetoes above).
- Remaining plumbing: A8, A9, A10, A13, A15, B8, B11, Group C (15), Group D
  (14), Group E (8), follow-ups (A21, A23, A4b, A17b, A18b, A18c, C16, E8).
- Suites: 630 → **721 green**. Fingerprint harness byte-identical throughout.
- Bot: **paused**, VPS untouched, nothing deployed. Deploy only at Phase F.
