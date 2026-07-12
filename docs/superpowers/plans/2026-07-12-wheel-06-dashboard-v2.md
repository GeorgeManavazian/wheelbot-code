# Wheel Sub-project 6 — Dashboard v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Add a date-range picker, a position-level trade blotter (with per-trade realized P&L), and a persisted run-history tab (with load-config-back) to the Wheel dashboard.

**Architecture:** `position_log` in `report.py` (engine-layer, reconciled to total P&L); a CSV persistence helper `dashboard/wheel_history.py`; Wheel-page changes (date range + blotter + logging); a new History tab.

**Tech Stack:** Python 3.9, pandas, Streamlit, pytest.

## Global Constraints
- No change to `run_wheel` behavior.
- `position_log`'s realized P&L must **reconcile**: `sum(realized_pnl of closed rows) == final_cash − starting_capital` when the run ends flat.
- History = a gitignored CSV `data/wheel_runs.csv`. Load-config repopulates the form, does NOT auto-run.
- Every task ends green: `.venv/bin/pytest tests/ -q`.

---

### Task 1: `position_log` — the trade blotter

**Files:** Modify `src/engine_v2/options/report.py`; Test `tests/engine_v2/options/test_position_log.py`.

- [ ] **Step 1: failing test**
```python
# tests/engine_v2/options/test_position_log.py
import os, pandas as pd, pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel, Trade
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.report import position_log

def _t(date, action, strike, right, n, price):
    return Trade(pd.Timestamp(date), action, Contract("SPY", pd.Timestamp("2024-02-16"), strike, right), n, price, 0.0)

def test_blotter_outcomes_and_pnl():
    from src.engine_v2.options.wheel import WheelResult
    trades = [
        _t("2024-01-02","SELL_PUT",470,"P",1,2.00),   # credit 200
        _t("2024-01-05","CLOSE_PUT",470,"P",1,0.90),   # cost 90 -> pnl 110, Took profit
        _t("2024-01-08","SELL_PUT",460,"P",1,1.50),    # credit 150
        _t("2024-02-16","ASSIGNED",460,"P",1,460),     # pnl 150 kept, Assigned; shares in @460
        _t("2024-02-16","SELL_CALL",470,"C",1,3.00),   # credit 300
        _t("2024-03-15","CALLED_AWAY",470,"C",1,470),  # call pnl 300; shares sold @470 -> share pnl (470-460)*100 = 1000
    ]
    res = WheelResult(pd.Series(dtype=float), trades, 0.0, 0)
    df = position_log(res, WheelConfig(commission_per_contract=0.0))
    puts = df[df.instrument=="PUT"]
    assert (puts.outcome.tolist()) == ["Took profit","Assigned"]
    assert puts.iloc[0].realized_pnl == pytest.approx(110)
    assert puts.iloc[1].realized_pnl == pytest.approx(150)
    call = df[df.instrument=="CALL"].iloc[0]
    assert call.outcome == "Called away" and call.realized_pnl == pytest.approx(300)
    sh = df[df.instrument=="SHARES"].iloc[0]
    assert sh.realized_pnl == pytest.approx(1000) and sh.outcome == "Called away"

def test_blotter_reconciles_to_total():
    FIX = "fixtures/spy_wheel_cycle.parquet"
    if not os.path.exists(FIX): pytest.skip("fixture missing")
    ch = pd.read_parquet(FIX); cfg = WheelConfig(dte_min=20, dte_max=45)
    res = run_wheel(ch, cfg); df = position_log(res, cfg)
    total = res.final_cash - cfg.starting_capital
    assert df["realized_pnl"].sum() == pytest.approx(total, abs=0.01)
```

- [ ] **Step 2: run → FAIL.**

