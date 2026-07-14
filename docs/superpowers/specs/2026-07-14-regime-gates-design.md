# Regime gates (macro phase 2) — design

- **Date:** 2026-07-14
- **Status:** approved by owner (design dialogue 2026-07-14) — **this document is the pre-registration; it freezes the rules BEFORE any unseen-ticker data is opened**
- **Part of:** the macro layer for the wheel bot. Phase 1 (advisor: state, base rates, autopsy) shipped 2026-07-13 (`2026-07-13-regime-advisor-design.md`). This spec wires regime state into three wheel decisions as fixed rules.
- **Owner decisions locked (2026-07-14):**
  1. Scope: entry gate + roll gate + stop gate. Assignment policy explicitly excluded.
  2. State input: the **traded ticker's** regime state (not SPY's).
  3. Pre-registration: this spec is frozen and committed **before** the pre-registered basket run executes and before any XBI/EEM/EWZ/TLT/ARKK data is opened. The unseen tickers are therefore genuine out-of-sample for these gates.
  4. Keep/kill: owner judges from raw A/B reports, seen vs unseen separated. No pre-committed kill rule.
  5. Packaging: three independent boolean flags (approach A), each A/B-able alone.

## Evidence (motivating, not proving)

Campaign autopsy (phase 1 tooling) on the four burned tickers (SPY GDX SLV XOP), repaired engine, basket config, full history, campaigns grouped by ticker state at open (strictly-prior-day):

- **Panic pays.** Downtrend + stressed vol was profitable on all four tickers (SPY +12.9k, GDX +27.5k, SLV +8.3k, XOP +3.2k by market state; same sign by ticker state). Post-crash IV overprices realized movement — the vol risk premium is fattest in panic.
- **Complacent decline bleeds.** Downtrend with calm/normal vol lost money on three of four (SPY −4.2k n=11, GDX −15.6k n=13, SLV −12.7k n=13; XOP +6.5k n=10). Falling prices without panic premium = uncompensated risk.
- **Ticker state catches what market state misses.** XOP's worst bleed (−55.0k) sat in "SPY uptrend + calm"; by ticker state it is XOP downtrend (−30.7k in downtrend+calm). GDX and SLV show the same divergence.
- **The stop is anti-regime.** `put_stop_mult=3` flipped SPY downtrend+stressed from +12.9k to −23.3k and damaged the stressed cells on every ticker — it sells panic bottoms, exactly where the plain wheel earns.
- Rolls rescued no bleed cell on any ticker.

**Honesty caveat, stated in full:** these are full-history, in-sample tables on data that has been examined repeatedly; the decisive cells hold 10–13 campaigns; overlapping campaigns are not independent samples. This evidence motivates the rules; it cannot validate them. Validation is the out-of-sample A/B below.

## The economic principle (one sentence)

**A falling underlying is only acceptable wheel inventory while the market is paying panic premium for it.** All three gates are corollaries. No per-cell table exists or may be added — a 9-cell action table is in-sample mining and is out of scope permanently.

## Named regime

Module-level constants (documented, never config, never swept):

```python
# unpaid decline: falling without panic premium
def is_unpaid_decline(trend: str, vol: str) -> bool:
    return trend == "downtrend" and vol in ("calm", "normal")

def is_stressed(vol: str) -> bool:
    return vol == "stressed"
```

`trend`/`vol` come from phase 1 `regime_series` unchanged (fixed ex-ante thresholds, trailing-only, property-tested for no look-ahead). Phase 2 adds **no new state definitions and no new thresholds.**

## Config changes

```python
# added to WheelConfig, all default False
regime_entry_gate: bool = False   # no new campaign opens in unpaid decline
regime_roll_gate: bool = False    # mid-life roll denied in unpaid decline
regime_stop_gate: bool = False    # put stop suppressed while ticker vol == "stressed"
```

`run_wheel` gains an optional parameter `regime_states: pd.DataFrame | None = None` — the output of `regime_series(ticker_closes)`, computed by the caller. The engine does **not** import `regime/`; it consumes a plain DataFrame (dependency direction preserved: `regime/` stays a pure consumer of `options/`).

