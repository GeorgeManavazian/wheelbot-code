# Wheel defense mechanics — repair pass (design)

- **Date:** 2026-07-13
- **Status:** design — awaiting owner review
- **Part of:** the Wheel options backtester (`src/engine_v2/options/`)
- **Supersedes:** the four defense variants of amendment 2026-07-12b in their current form; un-parks stop-loss/rolling (parked 2026-07-12, decision record in vault: `03 Decisions/2026-07-13 — Un-park wheel defense mechanics (repair pass)`)
- **Motivation:** audit of 2026-07-13 found all four defense variants defective or incoherent. They must work as intended and be measurable before any regime/macro layer is built on top of them. The macro layer (separate, future spec) will *choose between* these mechanics; this pass makes the mechanics worth choosing between.

## Audit findings being fixed (summary)

1. `put_stop_mult` allows same-day re-entry after a stop → the "stop" is a de-facto roll-down that never reduces exposure and pays double friction.
2. `put_stop_mult` has no expiry-day guard → on expiry day it buys back at the ask (intrinsic + spread + commission) when assignment settles at intrinsic. Strictly worse whenever it fires.
3. Stop check silently skips when the day's mark is missing.
4. `roll_puts` fires only at expiry, deep ITM, extrinsic dead → economically dominated by `liquidate_assignment` (same end state, worse price) in every state this engine models. Retired and rebuilt as a mid-life roll.
5. `roll_puts` missing-mark fallback fills at raw intrinsic (better than any real ask) — optimistic fill.
6. `call_min_strike="basis"` anchors on the raw assignment strike, ignoring premium already collected → refuses viable calls; after a large drop it can hold naked shares indefinitely, earning nothing exactly when IV is richest.
7. `select_contract` applies `min_strike` only within the pre-chosen expiry → returns None (sit in cash / no call) even when another in-band expiry has an eligible strike.
8. Trade log has no linkage between a defense action and the position it defends → cannot measure whether any defense helped. Blocks all evaluation.
9. Config allows contradictory combos (`liquidate_assignment` never holds shares; `call_min_strike` governs how shares are held).

## Ground rules

- **Plain-path invariance:** with all defense flags off, engine output (equity series, trade list, final cash/shares) is byte-identical to current `main`. Enforced by a regression test. The pre-registered basket test (both arms: plain + call>=basis) is not touched by this project; the call>=basis arm runs on whatever the frozen spec pinned — see "Interaction with the frozen basket test" below.
- **No new tunables.** One boolean flag replaces another (`roll_puts` → `roll_tested_puts`); every new threshold is either derived from existing config or a hard-coded documented constant. Nothing new appears in the dashboard as a slider.
- **Everything measurable.** Every defense action carries a campaign id; the report can attribute outcomes.

## Config changes

```python
# removed
roll_puts: bool = False
# added
roll_tested_puts: bool = False   # mid-life roll of tested puts; see Roll section
```

Module-level constant (not config): `MAX_ROLLS_PER_CAMPAIGN = 2`.

Validation at `run_wheel` entry:
- `liquidate_assignment and call_min_strike is not None` → `ValueError` (contradictory: one never holds shares, the other governs held shares).
- All other combinations remain legal. `roll_tested_puts` + `liquidate_assignment` is legal (roll mid-life; if the cap is hit and assignment happens anyway, liquidation applies).

## Mechanic 1 — stop (`put_stop_mult`), fixed

Trigger unchanged: EOD `ask >= put_stop_mult × credit`, puts only, EOD marks only.

Changes:
1. **Expiry-day guard:** stop check requires `d < c.expiry`. Expiry day is resolved exclusively by the expiry logic (assignment / expire OTM).
2. **Stop means flat:** after a `STOP_CLOSE`, no new position may be opened the same day (any contract, either right). Implemented as a `no_entry_today` flag consumed by the entry step. Re-entry is eligible from the next trading day via the normal entry logic.
3. **No silent skips:** if the day's mark is missing, append `(date, "stop_check_no_mark", contract)` to `WheelResult.warnings` (new field, `list`, default empty). Report surfaces the count.

Check order within a day (see "Daily decision order" below): TP → roll → stop → expiry.

## Mechanic 2 — roll, rebuilt (`roll_tested_puts`)