- [ ] **Step 3: add to `report.py`**
```python
_RIGHT_WORD = {"P": "PUT", "C": "CALL"}
_TERM = {"CLOSE_PUT", "CLOSE_CALL", "PUT_EXPIRED", "CALL_EXPIRED", "ASSIGNED", "CALLED_AWAY"}

def position_log(result, cfg) -> pd.DataFrame:
    mult, comm = cfg.contract_multiplier, cfg.commission_per_contract
    rows, open_opt, assign = [], None, None
    for t in result.trades:
        if t.action in ("SELL_PUT", "SELL_CALL"):
            open_opt = t
        elif t.action in _TERM and open_opt is not None:
            oc, n = open_opt.contract, open_opt.contracts
            credit = open_opt.price_per_contract * mult * n - comm * n
            if t.action in ("CLOSE_PUT", "CLOSE_CALL"):
                cost, outcome = t.price_per_contract * mult * n + comm * n, "Took profit"
            elif t.action in ("PUT_EXPIRED", "CALL_EXPIRED"):
                cost, outcome = 0.0, "Expired worthless"
            elif t.action == "ASSIGNED":
                cost, outcome = 0.0, "Assigned"
                assign = (oc.strike, n, t.date)
            else:  # CALLED_AWAY
                cost, outcome = 0.0, "Called away"
            realized = credit - cost
            rows.append(dict(opened=open_opt.date, closed=t.date,
                instrument=_RIGHT_WORD[oc.right], strike=oc.strike, expiry=oc.expiry,
                qty=n, credit=credit, outcome=outcome, cost_to_close=cost,
                realized_pnl=realized,
                pct_of_credit=(realized / credit if credit else 0.0),
                days_held=(t.date - open_opt.date).days))
            if t.action == "CALLED_AWAY" and assign is not None:
                astrike, aqty, adate = assign
                rows.append(dict(opened=adate, closed=t.date, instrument="SHARES",
                    strike=astrike, expiry=pd.NaT, qty=aqty,
                    credit=-astrike * mult * aqty, outcome="Called away",
                    cost_to_close=oc.strike * mult * aqty,
                    realized_pnl=(oc.strike - astrike) * mult * aqty,
                    pct_of_credit=float("nan"), days_held=(t.date - adate).days))
                assign = None
            open_opt = None
    if assign is not None and getattr(result, "final_shares", 0) > 0:
        astrike, aqty, adate = assign
        rows.append(dict(opened=adate, closed=pd.NaT, instrument="SHARES",
            strike=astrike, expiry=pd.NaT, qty=aqty, credit=-astrike * mult * aqty,
            outcome="Open", cost_to_close=float("nan"), realized_pnl=float("nan"),
            pct_of_credit=float("nan"), days_held=float("nan")))
    cols = ["opened","closed","instrument","strike","expiry","qty","credit","outcome",
            "cost_to_close","realized_pnl","pct_of_credit","days_held"]
    return pd.DataFrame(rows, columns=cols)
```

- [ ] **Step 4: run → PASS; full suite green.**
- [ ] **Step 5: commit** `feat: position_log trade blotter (per-trade realized P&L, reconciled)`.

---

### Task 2: run-history persistence

**Files:** Create `dashboard/wheel_history.py`; Modify `.gitignore` (add `data/wheel_runs.csv`); Test `tests/test_wheel_history.py`.

- [ ] **Step 1: failing test**
```python
# tests/test_wheel_history.py
import pandas as pd
from dashboard import wheel_history as wh

def test_log_load_clear(tmp_path, monkeypatch):
    p = tmp_path / "runs.csv"
    monkeypatch.setattr(wh, "RUNS_PATH", str(p))
    assert wh.load_runs().empty
    wh.log_run({"ts": "2026-07-12T10:00", "pnl": 100.0, "sharpe": 0.5})
    wh.log_run({"ts": "2026-07-12T11:00", "pnl": 200.0, "sharpe": 0.6})
    df = wh.load_runs()
    assert len(df) == 2
    assert df.iloc[0]["ts"] == "2026-07-12T11:00"   # newest first
    wh.clear_runs()
    assert wh.load_runs().empty
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: write `dashboard/wheel_history.py`**
```python
"""Persist wheel backtest runs to a gitignored CSV for the History tab."""
import os
import pandas as pd

RUNS_PATH = "data/wheel_runs.csv"

def log_run(record: dict) -> None:
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    df = pd.DataFrame([record])
    header = not os.path.exists(RUNS_PATH)
    df.to_csv(RUNS_PATH, mode="a", header=header, index=False)

def load_runs() -> pd.DataFrame:
    if not os.path.exists(RUNS_PATH):
        return pd.DataFrame()
    return pd.read_csv(RUNS_PATH).iloc[::-1].reset_index(drop=True)  # newest first

def clear_runs() -> None:
    if os.path.exists(RUNS_PATH):
        os.remove(RUNS_PATH)