Validation at `run_wheel` entry:
- any gate flag True and `regime_states is None` → `ValueError` (a gate with no state is not a run, it is a bug).
- `regime_stop_gate=True` and `put_stop_mult is None` → `ValueError` (a gate on a mechanic that is off would be a silent no-op A/B arm).
- All other combinations legal.

**No new tunables.** Three booleans; every threshold already existed in phase 1.

## State lookup rule (information discipline)

For a decision on day *d*, the engine uses the state row with the greatest date **strictly before** *d* (a trade on day *d* cannot know day *d*'s close — same rule as the engine's fills and the autopsy tagging), subject to a staleness bound of **14 calendar days** (constant, mirrors `autopsy.MAX_STALENESS_DAYS`). Implemented as a small searchsorted as-of helper inside the engine (≤10 lines), unit-tested; the duplication with `autopsy._tag_rows` is deliberate and cross-referenced in both docstrings (no shared module to avoid the import inversion).

Warmup rows, missing dates, and stale states resolve to `"unknown"` → **every gate default-allows** (never gate on missing information). Each unknown-on-a-gate-day occurrence appends `(date, "gate_state_unknown", gate_name)` to `WheelResult.warnings`; the report surfaces the count.

Ticker closes for `regime_series` come from the longest clean series on disk (`regime.data.closes_for`: 2010+ bars fixture first, chain `underlying` fallback), so the ~273-day warmup burns off before chains start in 2017 for fixture-covered tickers.

## Gate mechanics

Daily decision order is **unchanged** (TP → roll → stop → expiry → entry, per the defense-repair spec). Gates are additional conditions inside existing checks; they never reorder, never add fills, never touch sizing.

1. **Entry gate** (`regime_entry_gate`): in the entry step, if the day's state is unpaid decline, no new campaign opens. `days_entry_gated` counts only days where the gate was the proximate blocker — the entry step was reached, an entry would otherwise have been attempted, and the gate stopped it (not every unpaid-decline calendar day). The day still counts as flat in the existing flat-day accounting. Re-evaluated daily — first non-gated day is a normal entry.
2. **Roll gate** (`regime_roll_gate`): in the roll check (`roll_tested_puts` path), if the day's state is unpaid decline, no roll today; trigger re-evaluated tomorrow (same semantics as a failed credit check). Logged: `(date, "roll_denied_by_gate", contract)` appended to a new `WheelResult.gate_events` list. Panic-state rolls stay allowed.
3. **Stop gate** (`regime_stop_gate`): in the stop check, if the day's ticker vol state is `"stressed"`, the stop does not fire that day. Logged: `(date, "stop_suppressed_by_gate", contract)` in `gate_events`. The stop re-arms automatically on the first non-stressed day (per-leg credit threshold unchanged).

Every suppression/skip is an explicit logged event — nothing is silent.

`WheelResult` additions: `gate_events: list` (default empty), `days_entry_gated: int` (default 0).

## Reporting

`report.py` additions, shown whenever any gate flag is on **even if the gate never fired** (precedent: defense block visibility fix, amendment 2026-07-13c):

- per-gate: fired/suppressed counts (`days_entry_gated`, rolls denied, stops suppressed), `gate_state_unknown` count
- campaigns-not-opened is not directly observable; the A/B pair (below) is the measurement. The report says so rather than inventing a counterfactual.
- existing campaign table + autopsy remain the attribution layer.

## Execution audit

`scripts/audit_defense_execution.py` extended: for every day of a gated run, re-derive the state (strictly-prior, staleness-bounded) from the same closes series and assert (a) no entry on unpaid-decline days when entry gate on; (b) no `ROLL_CLOSE` on unpaid-decline days when roll gate on; (c) no `STOP_CLOSE` on stressed days when stop gate on; (d) every `gate_events` entry matches a re-derived gate day. Exit 0 = correct execution. Same contract as the defense audit.

## Testing

