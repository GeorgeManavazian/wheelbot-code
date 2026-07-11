# Wheel Sub-project 5 — Reporting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. NOTE: owner wants a full audit + explicit approval after EVERY task — do NOT run tasks continuously.

**Goal:** Turn a `WheelResult` into a report — headline + recent + year-by-year metrics, buy-hold SPY benchmark, and wheel-specific stats — reusing `metrics_simple`. Plus a required engine fix (settle a residual open short).

**Architecture:** One engine fix in `wheel.py` (settle any short still open at the window end → honest `final_cash`). A new `src/engine_v2/options/report.py` computing metrics (via the frequency-aware `metrics_simple`), a SPY buy-hold benchmark from the chain's underlying, and trade-log stats, plus a text formatter. Data-agnostic: works on the 2-month fixture now, on the full multi-year pull later.

**Tech Stack:** Python 3.9, pandas, pytest.

## Global Constraints

- Reuse `src/engine_v2/backtest/metrics_simple.py`: `cagr(equity, ppy)`, `sharpe(returns, ppy)`, `max_drawdown(equity)`, `yearly_returns(equity)`, `yearly_sharpe(returns, ppy)`, `infer_periods_per_year(index)`.
- `report.py` imports `metrics_simple` + the wheel types (`WheelConfig`, `underlying_series`) + pandas. NOT the gate/`run_backtest`.
- Standing methodology: headline = recent window; keep full history; always year-by-year; never one blended number.
- No pass/fail verdict. No dashboard (out of scope). No new data pull.
- Every task ends green: `.venv/bin/pytest tests/ -q`.

---

### Task 1: Engine fix — settle a residual open short at window end

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (`WheelResult` gains `residual_settled`; `run_wheel` settles after the loop)
- Test: `tests/engine_v2/options/test_wheel_residual.py`

**Interfaces:** `WheelResult(equity, trades, final_cash, final_shares, residual_settled=False)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_wheel_residual.py
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]

def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"] = pd.to_datetime(ch["date"]); ch["expiry"] = pd.to_datetime(ch["expiry"])
    return ch

def test_residual_short_settled_at_window_end():
    # sell a put on d0; window ends d1 with the put STILL open (expiry is later, no TP)
    rows = [
        ["2024-01-02","2024-02-16",45,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-02-16",44,470,"P",1.50,1.60,1.55,1.55,-0.25,0.1,473.0],
    ]
    cfg = WheelConfig(starting_capital=50_000.0, dte_min=1, dte_max=60,
                      take_profit_pct=None, commission_per_contract=0.0)
    res = run_wheel(_chain(rows), cfg)
    assert res.residual_settled is True
    # settled to the last day's mid (1.55): cash = 50000 +200 (credit) -155 (buy back @mid) = 50045
    assert res.final_cash == pytest.approx(50_000 + 200 - 155)
    # final_cash + shares*spot reconciles to the last equity point
    assert res.equity.iloc[-1] == pytest.approx(res.final_cash + res.final_shares * 473.0)

def test_no_residual_when_flat_at_end():
    rows = [
        ["2024-01-02","2024-01-05",3,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-05","2024-01-05",0,470,"P",0.00,0.05,0.02,0.02,-0.01,0.1,475.0],
    ]
    cfg = WheelConfig(starting_capital=50_000.0, dte_min=1, dte_max=60,
                      take_profit_pct=None, commission_per_contract=0.0)
    res = run_wheel(_chain(rows), cfg)
    assert res.residual_settled is False
    assert res.final_cash == pytest.approx(50_200.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_residual.py -v`
Expected: FAIL — `residual_settled` absent.

- [ ] **Step 3: Edit `wheel.py`**

Add the field to `WheelResult`:
```python
@dataclass
class WheelResult:
    equity: pd.Series
    trades: list
    final_cash: float
    final_shares: int
    residual_settled: bool = False
```
At the END of `run_wheel`, after the daily loop and before `return`, settle any residual short to the last day's mid (synthetic close, no commission — this only reconciles the end state, it is not a real trade):
```python
    residual_settled = False
    if short is not None:
        last = pd.Timestamp(dates[-1])
        mk = option_mark(chain, last, short["contract"])
        mid = mk.mid if mk is not None else short["last_mid"]
        cash -= mid * mult * short["contracts"]
        residual_settled = True
    return WheelResult(pd.Series(equity), trades, cash, shares, residual_settled)
```
(Replace the existing `return WheelResult(...)`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_wheel_residual.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest tests/ -q` (the existing wheel tests still pass — flat-at-end runs are unaffected; the integration test's final_cash is unchanged because its last trade is a CLOSE, leaving it flat).
```bash
git add src/engine_v2/options/wheel.py tests/engine_v2/options/test_wheel_residual.py
git commit -m "fix: settle residual open short at window end (residual_settled flag)"
```

---

### Task 2: `wheel_report` — metrics, benchmark, stats

**Files:**
- Create: `src/engine_v2/options/report.py`
- Test: `tests/engine_v2/options/test_report.py`

**Interfaces produced:**
- `@dataclass WheelReport(metrics, recent, yearly_return, yearly_sharpe, benchmark, stats, periods_per_year)`.
- `spy_buy_hold(chain, starting_capital) -> pd.Series`.
- `wheel_stats(trades, config) -> dict`.
- `wheel_report(result, chain, config, recent_start="2021-07-01") -> WheelReport`.

- [ ] **Step 1: Write the failing test** (on the committed real fixture)

```python
# tests/engine_v2/options/test_report.py
import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import spy_buy_hold, wheel_stats, wheel_report

FIX = "fixtures/spy_wheel_cycle.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")