The at-expiry buyback is removed. A tested put is now managed mid-life, while extrinsic is alive.

**Trigger** (all must hold, checked daily after the TP check):
- flag on, current short is a put, `d < expiry`
- put is tested: EOD `spot <= strike`
- campaign roll count `< MAX_ROLLS_PER_CAMPAIGN`
- current mark exists (no mark → no roll today; append `(date, "roll_check_no_mark", contract)` to warnings)

**Action** (single decision, two fills, same day):
- Buy-to-close current put at the **ask** + commission.
- Sell-to-open new put at the *config* delta, same contract count `n`. Must be a different contract than the one just closed; selection returning None or the identical contract → no roll today.
- **Destination (amended 2026-07-13b, twice-falsified by stress data):** the original draft re-used `select_contract` at the config target DTE. SPY 2017–2026 stress: all 143 tested-day opportunities failed the credit-only check (median deficit $2.69/share). First amendment extended time but re-picked the strike at the config delta (roll down-and-out) — still 143/143 debit (median $2.51): a ~50Δ buyback can never be funded by a 20Δ sale at these tenors. The canonical credit roll is **same strike, out in time**: destination = **the held strike**, in the expiry strictly beyond the held leg nearest to `held_expiry + target_dte` (tie → longer-dated); expiries without that exact strike are skipped in favor of the next-nearest that has it (`select_roll_contract` in `select.py`). Same strike keeps the assignment risk — the credit-only rule and the cap are the bounds on that. Fully derived; zero new knobs.
- **Credit-only rule:** the roll executes only if `sell_proceeds(new) >= buy_cost(current)` (both commission-inclusive). Net debit rolls never execute. If the rule fails, nothing happens today; the trigger is re-evaluated tomorrow.
- Trade log: `ROLL_CLOSE` (old contract) + `ROLL_OPEN` (new contract), same campaign id, roll counter incremented. (`ROLL_OPEN` is a new action name so rolls are distinguishable from fresh `SELL_PUT` entries.)

**Cap:** after `MAX_ROLLS_PER_CAMPAIGN` rolls, the position runs to the normal expiry path — assignment if ITM (then `liquidate_assignment` or the call phase per config).

**No new knobs:** trigger is derived from the position itself (strike touch), destination from existing config (delta, target DTE), cap is a documented constant.

