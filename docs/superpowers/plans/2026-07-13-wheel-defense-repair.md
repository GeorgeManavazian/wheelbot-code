# Wheel Defense Mechanics Repair — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the four wheel defense mechanics (stop, roll, basis floor, liquidate) per spec `docs/superpowers/specs/2026-07-13-wheel-defense-repair-design.md`, rebuild the roll as a mid-life credit-only capped roll, and add campaign-level accounting so every defense action is measurable.

**Architecture:** All engine changes live in `src/engine_v2/options/wheel.py` (state machine) and `select.py` (selection fallback); reporting in `report.py`. Plain path (all defense flags off) must stay byte-identical — pinned by a digest regression test written BEFORE any change.

**Tech Stack:** Python 3.9, pandas, pytest. Synthetic-chain unit tests follow the existing `_chain(rows)` pattern in `tests/engine_v2/options/test_wheel_defense.py`.

## Global Constraints

- **Test command:** `.venv/bin/python -m pytest` — NEVER `.venv/bin/pytest` (stale shebang: repo moved from `Trading code/` to `Trading/code/`, script shebangs point at the dead path).
- **Plain-path invariance:** with `call_min_strike=None, roll_tested_puts=False, liquidate_assignment=False, put_stop_mult=None`, output is byte-identical to current `main` (Task 1's digest test enforces; it must stay green through every task).
- **No new tunables:** one flag rename (`roll_puts` → `roll_tested_puts`); `MAX_ROLLS_PER_CAMPAIGN = 2` is a module constant, not config.
- **Coverage floor:** `make ...` equivalent — `.venv/bin/python -m pytest tests/engine_v2/ --cov=src/engine_v2 --cov-fail-under=85` must pass at the end.
- Decision order inside a day, per spec: **TP → roll → stop → expiry**; a day with a roll runs no stop check; `STOP_CLOSE` blocks all same-day entries.
- Commit after every task (messages given per task).

---

### Task 1: Plain-path regression digest (BEFORE any change)

**Files:**
- Create: `tests/engine_v2/options/test_plain_regression.py`

**Interfaces:**
- Produces: `_digest(res) -> str` (sha256 over equity, trades, final state) used only within this test file. Digest deliberately EXCLUDES fields added later (`campaign_id`, `warnings`, `days_shares_uncovered`) so it stays valid for the whole plan.

- [ ] **Step 1: Write the test with placeholder digests**

```python
"""Plain-path regression: with all defense flags off, the wheel engine's output
is byte-identical to the pre-repair engine (main @ a39407d). Digest excludes
fields the repair pass adds (campaign_id, warnings, days_shares_uncovered)."""
import hashlib
import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

FIX = "fixtures/spy_wheel_cycle.parquet"

def _digest(res) -> str:
    h = hashlib.sha256()
    for ts, v in res.equity.items():
        h.update(f"{ts.isoformat()}:{v:.6f};".encode())
    for t in res.trades:
        h.update(f"{t.date}|{t.action}|{t.contract}|{t.contracts}"
                 f"|{t.price_per_contract:.6f}|{t.cash_after:.6f};".encode())
    h.update(f"{res.final_cash:.6f}|{res.final_shares}|{res.days_flat}".encode())
    return h.hexdigest()

GOLDEN = {
    "tp50":  "PIN_ME",
    "hold":  "PIN_ME",
}

@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")
@pytest.mark.parametrize("name,cfg", [
    ("tp50", WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30)),
    ("hold", WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30,
                         take_profit_pct=None)),
])
def test_plain_path_is_byte_identical(name, cfg):
    res = run_wheel(pd.read_parquet(FIX), cfg)
    assert _digest(res) == GOLDEN[name]
```

- [ ] **Step 2: Print the real digests on the CURRENT engine and pin them**

Run:
```bash
.venv/bin/python -c "
import pandas as pd
from tests.engine_v2.options.test_plain_regression import _digest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
fix = pd.read_parquet('fixtures/spy_wheel_cycle.parquet')
for name, cfg in [('tp50', WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30)),
                  ('hold', WheelConfig(target_dte=40, put_delta=0.30, call_delta=0.30, take_profit_pct=None))]:
    print(name, _digest(run_wheel(fix, cfg)))
"
```
Copy the two hex strings into `GOLDEN`, replacing `PIN_ME`.

- [ ] **Step 3: Run the test to verify it passes on the unmodified engine**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_plain_regression.py -v`
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/engine_v2/options/test_plain_regression.py
git commit -m "test: pin plain-path digest before defense repair (regression contract)"
```

---

### Task 2: Config rename, validation, old at-expiry roll removed

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (config lines 20–25, roll block lines 117–127, add validation at top of `run_wheel`)
- Modify: `scripts/run_defense_matrix.py:20`
- Modify: `dashboard/views/wheel.py:86`
- Modify: `tests/engine_v2/options/test_wheel_defense.py` (delete 4 old roll tests, add 2 new)

**Interfaces:**
- Produces: `WheelConfig.roll_tested_puts: bool = False` (mechanics arrive in Task 5 — until then the flag is accepted and does nothing); `run_wheel` raises `ValueError` on `liquidate_assignment=True` + `call_min_strike is not None`. `roll_puts` ceases to exist (constructor `TypeError` if passed).

- [ ] **Step 1: Write the failing tests** (append to `test_wheel_defense.py`; also DELETE `test_roll_puts_buys_back_at_ask_and_sells_fresh_put_same_day`, `test_roll_puts_missing_mark_falls_back_to_intrinsic`, `test_roll_puts_skips_entry_when_only_identical_contract_available`, `test_roll_puts_otm_expiry_unchanged` — the at-expiry roll is retired by spec; replacement tests come with the mid-life roll in Task 5)

```python
# ---- repair pass: config surface ----

def test_roll_puts_flag_no_longer_exists():
    with pytest.raises(TypeError):
        WheelConfig(roll_puts=True)

def test_contradictory_liquidate_plus_basis_rejected():
    rows = _ASSIGN
    with pytest.raises(ValueError):
        run_wheel(_chain(rows), _cfg(liquidate_assignment=True, call_min_strike="basis"))

def test_roll_tested_puts_flag_accepted_default_off():
    cfg = _cfg()
    assert cfg.roll_tested_puts is False
    cfg2 = _cfg(roll_tested_puts=True)
    assert cfg2.roll_tested_puts is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_wheel_defense.py -v -k "roll_puts_flag or contradictory or tested_puts_flag"`
Expected: FAIL (`roll_puts` still accepted / no ValueError / no `roll_tested_puts` attribute)

- [ ] **Step 3: Implement**

In `src/engine_v2/options/wheel.py`, replace the config block:

```python
    # defense variants (amendment 2026-07-12b, repaired 2026-07-13) — all
    # default-off so plain behavior is byte-identical.
    call_min_strike: str | None = None   # covered calls only at strike >= net basis
    roll_tested_puts: bool = False       # mid-life roll of tested puts (credit-only, capped)
    liquidate_assignment: bool = False   # take assignment, dump all shares at that day's spot, back to puts
    put_stop_mult: float | None = None   # buy the put back when EOD ask >= mult x credit received (puts only)
```

At the top of `run_wheel`, before the `dates = ...` line:

```python
    if cfg.liquidate_assignment and cfg.call_min_strike is not None:
        raise ValueError("liquidate_assignment never holds shares; call_min_strike "
                         "governs held shares — enable one, not both")
```

Delete the at-expiry roll branch (the `if spot < c.strike and cfg.roll_puts:` block, lines 119–127) so the ITM-at-expiry path reads:

```python
            if short is not None and d == c.expiry:
                if c.right == "P":
                    if spot < c.strike:
                        cash -= c.strike * mult * n; shares += mult * n; phase = "CALL"
                        basis = c.strike
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash))
                        if cfg.liquidate_assignment:
                            # pure put-write: dump the shares at spot same day
                            cash += shares * spot
                            trades.append(Trade(d, "LIQUIDATE", c, n, spot, cash))
                            shares = 0; phase = "PUT"; basis = None
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash))
```

In `scripts/run_defense_matrix.py` line 20: `"roll-puts":   {"roll_puts": True},` → `"roll-tested": {"roll_tested_puts": True},`
In `dashboard/views/wheel.py` line 86: `"Roll puts (never assign)": {"roll_puts": True},` → `"Roll tested puts (mid-life)": {"roll_tested_puts": True},`

- [ ] **Step 4: Run the new tests, the regression, and the full options suite**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS (old roll tests deleted; regression digests unchanged — the deleted branch is dead code when the flag is off)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: retire at-expiry roll_puts (dominated), add roll_tested_puts flag + config validation"
```

---

### Task 3: Campaign ids and warnings plumbing

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (`Trade`, `WheelResult`, `run_wheel` body)
- Create: `tests/engine_v2/options/test_campaigns.py`

**Interfaces:**
- Produces: `Trade.campaign_id: int = 0` (last positional field); `WheelResult.warnings: list` (of `(date, reason_str, contract)` tuples) and `WheelResult.days_shares_uncovered: int`; inside `run_wheel` the locals `campaign` (int), `rolls_this_campaign` (int), `campaign_premium` (float, net option cash this campaign) — Tasks 5 and 6 consume all three.
- A campaign starts at each `SELL_PUT` opened from flat via the entry step and covers every trade until the position returns to flat cash. `ROLL_OPEN` (Task 5) does NOT start a campaign.

- [ ] **Step 1: Write the failing tests**

```python
"""Campaign accounting: every trade carries the id of the wheel saga it belongs
to (entry -> defenses -> assignment -> calls -> exit = one campaign)."""
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def _cfg(**kw):
    base = dict(starting_capital=50_000.0, put_delta=0.30, call_delta=0.30,
                take_profit_pct=None, commission_per_contract=0.0)
    base.update(kw); return WheelConfig(**base)

# saga: put sold, assigned, call sold, called away -> ONE campaign; the next
# put entry -> campaign 2.
_SAGA = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-09","2024-01-09",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
    ["2024-01-09","2024-01-16",7,470,"C",2.00,2.10,2.05,2.05,0.30,0.1,465.0],
    ["2024-01-16","2024-01-16",0,470,"C",5.00,5.10,5.05,5.05,0.99,0.1,476.0],
    ["2024-01-16","2024-01-23",7,472,"P",2.00,2.10,2.05,2.05,-0.30,0.1,476.0],
    ["2024-01-23","2024-01-23",0,472,"P",0.05,0.10,0.075,0.075,-0.01,0.1,480.0],
]

