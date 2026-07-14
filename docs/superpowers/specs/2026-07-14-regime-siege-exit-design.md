# Regime siege exit — design

- **Date:** 2026-07-14
- **Status:** approved direction (owner: "work on 3 right now", idle-capital brainstorm) — this document freezes the rule before any test runs
- **Part of:** the macro layer. Companions: `2026-07-14-regime-gates-design.md` (phase 2), defense repair `2026-07-13-...`. Deferred siblings bookmarked in vault `08 Wheel Bot/Macro roadmap — rotation and hybrid (BOOKMARKED 2026-07-14).md`.

## Problem (from the 2026-07-14 trade dissection)

The basis floor (`call_min_strike="basis"`) fixed the wheel's worst wound (selling the recovery below cost: GDX shares leg −$159k → +$14k) but created **sieges**: shares held uncovered below basis waiting for recovery — GDX 594 days, SLV 375, XOP 336; single sieges up to 1,020 days. Hostage capital with full downside and no income.

Blanket time-stops sell bottoms (dissection: that is the killer move). The regime state can do better: the campaign autopsy showed **panic recovers, complacent decline bleeds** — the same evidence base as the phase-2 gates.

## The rule (fixed, zero knobs)

During a siege, if the ticker's regime state is **unpaid decline** (`downtrend` + vol in {calm, normal} — the phase-2 constant, unchanged), sell all shares that day and return to cash. In any other state — including panic (stressed) — hold.

- **Siege day** = shares held (≥ 1 contract lot), no short open, after the day's entry step failed to write a call (the same day the existing `days_shares_uncovered` counter ticks). A day with a call successfully written above basis is not a siege day.
- **State** = strictly-prior-day, 14-day staleness bound, identical lookup to the phase-2 gates. `unknown` state → **hold** (never act on missing information), warning `(date, "gate_state_unknown", "siege")`.
- **Action:** sell all shares at that day's EOD spot. Trade action **`SIEGE_EXIT`** (own name — must never be conflated with `LIQUIDATE`), same campaign id; campaign then ends at flat. `basis` cleared, phase → PUT. Re-entry is the normal entry step from the **next** day (with the entry gate on, unpaid decline blocks it — the coherent combo).
- **Fill honesty:** stock fills at EOD spot, no stock-leg spread/slippage modeled — same explicit label as `liquidate_assignment`.
- Event log: `(date, "siege_exit", None)` appended to `gate_events`.

**Stated tension, on purpose:** the basis floor exists to avoid *passively* locking losses via cheap calls; the siege exit *actively* realizes a loss below basis when the regime says the decline is uncompensated. The floor blocks the reflex; the exit is a deliberate, evidence-motivated, pre-registered decision. The A/B measures whether the evidence holds.

## Config

```python
regime_siege_exit: bool = False   # exit uncovered shares in unpaid decline; hold through panic
```

Validation at `run_wheel` entry (extends the existing gate validation):
- `regime_siege_exit` and `regime_states is None` → `ValueError`. Implemented by extending `WheelConfig.any_regime_gate` to include the new flag (its meaning is "any regime-consuming mechanic armed"); the gates report block consequently appears whenever the siege flag is on, which is the intended visibility.
- `regime_siege_exit` and `call_min_strike is None` → `ValueError` (sieges are floor-created; without the floor the flag would be a near-no-op arm).
- `regime_siege_exit` and `liquidate_assignment` → `ValueError` (liquidation never holds shares; there is nothing to exit).

## Engine placement

In the daily loop, after the entry step and exactly where the uncovered-day accounting already happens (`short is None and phase == "CALL" and shares >= mult`): if flag on and state is unpaid decline → execute the exit. One check, no reordering of TP/roll/stop/expiry/entry.

`WheelResult`: no new fields (`gate_events` carries the events; the trade ledger carries `SIEGE_EXIT`).

## Reporting

- `wheel_stats`: `n_siege_exits` (count of `SIEGE_EXIT` trades).
- Gates/defense report block: siege line shown whenever the flag is on, even at zero fires (visibility precedent).
- `position_log`: `SIEGE_EXIT` closes the shares row with outcome `"Siege exit"` (mirrors `LIQUIDATE` handling).
- `_trade_cash_flow`: `SIEGE_EXIT` → `+ px * mult * n` (stock sale).

## Execution audit (referee)

`audit_defense_execution.py` gains siege re-derivation for flagged variants:
- Reconstruct share-holding and call-coverage periods from the ledger (ASSIGNED → shares; SELL_CALL/expiry events → covered/uncovered days).
- For every reconstructed uncovered day with locally-re-derived state = unpaid decline (`_unpaid`/`_asof`, the audit's own copies): expect a `SIEGE_EXIT` that day (double-entry, both directions — an exit without a derived trigger is a mismatch, a derived trigger without an exit is a mismatch).
- New audited variants: `basis+siege` and `basis+siege+entry-gate`.

## A/B protocol (pre-registered, seen tickers only)

Frozen basket config, EOD fills (comparable to prior matrices), full available history per ticker with the **XOP arm starting 2020-07-01** (split-broken chain before that — STATUS item; the same start applies to BOTH arms so the pair stays honest):

| Arm | Baseline | Treatment |
|---|---|---|
| SE | basis floor | basis + siege exit |
| SE+E | basis + entry gate | basis + entry gate + siege exit |

All four seen tickers, raw, per-ticker, no aggregation, owner judges (no kill rule — standing choice). Runner: `scripts/run_siege_exit.py`, output `data/options/reports/siege_exit.txt`, unseen tickers refused without `--after-basket-run`. Report must show: P&L/Sharpe/maxDD, `n_siege_exits`, `days_shares_uncovered` (the metric this mechanic exists to cut), and realized shares-leg P&L.

## Testing

- Plain-path invariance: flag off → byte-identical (existing digest regression).
- Unit (synthetic chains + states): exit fires on uncovered+unpaid day; holds in stressed/uptrend/chop; holds on unknown + warns; covered day (call written) never exits; validation errors (no states / no floor / liquidate combo); campaign id continuity + `position_log` row; re-entry not same day.
- No-look-ahead: state flip on the exit day itself must not trigger (strictly-prior rule) — same property shape as the gates.
- Coverage: engine_v2 gate ≥95%.

## Out of scope

- Rotation of freed cash to other tickers (bookmarked idea 1), strategy switching (bookmarked idea 5), any per-regime parameter, time-based siege stops, renting below basis in panic (rejected in brainstorm — re-opens the closed wound).
