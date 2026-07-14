# Regime Gates (Macro Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire ticker regime state into three wheel decisions (entry, roll, stop) as fixed pre-registered rules with zero new knobs, per `docs/superpowers/specs/2026-07-14-regime-gates-design.md`.

**Architecture:** Three boolean flags on `WheelConfig`; `run_wheel` gains an optional `regime_states` DataFrame (phase-1 `regime_series` output) and a tiny strictly-prior/staleness-bounded as-of helper. Gates are extra conditions inside the existing TP → roll → stop → expiry → entry order — never reorder, never add fills. Every suppression is a logged event. Engine does NOT import `regime/`.

**Tech Stack:** Python, pandas, pytest. Repo `~/Documents/Trading/code/etf-bot`, run tests with `PYTHONPATH=. .venv/bin/python -m pytest`.

## Global Constraints (from spec, binding)

- Plain path (all gates off) byte-identical — `tests/engine_v2/options/test_plain_regression.py` must stay green.
- Zero new tunables: three booleans only; `GATE_STALENESS_DAYS = 14` and `is_unpaid_decline` are documented constants.
- State lookup strictly-prior-day, 14-calendar-day staleness bound; unknown/stale/warmup → default-allow + logged.
- Unpaid decline = `trend == "downtrend" and vol in ("calm", "normal")`; stop suppression = `vol == "stressed"`. Ticker state only.
- Unseen tickers (XBI EEM EWZ TLT ARKK) must not be touched by any run in this plan. Seen = SPY GDX SLV XOP.
- Validation errors: any gate on + `regime_states is None`; `regime_stop_gate` + `put_stop_mult is None`.
- Coverage: engine_v2 gate ≥85%, don't regress below 95%.

---

### Task 1: Gate constants, config flags, validation, state-lookup helper

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (config, constants, helper, validation)
- Test: `tests/engine_v2/options/test_regime_gates.py` (new)

**Interfaces:**
- Produces: `is_unpaid_decline(trend, vol) -> bool`, `_state_before(states, d) -> tuple[str, str]`, `GATE_STALENESS_DAYS = 14`, `WheelConfig.regime_entry_gate/regime_roll_gate/regime_stop_gate: bool = False`, `run_wheel(chain, cfg, intraday=None, regime_states=None)`.

- [ ] **Step 1: Write failing tests** — new file `tests/engine_v2/options/test_regime_gates.py`:

```python
"""Regime gates (macro phase 2, spec 2026-07-14): entry/roll denied in unpaid
decline, stop suppressed in stressed vol. Ticker state, strictly-prior-day,
zero new knobs. All default-off — plain behavior byte-identical."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import (WheelConfig, run_wheel,
                                         is_unpaid_decline, _state_before,
                                         GATE_STALENESS_DAYS)

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def _cfg(**kw):
    base = dict(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                take_profit_pct=None, commission_per_contract=0.0)
    base.update(kw); return WheelConfig(**base)

def _states(rows):
    """rows: list of (date, trend, vol)."""
    df = pd.DataFrame(rows, columns=["date","trend","vol"]).set_index("date")
    df.index = pd.to_datetime(df.index)
    return df

PUT_DAY = [["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0]]

def test_is_unpaid_decline_cells():
    assert is_unpaid_decline("downtrend", "calm")
    assert is_unpaid_decline("downtrend", "normal")
    assert not is_unpaid_decline("downtrend", "stressed")
    assert not is_unpaid_decline("uptrend", "calm")
    assert not is_unpaid_decline("chop", "normal")
    assert not is_unpaid_decline("unknown", "unknown")

def test_state_before_is_strictly_prior():
    st = _states([("2024-01-01","uptrend","calm"), ("2024-01-02","downtrend","calm")])
    assert _state_before(st, pd.Timestamp("2024-01-02")) == ("uptrend", "calm")
    assert _state_before(st, pd.Timestamp("2024-01-03")) == ("downtrend", "calm")

def test_state_before_staleness_bound():
    st = _states([("2024-01-01","downtrend","calm")])
    ok = pd.Timestamp("2024-01-01") + pd.Timedelta(days=GATE_STALENESS_DAYS)
    assert _state_before(st, ok) == ("downtrend", "calm")
    assert _state_before(st, ok + pd.Timedelta(days=1)) == ("unknown", "unknown")

def test_state_before_empty_or_future_only():
    st = _states([("2024-06-01","downtrend","calm")])
    assert _state_before(st, pd.Timestamp("2024-01-02")) == ("unknown", "unknown")

def test_gate_flag_without_states_raises():
    for flag in ("regime_entry_gate", "regime_roll_gate"):
        with pytest.raises(ValueError):
            run_wheel(_chain(PUT_DAY), _cfg(**{flag: True}))

def test_stop_gate_without_stop_raises():
    st = _states([("2024-01-01","uptrend","calm")])
    with pytest.raises(ValueError):
        run_wheel(_chain(PUT_DAY), _cfg(regime_stop_gate=True), regime_states=st)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_regime_gates.py -x -q`