**Priority note:** the roll check runs *before* the stop check. Rationale: a credit-positive repair is attempted before surrendering; the stop remains the backstop for the case where no acceptable roll exists and the loss keeps growing. With both flags on, a day where both would trigger executes the roll only (position changed; stop re-evaluates against the *new* leg's credit from the next day).

**Credit tracking across rolls:** each leg's stop threshold uses that leg's own credit (`mark.bid` at its open), not the campaign total — the stop stays a per-leg reflex; campaign economics live in the report.

## Mechanic 3 — covered-call floor (`call_min_strike="basis"`), fixed

1. **Net basis, not raw strike:** the floor is `assignment_strike − campaign_premium_per_share`, where `campaign_premium_per_share` = (sum of all net option premium collected in this campaign so far, in dollars) / (shares held). Updated as call premium accrues, so the floor ratchets *down* as rent comes in and eligible strikes unlock sooner.
2. **Cross-expiry fallback in `select_contract`:** when `min_strike` filtering empties the chosen expiry, try the remaining in-band expiries in ascending DTE-error order (tie → longer-dated, matching the existing rule) and take the first expiry containing a strike `>= min_strike`. Only when *no* in-band expiry qualifies does selection return None.

Naked-shares days (shares held, no call short) are already counted as exposed, not flat; the report additionally surfaces `days_shares_uncovered` so the cost of the floor is visible.

## Mechanic 4 — `liquidate_assignment`, honesty pass

Mechanics unchanged. Two additions:
1. Report labels the stock fill assumption explicitly: fills at EOD spot, no spread/slippage modeled on the stock leg (options pay full spread; the asymmetry is stated, not hidden).
2. Config validation per above.

## Campaign accounting (the measurement layer)

- `Trade` gains `campaign_id: int`. A campaign opens with a `SELL_PUT` from flat (id = monotonically increasing int) and every subsequent trade — TP close, stop, rolls, assignment, liquidation, calls, called-away — carries the same id until the position returns to flat cash. Assignment and the entire call phase belong to the put's campaign (one wheel saga = one campaign).
- `WheelResult` gains `warnings: list` (see above).
- Report additions (`report.py`):
  - campaigns: count, P&L per campaign (net cash delta over the campaign), win rate
  - rolls: campaigns with 0/1/2 rolls; per-roll net credit captured
  - **short-leg counterfactual per roll:** for each closed-by-roll leg, what that leg would have settled at had it been held to expiry (credit − intrinsic at expiry, from chain data) vs. what closing actually cost. Labeled explicitly as *short-leg-only* — it does not model the divergent post-assignment path — and reported as a distribution, not a verdict.
  - stops: count, loss realized per stop, `stop_check_no_mark` count
  - `days_shares_uncovered` (floor cost visibility)
- Causal comparisons between variants remain what they always were: same-data A/B runs (plain vs. one flag on), reported raw. The campaign table makes those runs interpretable; it does not replace them.

## Daily decision order (consolidated)

For a held short, in order: **TP check** (intraday then EOD, unchanged) → **roll check** (mid-life, puts) → **stop check** (mid-life, puts) → **expiry resolution** (assignment / expire / called away). Then the entry step, which is blocked for the day by `no_entry_today` (set only by `STOP_CLOSE`). Roll's `ROLL_OPEN` happens inside the roll action, not the entry step.

## Interaction with the frozen basket test

The basket spec (2026-07-12, two pre-registered arms) is binding and untouched. Arm 1 (plain) is protected by the plain-path invariance test. Arm 2 (call>=basis) predates this repair: it must run on the engine commit its spec pinned, or — if the owner prefers the repaired floor — that is an **owner decision to amend the pre-registration before any basket data is seen**, recorded in the vault. This spec takes no position; it only flags the fork.

## Error handling

| Situation | Behavior |
|---|---|
| Missing mark on stop/roll check day | Skip check, append to `warnings`, continue |
| `select_contract` returns None during roll | No roll today, re-evaluate tomorrow |
| Roll candidate identical to closed contract | No roll today |
| Credit-only rule fails | No roll today, re-evaluate tomorrow |
| Contradictory config | `ValueError` at `run_wheel` entry, before any iteration |

## Testing

**Regression (the contract):**
- Plain config (all defense off) on the SPY fixture → equity series, trade list, final cash byte-identical to current `main` output (golden file).

**Unit (synthetic chains, deterministic):**
- Stop: cannot fire on expiry day; firing blocks all same-day entries; missing mark → warning entry, no crash; threshold arithmetic exact.
- Roll: fires only when tested and `d < expiry`; credit-only blocks net-debit rolls; cap enforced at exactly 2; `ROLL_CLOSE`/`ROLL_OPEN` share campaign id and increment the counter; None/identical selection → no action; missing mark → warning.
- Roll-vs-stop priority: a day where both trigger executes the roll only.
- Basis: floor equals strike − premium/share and ratchets down as call premium accrues; cross-expiry fallback finds an eligible strike in a farther in-band expiry; no in-band strike ≥ floor → None.
- Campaign ids: full saga (put → roll → assignment → calls → called away) carries one id; next entry gets a new id.
- Config validation: contradictory combo raises before iteration.
- Counterfactual: closed-leg settle value computed correctly from chain fixture.

**Report:** campaign table, roll histogram, stop stats, `days_shares_uncovered`, warning counts render and sum correctly on a fixture run.

All existing tests stay green except those that referenced `roll_puts` semantics, which are updated deliberately and listed in the PR description.

## Out of scope

- Regime/technical state module (200d/50d MA, S/R zones, vol state) — next project.
- Any policy that chooses between mechanics based on market state — next project.
- Call-side rolling, stock-side stops, IV filters — not requested, not built.
- Intraday stop/roll timing — EOD decisions only, matching the engine's honesty model.

## References

- Audit: conversation 2026-07-13 (findings enumerated above)
- `src/engine_v2/options/wheel.py`, `select.py`, `report.py` @ f29aa36
- Basket pre-registration: `2026-07-12-wheel-multi-ticker-design.md` (+ two-arm amendment 2ba683c)
- Chan stop warning: *Machine Trading* Ch.5–6 (vault: 05 Library)
- Parked-decision being superseded: vault `Logs/2026-07-12` 17:15 entry