- **Plain-path invariance:** all gates off → byte-identical output (existing digest-pinned regression must stay green; add one explicit case: `regime_states` passed but all flags False → still byte-identical).
- **No look-ahead property:** for random truncation points k, gate decisions over days ≤ k are identical whether `regime_states` was computed from full closes or closes[:k]. (Inherits phase 1's property; asserted at the engine boundary.)
- **Unit, synthetic states:** each cell × each gate → correct allow/deny; unknown/stale/warmup → allow + warning; staleness boundary (14 days exactly vs 15).
- **Validation:** both `ValueError` combinations.
- **Integration, real data:** SPY gated run completes; audit script exit 0; gate_events non-empty for entry gate (SPY has unpaid-decline days in 2018/2022).
- Coverage folded into the engine_v2 gate (≥85%; current 95.5% must not regress below 95%).

## A/B protocol (pre-registered)

All at the frozen basket config (20Δ both legs, target 7 DTE, 50% TP, $100k, cross-spread + $0.65/ct, cash yield 0). Three paired arms, each gate measured alone against its own baseline:

| Arm | Baseline | Treatment |
|---|---|---|
| E | plain | plain + entry gate |
| R | roll-tested | roll-tested + roll gate |
| S | put-stop-3x (`put_stop_mult=3.0`) | put-stop-3x + stop gate |

- Runs on **all 10 tickers** (SPY GDX SLV XOP QQQ + XBI EEM EWZ TLT ARKK), **only after** the pre-registered basket run (amendment 2026-07-13d) has executed — the basket run is untouched by this spec and runs first.
- Reported raw, every arm, every ticker, **seen (SPY GDX SLV XOP) and unseen (XBI EEM EWZ TLT ARKK) in separate tables**, QQQ labeled as the pre-registered control. No aggregation that mixes the groups, no best-cell selection, intraday-fill optimism caveat printed as always.
- Runner script `scripts/run_regime_gates.py`, output `data/options/reports/regime_gates.txt`.
- **Owner judges.** No pre-committed kill rule (owner's explicit choice, mirrors basket precedent).

## Sequencing (binding)

1. This spec committed (the pre-registration timestamp is the git commit).
2. Implementation + tests + audit on seen tickers only.
3. Basket run executes per amendment 2026-07-13d (owner-triggered, frozen config, both arms — this spec changes nothing about it).
4. Regime-gates A/B runs on all 10.
5. Owner review, raw tables.

Unseen-ticker data stays unopened until step 3. If any unseen chain is inspected before step 3 for any reason, that fact must be recorded in this spec as an amendment (contamination statement discipline, per amendment 2026-07-13d).

## Amendment 2026-07-14a — contamination statement (binding disclosure)

During the post-implementation stress round, `scripts/run_defense_matrix.py` was launched with no ticker arguments; its default was `available_tickers()` — every chain on disk — and the basket data pull had completed earlier the same day, so the defense matrix **executed on the unseen tickers** (XBI EEM EWZ TLT ARKK QQQ) and wrote their results to `data/options/reports/defense_matrix.txt`. Facts, exactly:

- The regime-gates rules were **already frozen and committed** (b00205f) before this run — no rule was or can be influenced by it.
- The agent's context was exposed to **one** unseen-ticker number: buy-hold XBI total (+166,874) — price appreciation only, no wheel/defense result for any unseen ticker was seen.
- The contaminated file was **deleted unread**; no human saw any of it. It was never committed.
- `run_defense_matrix.py` now defaults to the seen set and refuses unseen tickers without `--after-basket-run` (same guard as `run_regime_gates.py`).

Owner call whether the unseen set's status is impaired; this note exists so that call is made with the facts.

## Out of scope

- Assignment/liquidation policy by regime (owner-excluded 2026-07-14)
- Per-cell action tables, per-regime parameter variation, any new threshold or knob
- Market-state (SPY) gating — ticker state only in phase 2
- VIX or any external data series
- Position sizing by regime

## References

- Phase 1: `2026-07-13-regime-advisor-design.md` · Defense repair: `2026-07-13-wheel-defense-repair-design.md`
- Basket pre-registration: `2026-07-12-wheel-multi-ticker-design.md` + amendment 2026-07-13d
- Evidence tables: generated 2026-07-14 from phase-1 autopsy on seen tickers (scratch run; regenerable via `campaign_regimes`/`autopsy_table` at basket config)