Expected: ImportError (`is_unpaid_decline` not defined).

- [ ] **Step 3: Implement** in `src/engine_v2/options/wheel.py`:

Below `MAX_ROLLS_PER_CAMPAIGN`:

```python
GATE_STALENESS_DAYS = 14   # mirrors regime.autopsy.MAX_STALENESS_DAYS (kept in
                           # sync by hand: no shared module, to preserve the
                           # options/ -> regime/ import direction)

def is_unpaid_decline(trend: str, vol: str) -> bool:
    """Falling without panic premium — the one cell the gates act on
    (pre-registered, spec 2026-07-14). Never extended to a per-cell table."""
    return trend == "downtrend" and vol in ("calm", "normal")

def _state_before(states: pd.DataFrame, d: pd.Timestamp) -> tuple:
    """(trend, vol) from the last state row STRICTLY before d (a decision on
    day d cannot know day d's close — same rule as fills and autopsy tagging),
    bounded by GATE_STALENESS_DAYS. Missing/stale -> ("unknown","unknown"):
    gates never act on missing information."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return "unknown", "unknown"
    row = states.iloc[pos]
    return row["trend"], row["vol"]
```

`WheelConfig` additions (after `put_stop_mult`):

```python
    # regime gates (macro phase 2, spec 2026-07-14) — all default-off so the
    # plain path is byte-identical. Ticker state, strictly-prior-day.
    regime_entry_gate: bool = False   # no new campaign opens in unpaid decline
    regime_roll_gate: bool = False    # mid-life roll denied in unpaid decline
    regime_stop_gate: bool = False    # put stop suppressed while vol == "stressed"
```

`run_wheel` signature + validation (after the existing liquidate/call_min_strike check):

```python
def run_wheel(chain: pd.DataFrame, cfg: WheelConfig, intraday=None,
              regime_states: pd.DataFrame | None = None) -> WheelResult:
    ...
    any_gate = cfg.regime_entry_gate or cfg.regime_roll_gate or cfg.regime_stop_gate
    if any_gate and regime_states is None:
        raise ValueError("a regime gate is on but no regime_states was passed — "
                         "a gate with no state is a bug, not a run")
    if cfg.regime_stop_gate and cfg.put_stop_mult is None:
        raise ValueError("regime_stop_gate gates the put stop; put_stop_mult is "
                         "None so the arm would be a silent no-op — refuse it")
```

- [ ] **Step 4: Run tests** — same command, expected PASS. Full suite: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2 -q` green.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: regime gate constants, config flags, validation, as-of state helper"`

### Task 2: Entry gate

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (entry step, `WheelResult`)
- Test: `tests/engine_v2/options/test_regime_gates.py`

**Interfaces:**
- Consumes: Task 1 names.
- Produces: `WheelResult.gate_events: list` (default None→[]), `WheelResult.days_entry_gated: int = 0`; gate_events entries `(date, "entry_gated", None)`, warnings entries `(date, "gate_state_unknown", "entry")`.

- [ ] **Step 1: Failing tests**