def test_full_saga_is_one_campaign_next_entry_is_new():
    res = run_wheel(_chain(_SAGA), _cfg())
    acts = {t.action: t.campaign_id for t in res.trades}
    assert acts["SELL_PUT"] == 1        # first entry
    assert acts["ASSIGNED"] == 1
    assert acts["SELL_CALL"] == 1
    assert acts["CALLED_AWAY"] == 1
    second_puts = [t for t in res.trades if t.action == "SELL_PUT"]
    assert second_puts[-1].campaign_id == 2
    expired = [t for t in res.trades if t.action == "PUT_EXPIRED"]
    assert expired and expired[-1].campaign_id == 2

def test_warnings_field_exists_and_empty_on_clean_run():
    res = run_wheel(_chain(_SAGA), _cfg())
    assert res.warnings == []

def test_days_shares_uncovered_counts_naked_share_days():
    # assignment day has a call to sell -> 0 uncovered days; drop the call rows
    # and shares sit naked from assignment to the end of the chain.
    naked = _SAGA[:2] + [
        ["2024-01-10","2024-01-17",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,465.0],
        ["2024-01-11","2024-01-18",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,465.0],
    ]
    res = run_wheel(_chain(naked), _cfg())
    assert res.days_shares_uncovered == 2   # Jan 10 and Jan 11: shares held, no call short
    covered = run_wheel(_chain(_SAGA), _cfg())
    assert covered.days_shares_uncovered == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_campaigns.py -v`
Expected: FAIL — `Trade.__init__` has no `campaign_id`; `WheelResult` has no `warnings`

- [ ] **Step 3: Implement**

In `wheel.py`:

```python
@dataclass
class Trade:
    date: pd.Timestamp
    action: str
    contract: object
    contracts: int
    price_per_contract: float
    cash_after: float
    campaign_id: int = 0
