# Wheel Sub-project 2 — Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. NOTE: owner wants a full audit + explicit approval after EVERY task — do NOT run tasks continuously.

**Goal:** A pure-wheel EOD backtest engine: `run_wheel(chain, config) -> WheelResult` (equity curve + trade log), built on sub-project 1's chain + primitives.

**Architecture:** New module `src/engine_v2/options/wheel.py`. A daily loop over a two-phase (PUT/CALL) state machine that sells cash-secured puts and covered calls, takes assignment/called-away at expiry, closes at a take-profit, crosses the spread + charges $0.65/contract. State-machine logic tested on hand-built synthetic chains (deterministic); one real-data golden integration.

**Tech Stack:** Python 3.9, pandas, pytest. `.venv/bin/pytest`.

## Global Constraints

- `wheel.py` imports ONLY `src/engine_v2/options/{chain,select}.py` + pandas. NOT the equity `_simulate`, gate, `compute_verdict`, `run_backtest`.
- Sub-project 1 interfaces (on main): `Contract(root, expiry, strike, right)`, `Mark(bid, ask, mid)`; `select_strike_by_delta(chain, date, right, target_delta, dte_min, dte_max) -> Contract|None`; `option_mark(chain, date, contract) -> Mark|None`; `expiry_underlying` exists but the engine uses `underlying_series` instead.
- OptionsChain columns: `date, expiry, dte, strike, right, bid, ask, mid, close, delta, iv, underlying` (tz-naive Timestamps).
- **Pure wheel** (no rolling). **Cross the spread:** sell at bid, buy-to-close at ask. **Commission $0.65/contract** on every option open AND close (not on assignment/called-away). **MTM at mid** for the between-trade equity curve.
- Cash-secured sizing: puts `n = floor(cash / (strike*mult))`; calls `n = shares // mult`.
- Expiry resolution is European-style (no early assignment): put ITM (`spot < strike`) → assigned; call ITM (`spot > strike`) → called away; `spot == strike` → OTM.
- Every task ends green: `.venv/bin/pytest tests/ -q`.

---

### Task 1: Config, Trade, fills, underlying series

**Files:**
- Create: `src/engine_v2/options/wheel.py` (this task adds the small pieces; Task 2 adds `run_wheel`)
- Test: `tests/engine_v2/options/test_wheel_parts.py`

**Interfaces produced:**
- `@dataclass WheelConfig(starting_capital=100_000.0, put_delta=0.30, call_delta=0.30, dte_min=25, dte_max=45, take_profit_pct=0.50, contract_multiplier=100, commission_per_contract=0.65)`.
- `@dataclass Trade(date, action: str, contract, contracts: int, price_per_contract: float, cash_after: float)`.
- `underlying_series(chain) -> pd.Series` (index = date, value = that date's underlying spot).
- `sell_proceeds(mark, contracts, cfg) -> float` = `mark.bid*mult*contracts - commission_per_contract*contracts`.
- `buy_cost(mark, contracts, cfg) -> float` = `mark.ask*mult*contracts + commission_per_contract*contracts`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_wheel_parts.py
import pandas as pd
from src.engine_v2.options.chain import Mark
from src.engine_v2.options.wheel import (
    WheelConfig, Trade, underlying_series, sell_proceeds, buy_cost)

def test_config_defaults():
    c = WheelConfig()
    assert c.starting_capital == 100_000.0 and c.commission_per_contract == 0.65
    assert c.contract_multiplier == 100 and c.take_profit_pct == 0.50

def test_underlying_series_one_per_date():
    cols = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]
    rows = [
        ["2024-01-02","2024-02-16",45,470,"P",1,1.1,1.05,1.05,-0.3,0.1,472.0],
        ["2024-01-02","2024-02-16",45,475,"P",2,2.1,2.05,2.05,-0.4,0.1,472.0],
        ["2024-01-03","2024-02-16",44,470,"P",1,1.1,1.05,1.05,-0.3,0.1,473.5],
    ]
    ch = pd.DataFrame(rows, columns=cols)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    u = underlying_series(ch)
    assert u[pd.Timestamp("2024-01-02")] == 472.0
    assert u[pd.Timestamp("2024-01-03")] == 473.5