```python
def test_entry_gate_blocks_new_campaign_in_unpaid_decline():
    st = _states([("2024-01-01","downtrend","calm")])
    res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
    assert not [t for t in res.trades if t.action == "SELL_PUT"]
    assert res.days_entry_gated == 1
    assert (pd.Timestamp("2024-01-02"), "entry_gated", None) in res.gate_events

def test_entry_gate_allows_entry_outside_unpaid_decline():
    for trend, vol in [("uptrend","calm"), ("downtrend","stressed"), ("chop","normal")]:
        st = _states([("2024-01-01",trend,vol)])
        res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
        assert [t for t in res.trades if t.action == "SELL_PUT"], (trend, vol)
        assert res.days_entry_gated == 0

def test_entry_gate_unknown_state_allows_and_warns():
    st = _states([("2023-06-01","downtrend","calm")])   # stale > 14d
    res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
    assert (pd.Timestamp("2024-01-02"), "gate_state_unknown", "entry") in res.warnings

def test_entry_gate_does_not_gate_call_phase():
    # assigned shares; call written next day even though state is unpaid decline
    # (gate blocks NEW campaigns only — the call phase continues an old one).
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-09","2024-01-16",7,475,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
    ]
    st = _states([("2024-01-01","downtrend","calm")])
    res = run_wheel(_chain(rows), _cfg(regime_entry_gate=True), regime_states=st)
    assert [t for t in res.trades if t.action == "SELL_PUT"] == []  # entry gated
    # force the assignment path via an ungated first day instead:
    st2 = _states([("2024-01-01","uptrend","calm"), ("2024-01-08","downtrend","calm")])
    res2 = run_wheel(_chain(rows), _cfg(regime_entry_gate=True), regime_states=st2)
    assert [t.action for t in res2.trades] == ["SELL_PUT", "ASSIGNED", "SELL_CALL"]
```

- [ ] **Step 2: Verify failure** (AttributeError / assertion).
- [ ] **Step 3: Implement.** `WheelResult` gains `gate_events: list = None` and `days_entry_gated: int = 0`. In `run_wheel` init: `gate_events, days_entry_gated = [], 0`; compute once per day (top of loop, after `prev_d = d`):

```python
        g_trend = g_vol = None
        if any_gate:
            g_trend, g_vol = _state_before(regime_states, d)
```

Entry step PUT branch — first lines inside `if phase == "PUT":`:

```python
                if cfg.regime_entry_gate:
                    if (g_trend, g_vol) == ("unknown", "unknown"):
                        warnings.append((d, "gate_state_unknown", "entry"))
                    elif is_unpaid_decline(g_trend, g_vol):
                        days_entry_gated += 1
                        gate_events.append((d, "entry_gated", None))
                        c = None   # fall through: no selection, day counts flat below
                if not (cfg.regime_entry_gate and gate_events and gate_events[-1] == (d, "entry_gated", None)):
                    c = select_contract(...)
```