```

```python
@dataclass
class WheelResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: int
    residual_settled: bool = False
    days_flat: int = 0
    warnings: list = None
    days_shares_uncovered: int = 0
```

(`warnings: list = None` then `if warnings is None` is NOT needed — `run_wheel` always passes a list; keep the default for backward construction in old tests.)

In `run_wheel`, extend the state line and add trackers:

```python
    cash, shares, phase, short = cfg.starting_capital, 0, "PUT", None
    basis = None  # assigned put's strike while shares are held (defense variants)
    campaign, rolls_this_campaign, campaign_premium = 0, 0, 0.0
    warnings, days_shares_uncovered = [], 0
    trades, equity = [], {}
```

Every existing `trades.append(Trade(...))` call gains `, campaign` as the final argument (there are 10 call sites after Task 2; do all of them — TP intraday fill, TP EOD close, stop, assigned, liquidate, put-expired, called-away, call-expired, sell-put, sell-call).

New-campaign increment in the entry step (the `if phase == "PUT":` branch), immediately before `cash += sell_proceeds(mark, n, cfg)`:

```python
                    if n > 0:
                        campaign += 1
                        rolls_this_campaign, campaign_premium = 0, 0.0
                        cash += sell_proceeds(mark, n, cfg)
```

`campaign_premium` bookkeeping (consumed by Task 6's net basis):
- after each `cash += sell_proceeds(mark, n, cfg)` (SELL_PUT and SELL_CALL entries): `campaign_premium += sell_proceeds(mark, n, cfg)` — compute once into a local `proceeds = sell_proceeds(mark, n, cfg)` and reuse to avoid double evaluation.
- after each `cash -= buy_cost(mark, n, cfg)` (TP EOD close, stop): `campaign_premium -= buy_cost(mark, n, cfg)` — same local-variable treatment (`cost = buy_cost(mark, n, cfg)`).
- in the intraday TP fill branch: `cost = fill["close"] * mult * n + cfg.commission_per_contract * n`, then `cash -= cost; campaign_premium -= cost`.

Uncovered-day counter, next to the existing flat counter:

```python
        if short is None and not (phase == "CALL" and shares >= mult):
            days_flat += 1
        if short is None and phase == "CALL" and shares >= mult:
            days_shares_uncovered += 1
```

Return: `WheelResult(pd.Series(equity), trades, cash, shares, residual_settled, days_flat=days_flat, warnings=warnings, days_shares_uncovered=days_shares_uncovered)`

- [ ] **Step 4: Run new tests + full options suite (regression must stay green — digest ignores the new fields)**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: campaign ids on every trade + warnings and uncovered-days fields"
```

---

### Task 4: Stop repair (expiry guard, flat-means-flat, warning log)

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (stop block + entry gate)
- Modify: `tests/engine_v2/options/test_wheel_defense.py` (append tests)

**Interfaces:**
- Consumes: `warnings` list and `campaign` from Task 3.
- Produces: `no_entry_today` local (True after a `STOP_CLOSE`, resets every day); Task 5's roll block sits immediately BEFORE this stop block.

- [ ] **Step 1: Write the failing tests** (append to `test_wheel_defense.py`)

```python
# ---- repair pass: stop fixes ----

def test_put_stop_cannot_fire_on_expiry_day():
    # expiry day, deep ITM, ask >= 3x credit: stop must NOT fire; assignment
    # (intrinsic, no spread) resolves the day instead.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",9.00,9.10,9.05,9.05,-0.99,0.1,461.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0))
    acts = [t.action for t in res.trades]
    assert "STOP_CLOSE" not in acts
    assert "ASSIGNED" in acts

def test_stop_blocks_all_entries_same_day():
    # stop fires mid-life; a fresh sellable put exists the same day at another
    # strike -> entry must NOT happen (stop means flat), but next day it may.
    rows = [
        ["2024-01-02","2024-01-16",14,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-16",11,470,"P",6.50,6.60,6.55,6.55,-0.70,0.1,463.0],
        ["2024-01-05","2024-01-16",11,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
        ["2024-01-08","2024-01-16",8,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0))
    stop_day = [t for t in res.trades if t.action == "STOP_CLOSE"][0].date
    same_day_entries = [t for t in res.trades
                        if t.action == "SELL_PUT" and t.date == stop_day]
    assert same_day_entries == []
    next_day_entries = [t for t in res.trades
                        if t.action == "SELL_PUT" and t.date > stop_day]
    assert len(next_day_entries) == 1

def test_stop_check_missing_mark_logs_warning():
    # day 2 has no row for the held contract at all -> stop check skipped + logged
    rows = [
        ["2024-01-02","2024-01-16",14,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-16",11,455,"P",2.00,2.10,2.05,2.05,-0.30,0.1,463.0],
    ]
    res = run_wheel(_chain(rows), _cfg(put_stop_mult=3.0))
    assert any(w[1] == "stop_check_no_mark" for w in res.warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_wheel_defense.py -v -k "expiry_day or blocks_all or missing_mark_logs"`
Expected: FAIL — stop currently fires on expiry day; entry happens same day; no warning

- [ ] **Step 3: Implement**

Replace the stop block in `wheel.py` (currently `# put-stop (after the TP check ...)`) with:

```python
            # put-stop (after TP and roll checks; puts only — the shares anatomy
            # showed calls are not the losing leg). EOD marks only. Never on
            # expiry day (assignment settles at intrinsic; buying back pays the
            # spread on top). Firing means FLAT: no re-entry until tomorrow.
            if (short is not None and cfg.put_stop_mult is not None and c.right == "P"
                    and d < c.expiry):
                if mark is None:
                    warnings.append((d, "stop_check_no_mark", c))
                elif mark.ask >= cfg.put_stop_mult * short["credit"]:
                    cost = buy_cost(mark, n, cfg)
                    cash -= cost; campaign_premium -= cost
                    trades.append(Trade(d, "STOP_CLOSE", c, n, mark.ask, cash, campaign))
                    short = None; closed_today = c; no_entry_today = True
```

Add per-day reset at the top of the loop body (right after `day_chain = by_date.get(d)`):

```python
        no_entry_today = False
```

Gate the entry step:

```python
        if short is None and not no_entry_today:
```

- [ ] **Step 4: Run defense tests + full options suite**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS (regression digests untouched — plain config never enters the stop block)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix: stop respects expiry day, blocks same-day re-entry, logs skipped checks"
```

---

### Task 5: Mid-life roll (`roll_tested_puts`)

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (new roll block between TP and stop; module constant)
- Modify: `tests/engine_v2/options/test_wheel_defense.py` (append tests)

**Interfaces:**
- Consumes: `campaign`, `rolls_this_campaign`, `campaign_premium`, `warnings` (Task 3); `no_entry_today` ordering (Task 4).
- Produces: trade actions `ROLL_CLOSE` + `ROLL_OPEN` (same `campaign_id`, consecutive); module constant `MAX_ROLLS_PER_CAMPAIGN = 2`. Task 7's reporting consumes both action names.

- [ ] **Step 1: Write the failing tests** (append to `test_wheel_defense.py`)

```python
# ---- repair pass: mid-life roll ----

# tested put mid-life: 470P sold at 472, spot drops to 468 (tested) with days
# left; a farther-dated 460P pays more than the buyback -> credit-only roll fires.
_ROLLABLE = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-04","2024-01-09",5,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
    ["2024-01-04","2024-01-11",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0],
]

def test_tested_put_rolls_midlife_with_credit():
    res = run_wheel(_chain(_ROLLABLE), _cfg(roll_tested_puts=True))
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT", "ROLL_CLOSE", "ROLL_OPEN"]
    rc = res.trades[1]; ro = res.trades[2]
    assert rc.price_per_contract == pytest.approx(3.10)   # buyback at ask
    assert ro.price_per_contract == pytest.approx(3.20)   # new leg at bid
    assert ro.contract.strike == 460.0
    assert rc.campaign_id == ro.campaign_id == 1          # same saga

def test_roll_requires_net_credit():
    # new leg bid (2.90) < buyback ask (3.10) -> net debit -> no roll
    rows = [_ROLLABLE[0], _ROLLABLE[1],
        ["2024-01-04","2024-01-11",7,460,"P",2.90,3.00,2.95,2.95,-0.30,0.1,468.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_not_triggered_when_not_tested():
    # spot 471 > strike 470 -> not tested -> no roll even though credit exists
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-09",5,470,"P",3.00,3.10,3.05,3.05,-0.45,0.1,471.0],
        ["2024-01-04","2024-01-11",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,471.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_cap_two_then_normal_expiry_path():
    # three tested days each offering a credit roll -> only 2 rolls fire; the
    # third leg runs to expiry and is assigned.
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        # day 2: tested, roll 1 -> 460P Jan-11
        ["2024-01-03","2024-01-09",6,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
        ["2024-01-03","2024-01-11",8,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0],
        # day 3: tested again, roll 2 -> 450P Jan-15
        ["2024-01-04","2024-01-11",7,460,"P",3.00,3.10,3.05,3.05,-0.55,0.1,458.0],
        ["2024-01-04","2024-01-15",11,450,"P",3.20,3.30,3.25,3.25,-0.30,0.1,458.0],
        # day 4: tested a third time, credit available — cap says NO
        ["2024-01-05","2024-01-15",10,450,"P",3.00,3.10,3.05,3.05,-0.55,0.1,448.0],
        ["2024-01-05","2024-01-19",14,440,"P",3.20,3.30,3.25,3.25,-0.30,0.1,448.0],
        # expiry of the second rolled leg: ITM -> assigned
        ["2024-01-15","2024-01-15",0,450,"P",5.00,5.10,5.05,5.05,-0.99,0.1,445.0],
    ]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True, target_dte=7))
    acts = [t.action for t in res.trades]
    assert acts.count("ROLL_CLOSE") == 2
    assert acts[-1] == "ASSIGNED"

def test_roll_beats_stop_when_both_would_fire():
    # ask 6.60 >= 3x credit 2.00 AND tested AND credit roll available -> roll
    # only; the stop is skipped for the day (spec: stop re-evaluates against the
    # new leg from the next day).
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-09",5,470,"P",6.50,6.60,6.55,6.55,-0.80,0.1,464.0],
        ["2024-01-04","2024-01-11",7,455,"P",6.70,6.80,6.75,6.75,-0.30,0.1,464.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True, put_stop_mult=3.0))
    acts = [t.action for t in res.trades]
    assert "ROLL_CLOSE" in acts and "STOP_CLOSE" not in acts