def test_fills_cross_spread_and_commission():
    cfg = WheelConfig()
    m = Mark(bid=2.00, ask=2.10, mid=2.05)
    # sell 3 contracts: +2.00*100*3 - 0.65*3
    assert sell_proceeds(m, 3, cfg) == 2.00*100*3 - 0.65*3
    # buy-to-close 3: 2.10*100*3 + 0.65*3
    assert buy_cost(m, 3, cfg) == 2.10*100*3 + 0.65*3
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_parts.py -v`
Expected: FAIL — module/names missing.

- [ ] **Step 3: Write the pieces in `src/engine_v2/options/wheel.py`**

```python
# src/engine_v2/options/wheel.py
"""Pure-wheel EOD backtest engine. Builds on the options chain + primitives.
No rolling, no intraday, no metrics (sub-project 5). Isolated from the equity
engine and the gate."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .select import select_strike_by_delta, option_mark

@dataclass
class WheelConfig:
    starting_capital: float = 100_000.0
    put_delta: float = 0.30
    call_delta: float = 0.30
    dte_min: int = 25
    dte_max: int = 45
    take_profit_pct: float | None = 0.50
    contract_multiplier: int = 100
    commission_per_contract: float = 0.65

@dataclass
class Trade:
    date: pd.Timestamp
    action: str
    contract: object
    contracts: int
    price_per_contract: float
    cash_after: float

def underlying_series(chain: pd.DataFrame) -> pd.Series:
    return chain.groupby("date")["underlying"].first()

def sell_proceeds(mark, contracts, cfg) -> float:
    return (mark.bid * cfg.contract_multiplier * contracts
            - cfg.commission_per_contract * contracts)

def buy_cost(mark, contracts, cfg) -> float:
    return (mark.ask * cfg.contract_multiplier * contracts
            + cfg.commission_per_contract * contracts)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_parts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/wheel.py tests/engine_v2/options/test_wheel_parts.py
git commit -m "feat: wheel config/trade/fills/underlying-series"
```

---

### Task 2: `run_wheel` state machine + synthetic-chain tests

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (add `WheelResult` + `run_wheel`)
- Test: `tests/engine_v2/options/test_wheel_engine.py`

**Interfaces produced:**
- `@dataclass WheelResult(equity: pd.Series, trades: list, final_cash: float, final_shares: int)`.
- `run_wheel(chain, config) -> WheelResult`.

Trade `action` values: `SELL_PUT, CLOSE_PUT, PUT_EXPIRED, ASSIGNED, SELL_CALL, CLOSE_CALL, CALL_EXPIRED, CALLED_AWAY`.

- [ ] **Step 1: Write the failing test** (synthetic chains forcing each transition)

```python
# tests/engine_v2/options/test_wheel_engine.py
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

# small cfg: 1 contract, no take-profit unless a test wants it
def _cfg(**kw):
    base = dict(starting_capital=50_000.0, dte_min=1, dte_max=60,
                take_profit_pct=None, commission_per_contract=0.0)
    base.update(kw); return WheelConfig(**base)

def test_put_expires_otm_keeps_credit():
    # sell 30d put strike 470 for 2.00 on d0; at expiry spot 475 > 470 -> OTM
    rows = [
        ["2024-01-02","2024-01-05",3,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-05",0,470,"P",0.00,0.05,0.02,0.02,-0.01,0.1,475.0],
    ]
    res = run_wheel(_chain(rows), _cfg())
    acts = [t.action for t in res.trades]
    assert "SELL_PUT" in acts and "PUT_EXPIRED" in acts and "ASSIGNED" not in acts
    # 1 contract, credit 2.00*100 = 200 kept; final cash = 50000 + 200
    assert res.final_cash == pytest.approx(50_200.0)
    assert res.final_shares == 0

def test_put_itm_assigned_then_call_called_away():
    # put strike 470 ITM at expiry (spot 465) -> assigned 100 sh @470
    # then covered call strike 475 sold, ITM at its expiry (spot 480) -> called away @475
    rows = [
        ["2024-01-02","2024-01-05",3,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-05",0,470,"P",5.00,5.10,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-05","2024-01-12",7,475,"C",3.00,3.10,3.05,3.05, 0.30,0.1,465.0],
        ["2024-01-12","2024-01-12",0,475,"C",5.00,5.10,5.05,5.05, 0.99,0.1,480.0],
    ]
    res = run_wheel(_chain(rows), _cfg())
    acts = [t.action for t in res.trades]
    assert acts == ["SELL_PUT","ASSIGNED","SELL_CALL","CALLED_AWAY"]
    # cash walk (1 contract, mult 100, commission 0):
    # start 50000; +200 put credit; -47000 assigned; +300 call credit; +47500 called away
    assert res.final_cash == pytest.approx(50_000 + 200 - 47_000 + 300 + 47_500)
    assert res.final_shares == 0

def test_take_profit_closes_short_put():
    # sell put for 2.00; next day ask drops to 0.90 (< 50% of 2.00 -> 1.00) -> buy to close
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",0.80,0.90,0.85,0.85,-0.15,0.1,476.0],
    ]
    res = run_wheel(_chain(rows), _cfg(take_profit_pct=0.50))
    acts = [t.action for t in res.trades]
    assert acts[:2] == ["SELL_PUT","CLOSE_PUT"]
    # +200 credit, -90 to close => +110
    assert res.final_cash == pytest.approx(50_000 + 200 - 90)

def test_sizing_multiple_contracts_and_commission():
    # cash 50000, strike 470 -> floor(50000/47000)=1 contract; bump cash to size up
    rows = [
        ["2024-01-02","2024-01-05",3,100,"P",1.00,1.10,1.05,1.05,-0.30,0.1,102.0],
        ["2024-01-05","2024-01-05",0,100,"P",0.00,0.05,0.02,0.02,-0.01,0.1,105.0],
    ]
    res = run_wheel(_chain(rows), _cfg(starting_capital=50_000.0, commission_per_contract=0.65))
    sell = [t for t in res.trades if t.action == "SELL_PUT"][0]
    assert sell.contracts == 5   # floor(50000/(100*100)) = 5
    # credit 1.00*100*5 - 0.65*5 = 500 - 3.25
    assert res.final_cash == pytest.approx(50_000 + 500 - 3.25)

def test_equity_identity_reconciles():
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",1.50,1.60,1.55,1.55,-0.25,0.1,473.0],
    ]
    res = run_wheel(_chain(rows), _cfg())
    # on d0: cash 50200, no shares, short liability = mid 2.05*100 = 205 -> equity 49995
    assert res.equity.iloc[0] == pytest.approx(50_000 + 200 - 205)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_engine.py -v`
Expected: FAIL — `run_wheel` not defined.

- [ ] **Step 3: Add `WheelResult` + `run_wheel` to `wheel.py`**

```python
@dataclass
class WheelResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: int

def run_wheel(chain: pd.DataFrame, cfg: WheelConfig) -> WheelResult:
    dates = sorted(pd.to_datetime(chain["date"]).unique())
    und = underlying_series(chain)
    mult = cfg.contract_multiplier
    cash, shares, phase, short = cfg.starting_capital, 0, "PUT", None
    trades, equity = [], {}

    for d in dates:
        d = pd.Timestamp(d)
        spot = float(und.get(d))

        # 1) manage an existing short: take-profit, then expiry resolution
        if short is not None:
            c = short["contract"]; n = short["contracts"]
            mark = option_mark(chain, d, c)
            if (cfg.take_profit_pct is not None and mark is not None and d < c.expiry
                    and mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]):
                cash -= buy_cost(mark, n, cfg)
                trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                    c, n, mark.ask, cash))
                short = None
            if short is not None and d == c.expiry:
                if c.right == "P":
                    if spot < c.strike:
                        cash -= c.strike * mult * n; shares += mult * n; phase = "CALL"
                        trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash))
                    else:
                        trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash))
                else:
                    if spot > c.strike:
                        cash += c.strike * mult * n; shares -= mult * n; phase = "PUT"
                        trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash))
                    else:
                        trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash))
                short = None

        # 2) open a new short if flat and eligible
        if short is None:
            if phase == "PUT":
                c = select_strike_by_delta(chain, d, "P", cfg.put_delta, cfg.dte_min, cfg.dte_max)
                mark = option_mark(chain, d, c) if c is not None else None
                if c is not None and mark is not None:
                    n = int(cash // (c.strike * mult))
                    if n > 0:
                        cash += sell_proceeds(mark, n, cfg)
                        short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                        trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash))
            elif phase == "CALL" and shares >= mult:
                c = select_strike_by_delta(chain, d, "C", cfg.call_delta, cfg.dte_min, cfg.dte_max)
                mark = option_mark(chain, d, c) if c is not None else None
                if c is not None and mark is not None:
                    n = shares // mult
                    cash += sell_proceeds(mark, n, cfg)
                    short = {"contract": c, "contracts": n, "credit": mark.bid, "last_mid": mark.mid}
                    trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash))

        # 3) mark equity (short MTM at mid; carry last mid across gaps)
        liab = 0.0
        if short is not None:
            mk = option_mark(chain, d, short["contract"])
            if mk is not None:
                short["last_mid"] = mk.mid
            liab = short["last_mid"] * mult * short["contracts"]
        equity[d] = cash + shares * spot - liab

    return WheelResult(pd.Series(equity), trades, cash, shares)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_engine.py -v`
Expected: PASS (5 passed). If a cash-walk assertion is off, trace the specific transition — do NOT weaken the assertion; fix the engine.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest tests/ -q`
```bash
git add src/engine_v2/options/wheel.py tests/engine_v2/options/test_wheel_engine.py
git commit -m "feat: run_wheel pure-wheel state machine + synthetic tests"
```

---

### Task 3: Real-data golden integration

**Files:**
- Create: `fixtures/spy_wheel_cycle.parquet` (committed; one real ~30–45 DTE cycle)
- Test: `tests/engine_v2/options/test_wheel_integration.py`

**Interfaces:** consumes `run_wheel` + the sub-project-1 puller (`scripts.pull_spy_options.build`).

- [ ] **Step 1: Write the failing/ skipping test**

```python
# tests/engine_v2/options/test_wheel_integration.py
import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

FIX = "fixtures/spy_wheel_cycle.parquet"

@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")
def test_wheel_runs_on_real_cycle():
    ch = pd.read_parquet(FIX)
    res = run_wheel(ch, WheelConfig(dte_min=20, dte_max=45))
    assert len(res.equity) > 5
    assert (res.equity > 0).all()               # never blows up
    assert any(t.action == "SELL_PUT" for t in res.trades)
    # equity is finite and starts at ~capital (first bar, small short liability)
    assert abs(res.equity.iloc[0] - 100_000) < 5_000
```

- [ ] **Step 2: Run — confirm SKIP** (fixture not built yet)

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_integration.py -v` → SKIP.

- [ ] **Step 3: Build the real cycle fixture (live, tight pull)**

Confirm terminal up (`ThetaClient().is_up()`); start it if needed (see `docs/thetadata-v3-access.md`). Pull ONE monthly expiry with ~6 weeks of daily marks so a 30–45 DTE entry + hold-to-expiry cycle exists. Each greeks/eod call is ~20–30s, so keep it to a couple of expirations:

```python
.venv/bin/python -c "
import pandas as pd
from src.engine_v2.options.theta_client import ThetaClient
from scripts.pull_spy_options import build
c = ThetaClient(timeout=120)
assert c.is_up()
# ~6wk window ending at the Feb-16-2024 monthly, plus Mar-15 so a full cycle can chain
df = build(c, 'SPY', '2024-01-02', '2024-03-15', strike_range=40, dte_max=50)
df.to_parquet('fixtures/spy_wheel_cycle.parquet')
print('cycle fixture', df.shape, 'dte', df.dte.min(), df.dte.max(), 'expiries', df.expiry.nunique())
"
```
Verify the fixture spans a real DTE range (not 0–1): `dte.max()` should be ≥ 40 and there should be ≥ 2 expiries with multi-week histories. If the file is too large (> ~2 MB) narrow the window or `strike_range`.

- [ ] **Step 4: Run the integration test**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_integration.py -v` → PASS.
Then inspect the run for a sanity read (not an assertion): print the trade log and final equity — confirm the event sequence is economically plausible (puts sold, maybe an assignment, calls sold). Note anything surprising in the report.

- [ ] **Step 5: Full suite + commit** (fixture committed; it is real data, a few hundred KB)

Run: `.venv/bin/pytest tests/ -q`
```bash
git add fixtures/spy_wheel_cycle.parquet tests/engine_v2/options/test_wheel_integration.py
git commit -m "test: real SPY wheel-cycle golden integration"
```

---

## Self-Review

**Spec coverage:** WheelConfig/Trade/fills/underlying (Task 1); the two-phase state machine, assignment/called-away, take-profit, sizing, commission, MTM equity (Task 2); real-data integration (Task 3). No rolling/intraday/metrics/dashboard — correctly absent.

**Placeholder scan:** none. Task 3's pull window/strike-range are concrete with a size guard.

**Type consistency:** `WheelConfig`/`Trade`/`WheelResult` fields match their construction and the tests. `run_wheel` uses `select_strike_by_delta`/`option_mark`/`sell_proceeds`/`buy_cost`/`underlying_series` exactly as defined in Task 1 and sub-project 1. Trade `action` strings are consistent between the engine and the test assertions. `short` dict keys (`contract`, `contracts`, `credit`, `last_mid`) are set and read consistently.

**Risk:** Task 3 depends on the live terminal + a slow pull (kept tight). The engine's correctness is fully covered by the deterministic synthetic tests in Task 2; Task 3 is an end-to-end sanity check on real data, not the primary correctness proof.