(Implementer note: express control flow cleanly — e.g. an `entry_gated_today` local bool instead of re-inspecting `gate_events`; behavior as tested is what's binding.) Return `WheelResult(..., gate_events=gate_events, days_entry_gated=days_entry_gated)`.

- [ ] **Step 4: Tests pass; full suite green (plain regression untouched).**
- [ ] **Step 5: Commit** — `feat: regime entry gate — no new campaign in unpaid decline`

### Task 3: Roll gate

**Files:** same as Task 2.

**Interfaces:**
- Produces: gate_events `(date, "roll_denied_by_gate", contract)`, warnings `(date, "gate_state_unknown", "roll")`.

- [ ] **Step 1: Failing tests.** Roll trigger fixture: tested put mid-life (spot ≤ strike), destination available for credit.

```python
ROLL_ROWS = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    # day 2: spot 468 <= 470 (tested), same-strike out-roll available for credit
    ["2024-01-03","2024-01-09",6,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
    ["2024-01-03","2024-01-16",13,470,"P",5.00,5.10,5.05,5.05,-0.50,0.1,468.0],
    # settle both potential legs OTM so the run ends clean
    ["2024-01-09","2024-01-09",0,470,"P",0.05,0.10,0.07,0.07,-0.01,0.1,475.0],
    ["2024-01-16","2024-01-16",0,470,"P",0.05,0.10,0.07,0.07,-0.01,0.1,475.0],
]

def test_roll_gate_denies_roll_in_unpaid_decline():
    st = _states([("2024-01-01","downtrend","normal")])
    cfg = _cfg(roll_tested_puts=True, regime_roll_gate=True)
    res = run_wheel(_chain(ROLL_ROWS), cfg, regime_states=st)
    assert not [t for t in res.trades if t.action == "ROLL_CLOSE"]
    ev = [e for e in res.gate_events if e[1] == "roll_denied_by_gate"]
    assert ev and ev[0][0] == pd.Timestamp("2024-01-03")

def test_roll_gate_allows_roll_in_panic():
    st = _states([("2024-01-01","downtrend","stressed")])
    cfg = _cfg(roll_tested_puts=True, regime_roll_gate=True)
    res = run_wheel(_chain(ROLL_ROWS), cfg, regime_states=st)
    assert [t for t in res.trades if t.action == "ROLL_CLOSE"]

def test_roll_gate_unknown_state_allows_and_warns():
    st = _states([("2023-06-01","downtrend","normal")])
    cfg = _cfg(roll_tested_puts=True, regime_roll_gate=True)
    res = run_wheel(_chain(ROLL_ROWS), cfg, regime_states=st)
    assert [t for t in res.trades if t.action == "ROLL_CLOSE"]
    assert (pd.Timestamp("2024-01-03"), "gate_state_unknown", "roll") in res.warnings
```

(Entry on 2024-01-02 is ungated in the first test — downtrend+normal gates *entries* only via `regime_entry_gate`, which is off.)

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.** Inside the roll block, immediately after the trigger condition and before the `mark is None` check:

```python
                if cfg.regime_roll_gate:
                    if (g_trend, g_vol) == ("unknown", "unknown"):
                        warnings.append((d, "gate_state_unknown", "roll"))
                    elif is_unpaid_decline(g_trend, g_vol):
                        gate_events.append((d, "roll_denied_by_gate", c))
                        roll_gated_today = True
```

and add `not roll_gated_today` to the conditions guarding the roll action (init `roll_gated_today = False` per day). Denied roll re-evaluates tomorrow; stop check still runs (a denied roll must not consume the stop the way an executed roll does).

- [ ] **Step 4: Tests pass; suite green.**
- [ ] **Step 5: Commit** — `feat: regime roll gate — no roll extension in unpaid decline`

### Task 4: Stop gate

**Files:** same as Task 2.

**Interfaces:**
- Produces: gate_events `(date, "stop_suppressed_by_gate", contract)`, warnings `(date, "gate_state_unknown", "stop")`.

- [ ] **Step 1: Failing tests.** Stop trigger fixture: put ask blows through 3× credit mid-life.

```python
STOP_ROWS = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-03","2024-01-09",6,470,"P",6.90,7.00,6.95,6.95,-0.80,0.1,463.0],
    ["2024-01-09","2024-01-09",0,470,"P",0.05,0.10,0.07,0.07,-0.01,0.1,475.0],
]

def test_stop_gate_suppresses_stop_in_stressed_vol():
    st = _states([("2024-01-01","downtrend","stressed")])
    cfg = _cfg(put_stop_mult=3.0, regime_stop_gate=True)
    res = run_wheel(_chain(STOP_ROWS), cfg, regime_states=st)
    assert not [t for t in res.trades if t.action == "STOP_CLOSE"]
    ev = [e for e in res.gate_events if e[1] == "stop_suppressed_by_gate"]
    assert ev and ev[0][0] == pd.Timestamp("2024-01-03")

def test_stop_gate_lets_stop_fire_when_not_stressed():
    for vol in ("calm", "normal"):
        st = _states([("2024-01-01","downtrend",vol)])
        cfg = _cfg(put_stop_mult=3.0, regime_stop_gate=True)
        res = run_wheel(_chain(STOP_ROWS), cfg, regime_states=st)
        assert [t for t in res.trades if t.action == "STOP_CLOSE"], vol

def test_stop_gate_unknown_state_allows_stop_and_warns():
    st = _states([("2023-06-01","uptrend","stressed")])
    cfg = _cfg(put_stop_mult=3.0, regime_stop_gate=True)
    res = run_wheel(_chain(STOP_ROWS), cfg, regime_states=st)
    assert [t for t in res.trades if t.action == "STOP_CLOSE"]
    assert (pd.Timestamp("2024-01-03"), "gate_state_unknown", "stop") in res.warnings
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.** In the stop block, replace the firing branch:

```python
                elif mark.ask >= cfg.put_stop_mult * short["credit"]:
                    if cfg.regime_stop_gate and g_vol == "stressed":
                        gate_events.append((d, "stop_suppressed_by_gate", c))
                    else:
                        if cfg.regime_stop_gate and (g_trend, g_vol) == ("unknown", "unknown"):
                            warnings.append((d, "gate_state_unknown", "stop"))
                        ... existing STOP_CLOSE body ...
```

Suppression only logged when the stop WOULD have fired (would-act moments only — no noise). Stop re-arms automatically on the first non-stressed would-fire day.

- [ ] **Step 4: Tests pass; suite green.**
- [ ] **Step 5: Commit** — `feat: regime stop gate — stop suppressed in stressed vol`

### Task 5: Plain-path invariance + no-look-ahead property

**Files:**
- Test: `tests/engine_v2/options/test_regime_gates.py`, `tests/engine_v2/options/test_plain_regression.py` (read to confirm untouched-green only)

- [ ] **Step 1: Write tests** (these should pass immediately if Tasks 1–4 are right — they are the spec's invariance clauses, kept even though they aren't TDD-red):

```python
def test_states_passed_flags_off_is_byte_identical():
    st = _states([("2024-01-01","downtrend","calm")])
    a = run_wheel(_chain(PUT_DAY), _cfg())
    b = run_wheel(_chain(PUT_DAY), _cfg(), regime_states=st)
    assert [(t.date, t.action, t.cash_after) for t in a.trades] == \
           [(t.date, t.action, t.cash_after) for t in b.trades]
    assert a.equity.equals(b.equity) and a.final_cash == b.final_cash

def test_no_lookahead_future_states_do_not_change_decisions():
    # identical states up to the chain window; extra FUTURE rows must not matter
    base = [("2024-01-01","downtrend","calm")]
    future = base + [("2024-06-01","uptrend","calm")]
    cfg = _cfg(regime_entry_gate=True)
    a = run_wheel(_chain(PUT_DAY), cfg, regime_states=_states(base))
    b = run_wheel(_chain(PUT_DAY), cfg, regime_states=_states(future))
    assert [(t.date, t.action) for t in a.trades] == [(t.date, t.action) for t in b.trades]
    assert a.days_entry_gated == b.days_entry_gated

def test_same_day_state_is_not_used():
    # state flips to unpaid decline ON the entry day; strictly-prior rule must
    # use the previous (benign) day -> entry allowed.
    st = _states([("2024-01-01","uptrend","calm"), ("2024-01-02","downtrend","calm")])
    res = run_wheel(_chain(PUT_DAY), _cfg(regime_entry_gate=True), regime_states=st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
```

- [ ] **Step 2: Run new tests + `test_plain_regression.py` + full suite.** All green.
- [ ] **Step 3: Commit** — `test: regime gates — plain-path invariance and no-look-ahead properties`

### Task 6: Reporting

**Files:**
- Modify: `src/engine_v2/options/report.py` (`wheel_stats`, `WheelReport`, `wheel_report`, `format_report`)
- Test: `tests/engine_v2/options/test_regime_gates.py`

**Interfaces:**
- Produces: `WheelReport.gates: dict | None` with keys `days_entry_gated, n_rolls_denied, n_stops_suppressed, n_state_unknown`; format block "Regime gates" shown whenever any gate flag on, even all-zero.

- [ ] **Step 1: Failing tests**

```python
from src.engine_v2.options.report import wheel_report, format_report

def test_report_gates_block_present_when_flag_on_even_if_never_fired():
    st = _states([("2024-01-01","uptrend","calm")])
    ch = _chain(PUT_DAY); cfg = _cfg(regime_entry_gate=True)
    rep = wheel_report(run_wheel(ch, cfg, regime_states=st), ch, cfg)
    assert rep.gates == {"days_entry_gated": 0, "n_rolls_denied": 0,
                         "n_stops_suppressed": 0, "n_state_unknown": 0}
    assert "Regime gates" in format_report(rep)

def test_report_gates_none_on_plain_run():
    ch = _chain(PUT_DAY); cfg = _cfg()
    rep = wheel_report(run_wheel(ch, cfg), ch, cfg)
    assert rep.gates is None and "Regime gates" not in format_report(rep)

def test_report_gates_counts_fired_events():
    st = _states([("2024-01-01","downtrend","calm")])
    ch = _chain(PUT_DAY); cfg = _cfg(regime_entry_gate=True)
    rep = wheel_report(run_wheel(ch, cfg, regime_states=st), ch, cfg)
    assert rep.gates["days_entry_gated"] == 1
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.** `WheelReport` gains `gates: dict | None = None`. In `wheel_report`, after the defense block:

```python
    gates = None
    if cfg.regime_entry_gate or cfg.regime_roll_gate or cfg.regime_stop_gate:
        ev = getattr(result, "gate_events", []) or []
        wn = getattr(result, "warnings", []) or []
        gates = {
            "days_entry_gated": getattr(result, "days_entry_gated", 0),
            "n_rolls_denied": sum(1 for e in ev if e[1] == "roll_denied_by_gate"),
            "n_stops_suppressed": sum(1 for e in ev if e[1] == "stop_suppressed_by_gate"),
            "n_state_unknown": sum(1 for w in wn if w[1] == "gate_state_unknown"),
        }
```

`format_report` after the defense block:

```python
    if rep.gates is not None:
        g = rep.gates
        L.append("\nRegime gates (ticker state, strictly-prior-day)")
        L.append(f"  entry-gated days {g['days_entry_gated']}  "
                 f"rolls denied {g['n_rolls_denied']}  "
                 f"stops suppressed {g['n_stops_suppressed']}  "
                 f"state-unknown consultations {g['n_state_unknown']}")
        L.append("  note: gated-entry counterfactual is not modeled — the paired "
                 "A/B run is the measurement")
```

- [ ] **Step 4: Tests pass; suite green.**
- [ ] **Step 5: Commit** — `feat: regime-gates report block (shown whenever a gate is armed)`

### Task 7: Execution audit extension

**Files:**
- Modify: `scripts/audit_defense_execution.py`

**Interfaces:**
- Consumes: `regime_series`, `closes_for` (regime package — scripts may import it), Task 1 helpers.
- Produces: three new audited variants (`entry-gate`, `roll+gate`, `stop+gate`); per-run assertions: no SELL_PUT on entry-gated days; no ROLL_CLOSE on roll-gated days; no STOP_CLOSE on stressed days under stop gate; every gate_event matches a re-derived gate day.

- [ ] **Step 1: Implement** (audit script has no unit tests — its own exit code is the test; it re-derives rules independently):
  - Add imports: `from src.engine_v2.regime.state import regime_series`, `from src.engine_v2.regime.data import closes_for`, and `is_unpaid_decline, _state_before` from wheel (re-derivation uses the same *constants* but independent day-walk; acceptable — the decision logic re-derived is the *composition*, not the cell test).
  - `VARIANTS` additions:

```python
    "entry-gate":  {"regime_entry_gate": True},
    "roll+gate":   {"roll_tested_puts": True, "regime_roll_gate": True},
    "stop+gate":   {"put_stop_mult": 3.0, "regime_stop_gate": True},
    "all-gates":   {"roll_tested_puts": True, "put_stop_mult": 3.0,
                    "regime_entry_gate": True, "regime_roll_gate": True,
                    "regime_stop_gate": True},
```

  - In `audit()`: when any gate flag in overrides, build `states = regime_series(closes_for(ticker))` and pass to `run_wheel`. Inside the day loop, compute `g = _state_before(states, d)`; modify expected-rule derivation: roll rule additionally requires `not (cfg.regime_roll_gate and is_unpaid_decline(*g))`; stop rule additionally requires `not (cfg.regime_stop_gate and g[1] == "stressed")`.
  - After the leg loop add the entry-gate check: for every SELL_PUT trade, if entry gate on, assert `not is_unpaid_decline(*_state_before(states, t.date))` — a SELL_PUT on a gated day is a mismatch.
  - gate_events cross-check: every `(d, "roll_denied_by_gate", c)` / `(d, "stop_suppressed_by_gate", c)` must re-derive as a day the un-gated rule would have fired AND the gate condition held; every `(d, "entry_gated", None)` must re-derive as unpaid-decline.
- [ ] **Step 2: Run** `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution SPY` — exit 0. Then all four seen tickers. Exit 0.
- [ ] **Step 3: Commit** — `feat: execution audit covers regime gates (entry/roll/stop re-derived)`

### Task 8: A/B runner script (seen tickers only until basket run)

**Files:**
- Create: `scripts/run_regime_gates.py`

**Interfaces:**
- Produces: `data/options/reports/regime_gates.txt` — arms E (plain vs +entry gate), R (roll-tested vs +roll gate), S (put-stop-3x vs +stop gate) per ticker, raw.

- [ ] **Step 1: Implement** — mirror `run_defense_matrix.py` structure:

```python
"""Regime-gates A/B (pre-registered, spec 2026-07-14): three paired arms at the
frozen basket config. Baseline vs gated, per ticker, raw — no best-cell selection.

DISCIPLINE: default tickers are the SEEN set. The all-10 run (incl. unseen
XBI EEM EWZ TLT ARKK) happens ONLY after the pre-registered basket run;
requesting an unseen ticker requires --after-basket-run.

Run: PYTHONPATH=. .venv/bin/python scripts/run_regime_gates.py [TICKER ...]
"""
import sys
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)
SEEN = ["SPY", "GDX", "SLV", "XOP"]
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK"}
ARMS = [  # (arm, baseline overrides, gated overrides)
    ("E", {}, {"regime_entry_gate": True}),
    ("R", {"roll_tested_puts": True},
          {"roll_tested_puts": True, "regime_roll_gate": True}),
    ("S", {"put_stop_mult": 3.0},
          {"put_stop_mult": 3.0, "regime_stop_gate": True}),
]

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = [t.upper() for t in args] or SEEN
    blocked = [t for t in tickers if t in UNSEEN]
    if blocked and "--after-basket-run" not in sys.argv:
        sys.exit(f"REFUSED: {blocked} are pre-registered unseen tickers. "
                 f"Run the basket first, then pass --after-basket-run.")
    lines = []
    for t in tickers:
        ch = pd.read_parquet(chain_path(t))
        states = regime_series(closes_for(t))
        lines.append(f"\n=== {t}  ({ch['date'].min().date()} → {ch['date'].max().date()}) ===")
        lines.append(f"{'arm':<22} {'P&L':>10} {'Sharpe':>7} {'maxDD':>8} "
                     f"{'gated/denied/suppr':>19} {'unknown':>8}")
        for arm, base_ov, gate_ov in ARMS:
            for label, ov in ((f"{arm}: baseline", base_ov), (f"{arm}: gated", gate_ov)):
                cfg = WheelConfig(ticker=t, **BASE, **ov)
                res = run_wheel(ch, cfg, regime_states=states)
                rep = wheel_report(res, ch, cfg)
                g = rep.gates or {}
                fired = (f"{g.get('days_entry_gated',0)}/{g.get('n_rolls_denied',0)}"
                         f"/{g.get('n_stops_suppressed',0)}")
                lines.append(f"{label:<22} {res.equity.iloc[-1]-cfg.starting_capital:>10,.0f} "
                             f"{rep.metrics['sharpe']:>7.2f} {rep.metrics['max_drawdown']:>8.1%} "
                             f"{fired:>19} {g.get('n_state_unknown',0):>8}")
        bh = ch.groupby('date')['underlying'].first()
        lines.append(f"{'buy-hold ' + t:<22} {100_000*(bh.iloc[-1]/bh.iloc[0]-1):>10,.0f}")
    txt = "\n".join(lines)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/regime_gates.txt").write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run on SPY** — completes, gates fire (entry-gated days > 0 expected: SPY had unpaid-decline stretches in 2018/2022). Then all four seen tickers; inspect output sanity.
- [ ] **Step 3: Commit** — `feat: regime-gates A/B runner (seen tickers; unseen blocked until basket run)`

### Task 9: Stress + audit rounds, coverage, wrap-up

**Files:** whatever the findings demand.

- [ ] **Step 1:** Full suite + coverage: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2 -q --cov=src/engine_v2 --cov-report=term | tail -20` — ≥95%.
- [ ] **Step 2:** Execution audit, all seen tickers, exit 0.
- [ ] **Step 3:** Adversarial review of the full diff (code-review at high effort); fix verified findings; re-run suite + audit.
- [ ] **Step 4:** Real-data stress: A/B runner output cross-checked against autopsy expectations (entry gate must bind mostly in 2018/2022-style windows; stop suppressions must cluster in stressed periods); investigate any surprise.
- [ ] **Step 5:** Update vault `08-Wheel-Bot/STATUS.md` + session log; final commit.