```
Add `data/wheel_runs.csv` to `.gitignore`.

- [ ] **Step 4: run → PASS; full suite green.**
- [ ] **Step 5: commit** `feat: wheel run-history persistence (csv)`.

---

### Task 3: Wheel page — date range + blotter + logging

**Files:** Modify `dashboard/views/wheel.py`; Test add to `tests/test_wheel_dashboard.py`.

- [ ] **Step 1: failing test** — assert the Wheel page has `wheel_start`/`wheel_end` date inputs and, after Run, a dataframe is shown.
```python
def test_wheel_page_has_date_inputs():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    assert any(di.key == "wheel_start" for di in at.date_input)
    assert any(di.key == "wheel_end" for di in at.date_input)
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: edit `dashboard/views/wheel.py`**
  - After loading `ch`, add date inputs bounded by `ch.date.min()/max()` (keys `wheel_start`, `wheel_end`; defaults = full span, but if `st.session_state` has loaded-config keys, use them).
  - On Run: `ch = ch[(ch["date"] >= pd.Timestamp(start)) & (ch["date"] <= pd.Timestamp(end))]`; if `ch["date"].nunique() < 5`: `st.warning("window too short"); st.stop()`.
  - Replace the raw trade-log dataframe with `position_log(res, cfg)`, formatted (dates, $ credit/realized, % ). Keep the existing metrics/charts.
  - After rendering, `from dashboard.wheel_history import log_run; log_run({...})` with config + window + results (`rep.metrics['total_return']`, `max_drawdown`, `sharpe`, `pnl`, `len(res.trades)`, `rep.stats['n_assignments']`, source name, deltas, dtes, tp, capital, intraday flag, ts via `pd.Timestamp.now().isoformat()`).
  - Read loaded-config from `st.session_state` for the strategy/delta/dte/tp widgets' defaults (so History's "load config" works).

- [ ] **Step 4: run tests → PASS; full suite green.**
- [ ] **Step 5: commit** `feat: Wheel page date range + trade blotter + run logging`.

---

### Task 4: History tab + load-config

**Files:** Create `dashboard/views/wheel_history.py`; Modify `dashboard/app.py`; Test add to `tests/test_wheel_dashboard.py`.

- [ ] **Step 1: failing test** — History page renders; if runs exist, shows a table; a "clear" button exists.
```python
def test_history_page_renders():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel_history.py").run(timeout=60)
    assert not at.exception
    assert any(b.key == "clear_history" for b in at.button)
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: write `dashboard/views/wheel_history.py`**
```python
import pandas as pd
import streamlit as st
from dashboard.wheel_history import load_runs, clear_runs

_CFG_KEYS = ["put_delta","call_delta","dte_min","dte_max","take_profit","capital","data_source"]

def render():
    st.title("Wheel — run history")
    runs = load_runs()
    if runs.empty:
        st.info("No runs yet. Run a wheel backtest and it'll show up here.")
        return
    st.dataframe(runs, use_container_width=True)
    c1, c2 = st.columns(2)
    if c1.button("Clear history", key="clear_history"):
        clear_runs(); st.rerun()
    idx = c2.selectbox("Load a run's config", runs.index,
                       format_func=lambda i: f"{runs.loc[i,'ts']}  ·  {runs.loc[i,'pnl']}", key="hist_pick")
    if c2.button("Load config into form", key="load_cfg"):
        row = runs.loc[idx]
        st.session_state["_load_cfg"] = {k: row[k] for k in _CFG_KEYS if k in row}
        st.success("Config staged — open the Wheel tab.")
```
Wire `dashboard/app.py`: add `st.Page(wheel_history.render, title="History", url_path="history")` after the Wheel page.
In `wheel.py`, at the top of `render()`, if `st.session_state.get("_load_cfg")`, use those values as widget defaults (pop it after applying once).

- [ ] **Step 4: run tests → PASS; full suite green.**
- [ ] **Step 5: commit** `feat: History tab + load-config-back`.

---

## Self-Review
Coverage: blotter+reconciliation (T1), persistence (T2), date range + blotter display + logging (T3), History tab + load-config (T4). No `run_wheel` behavior change. Types: `position_log` columns consistent; history record keys match what the History tab reads for load-config. Risk: Streamlit session-state load-config timing — apply loaded values as widget *defaults* before the widgets are created, pop once to avoid sticking.