def test_roll_missing_new_leg_or_mark_logs_nothing_and_waits():
    # tested but no alternative expiry exists -> select returns the same
    # contract -> no roll, position simply continues.
    rows = [_ROLLABLE[0], _ROLLABLE[1]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert [t.action for t in res.trades] == ["SELL_PUT"]

def test_roll_missing_current_mark_logs_warning():
    # held contract has no row on the tested day (unknowable ask) -> warning.
    # The 455P row establishes spot 468 for the day.
    rows = [_ROLLABLE[0],
        ["2024-01-04","2024-01-11",7,455,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0]]
    res = run_wheel(_chain(rows), _cfg(roll_tested_puts=True))
    assert any(w[1] == "roll_check_no_mark" for w in res.warnings)
```

Note for the implementer: in `test_roll_missing_current_mark_logs_warning` the roll trigger needs `spot <= strike`; spot comes from `underlying_series` (any row that day). 468 ≤ 470 ✓ — the warning fires because the HELD 470P has no mark row, even though the day has other rows.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_wheel_defense.py -v -k "roll"`
Expected: new tests FAIL (`ROLL_CLOSE` never produced — flag currently inert)

- [ ] **Step 3: Implement**

Module constant after the imports in `wheel.py`:

```python
MAX_ROLLS_PER_CAMPAIGN = 2   # then the normal expiry path (assignment) applies
```

Insert the roll block BETWEEN the TP check and the stop block (per the spec's daily order TP → roll → stop → expiry), inside `if short is not None:`. Also add `rolled_today = False` next to `closed_today = None`:

```python
            # mid-life roll of a tested put (repair spec 2026-07-13): fires while
            # extrinsic is alive, only ever for a net credit, at most
            # MAX_ROLLS_PER_CAMPAIGN times per campaign. Destination re-uses the
            # entry config (delta, target DTE) — nothing is re-tuned.
            if (short is not None and cfg.roll_tested_puts and c.right == "P"
                    and d < c.expiry and spot <= c.strike
                    and rolls_this_campaign < MAX_ROLLS_PER_CAMPAIGN):
                if mark is None:
                    warnings.append((d, "roll_check_no_mark", c))
                else:
                    new_c = select_contract(day_chain, d, "P", cfg.put_delta,
                                            cfg.target_dte, cfg.ticker)
                    new_mark = option_mark(day_chain, d, new_c) if new_c is not None else None
                    if (new_c is not None and new_c != c and new_mark is not None
                            and sell_proceeds(new_mark, n, cfg) >= buy_cost(mark, n, cfg)):
                        cost = buy_cost(mark, n, cfg)
                        cash -= cost; campaign_premium -= cost
                        trades.append(Trade(d, "ROLL_CLOSE", c, n, mark.ask, cash, campaign))
                        proceeds = sell_proceeds(new_mark, n, cfg)
                        cash += proceeds; campaign_premium += proceeds
                        short = {"contract": new_c, "contracts": n,
                                 "credit": new_mark.bid, "last_mid": new_mark.mid}
                        trades.append(Trade(d, "ROLL_OPEN", new_c, n, new_mark.bid, cash, campaign))
                        rolls_this_campaign += 1
                        c, mark = new_c, new_mark
                        rolled_today = True
```

Then guard the stop block with `and not rolled_today` (the stop re-evaluates against the new leg's own credit from the next day):

```python
            if (short is not None and cfg.put_stop_mult is not None and c.right == "P"
                    and d < c.expiry and not rolled_today):
```

The expiry check that follows (`d == c.expiry`) is naturally false after a roll (new expiry > d).

- [ ] **Step 4: Run defense tests + full options suite + regression**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: mid-life credit-only capped roll for tested puts (roll_tested_puts)"
```

---

### Task 6: Net basis + cross-expiry min_strike fallback

**Files:**
- Modify: `src/engine_v2/options/select.py` (`select_contract`)
- Modify: `src/engine_v2/options/wheel.py` (call-entry floor computation)
- Modify: `tests/engine_v2/options/test_select.py` (append fallback test)
- Modify: `tests/engine_v2/options/test_wheel_defense.py` (append net-basis tests)

**Interfaces:**
- Consumes: `campaign_premium`, `basis` (Task 3 / existing).
- Produces: `select_contract` with identical signature but multi-expiry fallback when `min_strike` filters the best expiry empty. Plain path (min_strike=None) must pick the SAME contract as before (regression test is the referee).

- [ ] **Step 1: Write the failing tests**

Append to `test_select.py` (match its existing imports/fixtures style — it builds chains with the same COLUMNS list):

```python
def test_min_strike_falls_back_to_other_inband_expiry():
    # best expiry (dte 7) has no strike >= 470; the dte-9 expiry does. The old
    # code returned None; the fallback must find the 470 in the farther expiry.
    rows = [
        ["2024-01-02","2024-01-09",7,460,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
        ["2024-01-02","2024-01-11",9,470,"C",0.60,0.70,0.65,0.65,0.20,0.1,465.0],
    ]
    ch = _chain(rows)
    c = select_contract(ch, pd.Timestamp("2024-01-02"), "C", 0.30, 7, "SPY", min_strike=470.0)
    assert c is not None and c.strike == 470.0

def test_min_strike_no_inband_expiry_qualifies_returns_none():
    rows = [
        ["2024-01-02","2024-01-09",7,460,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
        ["2024-01-02","2024-01-11",9,465,"C",0.60,0.70,0.65,0.65,0.20,0.1,465.0],
    ]
    ch = _chain(rows)
    assert select_contract(ch, pd.Timestamp("2024-01-02"), "C", 0.30, 7, "SPY",
                           min_strike=470.0) is None
```

(If `test_select.py` has no `_chain` helper, copy the one from `test_wheel_defense.py` into the test file.)

Append to `test_wheel_defense.py`:

```python
# ---- repair pass: net basis ----

def test_floor_is_net_basis_not_raw_strike():
    # assigned at 470 with 2.00 credit collected (no commission) -> net basis
    # 468. A 468 call must be eligible; under the old raw-strike floor it wasn't.
    rows = _ASSIGN + [
        ["2024-01-09","2024-01-16",7,468,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
        ["2024-01-09","2024-01-16",7,465,"C",3.00,3.10,3.05,3.05,0.60,0.1,465.0],
    ]
    res = run_wheel(_chain(rows), _cfg(call_min_strike="basis"))
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert calls and calls[0].contract.strike == 468.0

def test_floor_ratchets_down_as_call_premium_accrues():
    # saga: assigned at 470 (put credit 2.00 -> floor 468); first call at 468
    # expires worthless adding 1.00 credit -> floor 467; a 467 call becomes
    # eligible next cycle.
    rows = _ASSIGN + [
        ["2024-01-09","2024-01-16",7,468,"C",1.00,1.10,1.05,1.05,0.30,0.1,465.0],
        ["2024-01-16","2024-01-16",0,468,"C",0.05,0.10,0.075,0.075,0.01,0.1,464.0],
        ["2024-01-16","2024-01-23",7,467,"C",1.00,1.10,1.05,1.05,0.30,0.1,464.0],
        ["2024-01-16","2024-01-23",7,468,"C",0.40,0.50,0.45,0.45,0.10,0.1,464.0],
    ]
    res = run_wheel(_chain(rows), _cfg(call_min_strike="basis"))
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert len(calls) == 2
    assert calls[1].contract.strike == 467.0   # 0.30 delta beats 0.10 once eligible
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_select.py tests/engine_v2/options/test_wheel_defense.py -v -k "fallback or inband or net_basis or floor"`
Expected: FAIL

- [ ] **Step 3: Implement**

Rewrite `select_contract` in `select.py`:

```python
def select_contract(chain, date, right, target_delta, target_dte, root, min_strike=None):
    """Expiry FIRST (nearest target_dte within derived_band, from expiries visible
    on `date` only), THEN strike (nearest |delta| within that one expiry).
    With `min_strike`, expiries are tried in nearest-DTE order (tie -> longer-
    dated) and the first one containing a strike >= min_strike is used; only
    when no in-band expiry qualifies -> None. Deterministic; None -> sit in cash."""
    lo, hi = derived_band(target_dte)
    cand = chain[(chain["date"] == date) & (chain["right"] == right)]
    if cand.empty:
        return None
    dtes = cand.groupby("expiry")["dte"].first()
    dtes = dtes[(dtes >= lo) & (dtes <= hi)]
    if dtes.empty:
        return None
    err = (dtes - target_dte).abs()
    for exp in sorted(dtes.index, key=lambda e: (err[e], -dtes[e])):
        e = cand[cand["expiry"] == exp]
        if min_strike is not None:
            e = e[e["strike"] >= min_strike]
            if e.empty:
                continue
        row = e.loc[(e["delta"].abs() - abs(target_delta)).abs().idxmin()]
        return Contract(root, row["expiry"], float(row["strike"]), right)
    return None
```

(Plain path: first `exp` in the sort = minimal error, tie broken toward larger DTE — exactly the old `dtes[err == err.min()].index.max()` choice. The digest regression is the referee.)

In `wheel.py`, the call-entry branch:

```python
            elif phase == "CALL" and shares >= mult:
                floor = None
                if cfg.call_min_strike == "basis" and basis is not None:
                    floor = basis - campaign_premium / shares
                c = select_contract(day_chain, d, "C", cfg.call_delta, cfg.target_dte,
                                    cfg.ticker, min_strike=floor)
```

- [ ] **Step 4: Run the two test files + full options suite**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS, including the four pre-existing `call_min_strike` tests — CHECK THEM SPECIFICALLY: `test_call_min_strike_basis_restricts_to_strikes_at_or_above_basis` uses a 2.05-mid put credit before assignment, so the floor is now 470 − (2.00×100)/100 = 468 with `commission_per_contract=0`... if that test now selects a strike below 470 it will fail. **Expected and deliberate:** update that test's asserted floor to net basis (the spec changed the rule). Adjust `test_call_min_strike_basis_no_eligible_strike_sells_nothing_holds_shares` the same way if its strikes now qualify — raise its call strikes so they sit below the NET basis, keeping the no-eligible scenario intact. List both edits in the commit message.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: net-basis call floor + cross-expiry min_strike fallback (old raw-strike tests updated per spec)"
```

---

### Task 7: Campaign reporting, counterfactuals, position log

**Files:**
- Modify: `src/engine_v2/options/report.py`
- Modify: `tests/engine_v2/options/test_report.py` (append), `tests/engine_v2/options/test_position_log.py` (append)

**Interfaces:**
- Consumes: `Trade.campaign_id`, actions `ROLL_OPEN`/`ROLL_CLOSE`/`STOP_CLOSE`, `WheelResult.warnings`, `WheelResult.days_shares_uncovered`.
- Produces: `campaign_table(result, cfg) -> pd.DataFrame` (columns `campaign_id, opened, closed, n_trades, n_rolls, pnl, open_at_end`); `roll_counterfactuals(result, chain, cfg) -> pd.DataFrame` (columns `campaign_id, closed, strike, actual_close_pnl, held_to_expiry_pnl, roll_advantage`); `wheel_stats` gains keys `n_rolls, n_stops, n_campaigns, n_warnings, days_shares_uncovered`; `WheelReport` gains `defense: dict | None = None`; `format_report` renders a Defense block; `position_log` treats `ROLL_OPEN` as an opener.

- [ ] **Step 1: Write the failing tests**

Append to `test_report.py` (reuse its existing chain/config helpers; if none fit, copy `_chain`/`_cfg` from `test_wheel_defense.py`):

```python
from src.engine_v2.options.report import campaign_table, roll_counterfactuals

_ROLL_SAGA = [
    ["2024-01-02","2024-01-09",7,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
    ["2024-01-04","2024-01-09",5,470,"P",3.00,3.10,3.05,3.05,-0.55,0.1,468.0],
    ["2024-01-04","2024-01-11",7,460,"P",3.20,3.30,3.25,3.25,-0.30,0.1,468.0],
    # expiry of the ORIGINAL leg (for the counterfactual): spot 466 -> intrinsic 4.00
    ["2024-01-09","2024-01-09",0,470,"P",4.00,4.10,4.05,4.05,-0.99,0.1,466.0],
    # rolled leg expires worthless
    ["2024-01-11","2024-01-11",0,460,"P",0.05,0.10,0.075,0.075,-0.01,0.1,466.0],
]

def _roll_result():
    ch = _chain(_ROLL_SAGA)
    cfg = _cfg(roll_tested_puts=True)
    return run_wheel(ch, cfg), ch, cfg

def test_campaign_table_one_campaign_with_roll():
    res, ch, cfg = _roll_result()
    tbl = campaign_table(res, cfg)
    assert len(tbl) == 1
    row = tbl.iloc[0]
    assert row["campaign_id"] == 1 and row["n_rolls"] == 1
    # cash flow: +2.00 (entry) -3.10 (roll close) +3.20 (roll open) = +2.10/share
    assert row["pnl"] == pytest.approx(210.0)
    assert not row["open_at_end"]

def test_roll_counterfactual_short_leg():
    res, ch, cfg = _roll_result()
    cf = roll_counterfactuals(res, ch, cfg)
    assert len(cf) == 1
    r = cf.iloc[0]
    # actual: sold 2.00, closed 3.10 -> -1.10/share = -110
    assert r["actual_close_pnl"] == pytest.approx(-110.0)
    # held to expiry: 2.00 credit - 4.00 intrinsic -> -200
    assert r["held_to_expiry_pnl"] == pytest.approx(-200.0)
    assert r["roll_advantage"] == pytest.approx(90.0)

def test_wheel_stats_defense_keys():
    res, ch, cfg = _roll_result()
    from src.engine_v2.options.report import wheel_stats
    s = wheel_stats(res, cfg)
    assert s["n_rolls"] == 1 and s["n_stops"] == 0 and s["n_campaigns"] == 1
    # ROLL_OPEN premium counts in, ROLL_CLOSE cost counts out
    assert s["net_premium"] == pytest.approx(210.0 + 5.0)  # +0.05 leg expires… see note
```

**Note on the last assertion:** the rolled 460P expires worthless so its 3.20 credit is fully kept: net = 2.00 − 3.10 + 3.20 = 2.10/share = 210.0 total; the `+5.0` in the comment is wrong — assert `pytest.approx(210.0)`. (Left here deliberately as written-then-corrected so the implementer checks the arithmetic rather than copying blind; the correct expected value is **210.0**.)

Append to `test_position_log.py`:

```python
def test_roll_open_starts_its_own_row():
    # a rolled campaign produces two option rows: the original leg (outcome
    # "Rolled") and the rolled-into leg (outcome "Expired worthless"), each with
    # campaign_id.
    res, ch, cfg = _roll_result()   # copy the helper + _ROLL_SAGA from test_report.py
    log = position_log(res, cfg)
    opts = log[log["instrument"] == "PUT"]
    assert list(opts["outcome"]) == ["Rolled", "Expired worthless"]
    assert set(opts["campaign_id"]) == {1}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_report.py tests/engine_v2/options/test_position_log.py -v`
Expected: ImportError (`campaign_table` missing) / KeyError

- [ ] **Step 3: Implement in `report.py`**

Extend `wheel_stats` — change the sells/closes lines and add keys:

```python
    sells = of("SELL_PUT") + of("SELL_CALL") + of("ROLL_OPEN")
    closes = of("CLOSE_PUT") + of("CLOSE_CALL") + of("ROLL_CLOSE") + of("STOP_CLOSE")
```

(plain path unaffected: those actions don't occur) and in the returned dict:

```python
        "n_rolls": len(of("ROLL_CLOSE")),
        "n_stops": len(of("STOP_CLOSE")),
        "n_campaigns": len({t.campaign_id for t in trades if t.campaign_id}),
        "n_warnings": len(getattr(result, "warnings", []) or []),
        "days_shares_uncovered": getattr(result, "days_shares_uncovered", 0),
```

Also update `realized_dte` to use `of("SELL_PUT") + of("SELL_CALL") + of("ROLL_OPEN")` (rolled legs are real entries with a real DTE).

New functions:

```python
def campaign_table(result, cfg) -> pd.DataFrame:
    """One row per campaign: cash-flow P&L (exact for campaigns that ended flat;
    the final campaign may still be open -> flagged, P&L is cash-only)."""
    rows, cur = [], None
    prev_cash = cfg.starting_capital
    for t in result.trades:
        if cur is None or t.campaign_id != cur["campaign_id"]:
            if cur is not None:
                rows.append(cur)
                prev_cash = cur["_last_cash"]
            cur = dict(campaign_id=t.campaign_id, opened=t.date, closed=t.date,
                       n_trades=0, n_rolls=0, pnl=0.0, open_at_end=False,
                       _first_cash=prev_cash, _last_cash=t.cash_after)
        cur["n_trades"] += 1
        cur["closed"] = t.date
        cur["_last_cash"] = t.cash_after
        if t.action == "ROLL_CLOSE":
            cur["n_rolls"] += 1
    if cur is not None:
        cur["open_at_end"] = (result.final_shares > 0) or (not _campaign_ended_flat(result))
        rows.append(cur)
    for r in rows:
        r["pnl"] = r["_last_cash"] - r["_first_cash"]
        del r["_first_cash"], r["_last_cash"]
    return pd.DataFrame(rows, columns=["campaign_id","opened","closed","n_trades",
                                       "n_rolls","pnl","open_at_end"])

def _campaign_ended_flat(result) -> bool:
    """The last trade returned the book to flat cash iff nothing is open."""
    return result.final_shares == 0 and not result.residual_settled
```

```python
def roll_counterfactuals(result, chain, cfg) -> pd.DataFrame:
    """Short-leg-only counterfactual for every leg closed by a roll: what the
    close actually realized vs what holding THAT leg to its own expiry would
    have settled at (credit - intrinsic). Does NOT model the post-assignment
    path — labeled approximation, per spec."""
    mult, comm = cfg.contract_multiplier, cfg.commission_per_contract
    und = underlying_series(chain)
    opens, rows = {}, []
    for t in result.trades:
        if t.action in ("SELL_PUT", "SELL_CALL", "ROLL_OPEN"):
            opens[(t.contract, t.campaign_id)] = t
        elif t.action == "ROLL_CLOSE":
            o = opens.get((t.contract, t.campaign_id))
            exp = pd.Timestamp(t.contract.expiry)
            if o is None or exp not in und.index:
                continue
            n = t.contracts
            intrinsic = max(t.contract.strike - float(und[exp]), 0.0)
            actual = (o.price_per_contract - t.price_per_contract) * mult * n - 2 * comm * n
            held = (o.price_per_contract - intrinsic) * mult * n - comm * n
            rows.append(dict(campaign_id=t.campaign_id, closed=t.date,
                             strike=float(t.contract.strike),
                             actual_close_pnl=actual, held_to_expiry_pnl=held,
                             roll_advantage=actual - held))
    return pd.DataFrame(rows, columns=["campaign_id","closed","strike",
                                       "actual_close_pnl","held_to_expiry_pnl",
                                       "roll_advantage"])
```

`WheelReport` gains `defense: dict | None = None`; in `wheel_report(...)` add before the return:

```python
    ct = campaign_table(result, cfg)
    cf = roll_counterfactuals(result, chain, cfg)
    stats = wheel_stats(result, cfg)
    defense = None
    if stats["n_rolls"] or stats["n_stops"] or cfg.liquidate_assignment or cfg.call_min_strike:
        closed = ct[~ct["open_at_end"]]
        defense = {
            "n_campaigns": stats["n_campaigns"],
            "campaign_win_rate": float((closed["pnl"] > 0).mean()) if len(closed) else float("nan"),
            "rolls_per_campaign": ct["n_rolls"].value_counts().sort_index().to_dict(),
            "n_stops": stats["n_stops"],
            "roll_advantage_total": float(cf["roll_advantage"].sum()) if len(cf) else 0.0,
            "roll_advantage_positive": int((cf["roll_advantage"] > 0).sum()) if len(cf) else 0,
            "roll_advantage_negative": int((cf["roll_advantage"] < 0).sum()) if len(cf) else 0,
            "days_shares_uncovered": stats["days_shares_uncovered"],
            "n_warnings": stats["n_warnings"],
            "liquidate_fill_note": bool(cfg.liquidate_assignment),
        }
```

pass `stats=stats` (don't call `wheel_stats` twice) and `defense=defense` into the `WheelReport(...)` constructor.

`format_report` — append after the Wheel stats block:

```python
    if rep.defense:
        dd = rep.defense
        L.append("\nDefense stats (campaign-level)")
        L.append(f"  campaigns {dd['n_campaigns']}  win rate {dd['campaign_win_rate']:.0%}  "
                 f"stops {dd['n_stops']}  rolls/campaign {dd['rolls_per_campaign']}")
        L.append(f"  roll counterfactual (short-leg approx): total advantage "
                 f"{dd['roll_advantage_total']:+.0f}  "
                 f"(helped {dd['roll_advantage_positive']}, hurt {dd['roll_advantage_negative']})")
        L.append(f"  days shares uncovered {dd['days_shares_uncovered']}  "
                 f"skipped checks {dd['n_warnings']}")
        if dd["liquidate_fill_note"]:
            L.append("  note: liquidation fills at EOD spot — no stock spread/slippage modeled "
                     "(options pay full spread)")
```

`position_log` — two edits: the opener test becomes `if t.action in ("SELL_PUT", "SELL_CALL", "ROLL_OPEN"):` and every `rows.append(dict(...))` for option rows gains `campaign_id=t.campaign_id` (shares rows use the terminal trade's `t.campaign_id` too); add `"campaign_id"` to `cols`.

- [ ] **Step 4: Run report + position-log + full options suite**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/ -v`
Expected: all PASS. `test_report_format.py` may assert on exact formatted output — if it fails on the new Defense block, it only appears when `rep.defense` is truthy (plain runs keep `defense=None`), so plain-run format tests must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: campaign table, roll counterfactuals, defense report block, position-log roll rows"
```

---

### Task 8: Full suite, coverage, dashboard smoke, docs

**Files:**
- Modify: `vault` STATUS + log (outside repo), no code unless failures surface

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 0 failures (dashboard tests exercise the renamed flag label)

- [ ] **Step 2: Coverage gate**

Run: `.venv/bin/python -m pytest tests/engine_v2/ --cov=src/engine_v2 --cov-fail-under=85 -q`
Expected: pass ≥ 85%

- [ ] **Step 3: Commit any stragglers, update vault STATUS (repair pass shipped) and daily log**

```bash
git add -A && git commit -m "chore: defense repair pass complete — suite green, coverage >=85%"
```

---

## Self-review notes (run after drafting — resolved inline)

- Spec coverage: stop fixes (Task 4), roll rebuild (Task 5), net basis + fallback (Task 6), liquidate validation + honesty note (Tasks 2, 7), campaign accounting + report (Tasks 3, 7), plain-path invariance (Task 1, enforced throughout), config surface (Task 2). Exam/basket interaction needs no code — spec amendment 2026-07-13d already committed.
- Types: `Trade.campaign_id: int = 0` consistent across Tasks 3/5/7; `warnings` tuples `(date, str, Contract)` consistent Tasks 3/4/5; `campaign_table`/`roll_counterfactuals` signatures consistent between Task 7 interface block and code.
- Known deliberate test edits: four `roll_puts` tests deleted (Task 2), two raw-basis tests updated (Task 6) — both are spec-mandated behavior changes, listed in commit messages.