def _run():
    ch = pd.read_parquet(FIX)
    cfg = WheelConfig(dte_min=20, dte_max=45)
    return run_wheel(ch, cfg), ch, cfg

def test_spy_buy_hold_shape():
    _, ch, _ = _run()
    bh = spy_buy_hold(ch, 100_000)
    assert bh.iloc[0] == pytest.approx(100_000)          # starts at capital
    assert (bh > 0).all()

def test_wheel_stats_on_real_fixture():
    res, _, cfg = _run()
    s = wheel_stats(res.trades, cfg)
    assert s["n_puts_sold"] == 6 and s["n_take_profits"] == 6
    assert s["n_assignments"] == 0 and s["n_called_away"] == 0
    # 12 option legs x 2 contracts x $0.65
    assert s["commission_paid"] == pytest.approx(12 * 2 * 0.65)
    assert s["premium_collected"] > s["premium_paid_to_close"] > 0
    assert s["assignment_rate"] == 0.0

def test_wheel_report_metrics_finite_and_benchmarked():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg)
    for k in ("total_return", "cagr", "sharpe", "max_drawdown"):
        assert k in rep.metrics and pd.notna(rep.metrics[k])
    assert set(rep.benchmark) >= {"cagr", "sharpe", "max_drawdown"}
    assert 2024 in rep.yearly_return.index
    assert rep.periods_per_year == pytest.approx(252, abs=8)   # daily wheel equity
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_report.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `report.py`**

```python
# src/engine_v2/options/report.py
"""Reporting for the wheel backtest: headline + recent + year-by-year metrics,
a buy-hold SPY benchmark, and trade-log stats. Reuses the frequency-aware
metrics_simple. No verdict/gate — diagnostics only."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .wheel import underlying_series
from ..backtest import metrics_simple as m

@dataclass
class WheelReport:
    metrics: dict
    recent: dict
    yearly_return: pd.Series
    yearly_sharpe: pd.Series
    benchmark: dict
    stats: dict
    periods_per_year: float

def spy_buy_hold(chain, starting_capital) -> pd.Series:
    und = underlying_series(chain)
    return (starting_capital * und / und.iloc[0]).rename("spy_buy_hold")

def _perf(equity, ppy) -> dict:
    rets = equity.pct_change().fillna(0.0)
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else float("nan"),
        "cagr": m.cagr(equity, ppy),
        "sharpe": m.sharpe(rets, ppy),
        "max_drawdown": m.max_drawdown(equity),
    }

def wheel_stats(trades, cfg) -> dict:
    mult = cfg.contract_multiplier
    def of(a): return [t for t in trades if t.action == a]
    sells = of("SELL_PUT") + of("SELL_CALL")
    closes = of("CLOSE_PUT") + of("CLOSE_CALL")
    prem_in = sum(t.price_per_contract * mult * t.contracts for t in sells)
    prem_out = sum(t.price_per_contract * mult * t.contracts for t in closes)
    commission = cfg.commission_per_contract * sum(t.contracts for t in sells + closes)
    n_puts = len(of("SELL_PUT"))
    return {
        "n_puts_sold": n_puts, "n_calls_sold": len(of("SELL_CALL")),
        "n_assignments": len(of("ASSIGNED")), "n_called_away": len(of("CALLED_AWAY")),
        "n_take_profits": len(closes),
        "n_expired": len(of("PUT_EXPIRED")) + len(of("CALL_EXPIRED")),
        "premium_collected": prem_in, "premium_paid_to_close": prem_out,
        "commission_paid": commission,
        "net_premium": prem_in - prem_out - commission,
        "assignment_rate": (len(of("ASSIGNED")) / n_puts) if n_puts else 0.0,
    }

def wheel_report(result, chain, cfg, recent_start="2021-07-01") -> WheelReport:
    eq = result.equity
    ppy = m.infer_periods_per_year(eq.index)
    rs = max(pd.Timestamp(recent_start), eq.index.min())
    eq_recent = eq[eq.index >= rs]
    bh = spy_buy_hold(chain, cfg.starting_capital).reindex(eq.index).ffill()
    return WheelReport(
        metrics=_perf(eq, ppy),
        recent=_perf(eq_recent, ppy) if len(eq_recent) > 1 else _perf(eq, ppy),
        yearly_return=m.yearly_returns(eq),
        yearly_sharpe=m.yearly_sharpe(eq.pct_change().fillna(0.0), ppy),
        benchmark=_perf(bh, ppy),
        stats=wheel_stats(result.trades, cfg),
        periods_per_year=ppy,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_report.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/test_report.py
git commit -m "feat: wheel_report — metrics, SPY buy-hold benchmark, trade stats"
```

---

### Task 3: `format_report` — printable text

**Files:**
- Modify: `src/engine_v2/options/report.py` (add `format_report`)
- Test: `tests/engine_v2/options/test_report_format.py`

**Interfaces produced:** `format_report(report) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_report_format.py
import os
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.report import wheel_report, format_report

FIX = "fixtures/spy_wheel_cycle.parquet"

@pytest.mark.skipif(not os.path.exists(FIX), reason="wheel cycle fixture not built")
def test_format_report_has_sections():
    ch = pd.read_parquet(FIX)
    cfg = WheelConfig(dte_min=20, dte_max=45)
    rep = wheel_report(run_wheel(ch, cfg), ch, cfg)
    txt = format_report(rep)
    assert isinstance(txt, str)
    for token in ("CAGR", "Sharpe", "Max drawdown", "buy-hold", "Year", "assignment"):
        assert token.lower() in txt.lower()
    assert "2024" in txt          # year-by-year row present
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_report_format.py -v`
Expected: FAIL — `format_report` missing.

- [ ] **Step 3: Add `format_report` to `report.py`**

```python
def _pct(x): return "n/a" if pd.isna(x) else f"{x:+.2%}"
def _num(x): return "n/a" if pd.isna(x) else f"{x:.2f}"

def format_report(rep) -> str:
    L = []
    L.append("WHEEL BACKTEST REPORT")
    L.append("=" * 40)
    def block(title, d):
        L.append(f"\n{title}")
        L.append(f"  CAGR {_pct(d['cagr'])}   Sharpe {_num(d['sharpe'])}   "
                 f"Max drawdown {_pct(d['max_drawdown'])}   Total {_pct(d['total_return'])}")
    block("Headline (recent window)", rep.recent)
    block("Full history", rep.metrics)
    block("Benchmark — SPY buy-hold", rep.benchmark)
    L.append("\nYear-by-year (return / Sharpe)")
    for y in rep.yearly_return.index:
        L.append(f"  {y}: {_pct(rep.yearly_return[y])}  /  Sharpe {_num(rep.yearly_sharpe.get(y, float('nan')))}")
    s = rep.stats
    L.append("\nWheel stats")
    L.append(f"  puts sold {s['n_puts_sold']}  calls sold {s['n_calls_sold']}  "
             f"assignments {s['n_assignments']} (rate {s['assignment_rate']:.0%})  "
             f"called away {s['n_called_away']}  take-profits {s['n_take_profits']}")
    L.append(f"  premium collected {s['premium_collected']:.0f}  "
             f"paid to close {s['premium_paid_to_close']:.0f}  "
             f"commission {s['commission_paid']:.0f}  net {s['net_premium']:.0f}")
    return "\n".join(L)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_report_format.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest tests/ -q`
```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/test_report_format.py
git commit -m "feat: format_report — printable wheel report"
```

---

## Self-Review

**Spec coverage:** residual-short settlement fix (Task 1); metrics/recent/yearly/benchmark/stats (Task 2); printable formatter (Task 3). Dashboard correctly out of scope. Standing methodology (recent headline + year-by-year) honored in `wheel_report`/`format_report`.

**Placeholder scan:** none. Metric values on the short fixture are asserted finite (not pinned to specific numbers, since a 2-month span makes them illustrative); structural stats (counts, commission) ARE pinned.

**Type consistency:** `WheelResult.residual_settled` (Task 1) is read nowhere in Tasks 2–3 (report uses `equity`/`trades`), so no coupling risk. `WheelReport` fields match `format_report`'s reads. `wheel_stats`/`_perf`/`spy_buy_hold` names match their call sites. `metrics_simple` functions used exactly as defined on main.

**Risk:** low — all data-agnostic and tested on the committed fixture; independent of the in-progress bulk pull. When the full history lands, `wheel_report` runs on it unchanged.
