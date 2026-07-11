# Wheel Sub-project 4 — Intraday take-profit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. (Owner granted autonomous completion for this sub-project — execute continuously; still review each task.)

**Goal:** Refine take-profit *timing* with hourly intraday marks: TP fires at the first intraday bar that crosses the threshold, not just at EOD. Two-pass (EOD → pull held-contract intraday → re-run). Backward-compatible.

**Architecture:** `src/engine_v2/options/intraday.py` (puller + `held_contracts` + `run_wheel_intraday`); an optional `intraday=` param on `run_wheel`; a dashboard toggle. Intraday data is hourly OHLC (trade close), so TP fills at the bar close.

**Tech Stack:** Python 3.9, pandas, pytest, Streamlit.

## Global Constraints

- Intraday marks are keyed `(expiry: Timestamp, strike: float, right: "P"/"C") -> DataFrame[timestamp(tz-naive ET), close]`.
- `run_wheel(chain, cfg, intraday=None)`: `intraday=None` → byte-identical to current behavior. Do NOT regress any existing wheel test.
- Intraday TP: first bar with `close <= (1-tp)*credit` on the held day → fill at that `close` + `commission_per_contract*n`, `Trade` stamped with the bar timestamp; else fall through to the existing EOD ask check.
- Endpoint: `/v3/option/history/ohlc?symbol&expiration&start_date&end_date&interval=1h&strike_range=N` (whole-chain; single-strike returns 472 on STANDARD). Multi-day capped ~1 month.
- Imports: intraday.py uses `theta_client`, `chain` (Contract), `wheel`, pandas. NOT the gate.
- Every task ends green: `.venv/bin/pytest tests/ -q`.

## Real OHLC response (verified live)
`symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,vwap` — e.g. `"SPY","2024-01-19",477.000,"CALL","2024-01-16T09:30:00.000",1.74,1.98,1.10,1.84,8270,1044,1.45`. Timestamps are ET (treat tz-naive).

---

### Task 1: intraday puller + `held_contracts` + `intraday_marks`

**Files:** Create `src/engine_v2/options/intraday.py`; Test `tests/engine_v2/options/test_intraday_pull.py`.

**Interfaces produced:**
- `pull_option_intraday(client, symbol, expiration, start, end, interval="1h", strike_range=10) -> pd.DataFrame` (tidy: `timestamp, expiry, strike, right, close, high, low, volume`). Chunks windows > 25 days into ≤25-day calls. Skips 472.
- `intraday_marks(df) -> dict[(Timestamp, float, str), pd.DataFrame]` (key = (expiry, strike, right); value = `timestamp, close` sorted).
- `held_contracts(result) -> list[tuple]` — from a `WheelResult` trade log, each short position → `(expiry, strike, right, open_date, close_date)`.

- [ ] **Step 1: failing test**

```python
# tests/engine_v2/options/test_intraday_pull.py
import os, pandas as pd, pytest
from src.engine_v2.options.intraday import held_contracts, intraday_marks
from src.engine_v2.options.wheel import WheelResult
from src.engine_v2.options.chain import Contract

def _trade(date, action, strike, right):
    from src.engine_v2.options.wheel import Trade
    return Trade(pd.Timestamp(date), action, Contract("SPY", pd.Timestamp("2024-02-16"), strike, right), 1, 1.0, 0.0)

def test_held_contracts_from_trades():
    trades = [_trade("2024-01-05","SELL_PUT",470,"P"),
              _trade("2024-01-12","CLOSE_PUT",470,"P"),
              _trade("2024-01-12","SELL_PUT",475,"P"),
              _trade("2024-01-19","PUT_EXPIRED",475,"P")]
    res = WheelResult(pd.Series(dtype=float), trades, 0.0, 0)
    held = held_contracts(res)
    assert (pd.Timestamp("2024-02-16"), 470.0, "P", pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-12")) in held
    assert any(h[1] == 475.0 and h[4] == pd.Timestamp("2024-01-19") for h in held)

def test_intraday_marks_keys():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-16 09:30","2024-01-16 10:30"]),
        "expiry": pd.to_datetime(["2024-01-19","2024-01-19"]),
        "strike": [470.0,470.0], "right":["P","P"], "close":[2.0,1.5],
        "high":[2.1,1.6],"low":[1.9,1.4],"volume":[10,20]})
    m = intraday_marks(df)
    k = (pd.Timestamp("2024-01-19"), 470.0, "P")
    assert k in m and list(m[k]["close"]) == [2.0, 1.5]
```

- [ ] **Step 2: run → FAIL** (`.venv/bin/pytest tests/engine_v2/options/test_intraday_pull.py -v`).

- [ ] **Step 3: write `intraday.py`**

```python
# src/engine_v2/options/intraday.py
"""Intraday (hourly) option marks for take-profit timing. Pulls whole-chain OHLC
(trade prices; single-strike quotes aren't available on STANDARD) and indexes by
contract. Also the two-pass orchestration. Isolated from the gate."""
from __future__ import annotations
import pandas as pd
from .theta_client import ThetaError

_RIGHT = {"CALL": "C", "PUT": "P", "C": "C", "P": "P"}

def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    out = pd.DataFrame({
        "timestamp": pd.to_datetime(df["timestamp"]).dt.tz_localize(None),
        "expiry": pd.to_datetime(df["expiration"]).dt.normalize(),
        "strike": df["strike"].astype(float),
        "right": df["right"].map(_RIGHT),
        "close": df["close"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "volume": df["volume"].astype(float),
    })
    return out.dropna(subset=["close"]).sort_values(["expiry","strike","right","timestamp"]).reset_index(drop=True)

def pull_option_intraday(client, symbol, expiration, start, end,
                         interval="1h", strike_range=10) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frames = []
    lo = start
    while lo <= end:
        hi = min(lo + pd.Timedelta(days=25), end)
        try:
            raw = client.get_csv("/v3/option/history/ohlc", symbol=symbol,
                                 expiration=pd.Timestamp(expiration).strftime("%Y-%m-%d"),
                                 start_date=lo.strftime("%Y-%m-%d"),
                                 end_date=hi.strftime("%Y-%m-%d"),
                                 interval=interval, strike_range=strike_range)
            if len(raw):
                frames.append(_normalize(raw))
        except ThetaError as e:
            if e.code != 472:
                raise
        lo = hi + pd.Timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=["timestamp","expiry","strike","right","close","high","low","volume"])
    return pd.concat(frames, ignore_index=True)

def intraday_marks(df: pd.DataFrame) -> dict:
    out = {}
    for (exp, strike, right), g in df.groupby(["expiry","strike","right"]):
        out[(pd.Timestamp(exp), float(strike), right)] = \
            g[["timestamp","close"]].sort_values("timestamp").reset_index(drop=True)
    return out

def held_contracts(result) -> list:
    """Each short position -> (expiry, strike, right, open_date, close_date)."""
    OPEN = {"SELL_PUT", "SELL_CALL"}
    CLOSE = {"CLOSE_PUT", "CLOSE_CALL", "ASSIGNED", "PUT_EXPIRED", "CALLED_AWAY", "CALL_EXPIRED"}
    held, cur = [], None
    for t in result.trades:
        c = t.contract
        if t.action in OPEN:
            cur = (pd.Timestamp(c.expiry), float(c.strike), c.right, pd.Timestamp(t.date))
        elif t.action in CLOSE and cur is not None:
            held.append((cur[0], cur[1], cur[2], cur[3], pd.Timestamp(t.date)))
            cur = None
    if cur is not None:  # still open at window end
        held.append((cur[0], cur[1], cur[2], cur[3], cur[3]))
    return held
```

- [ ] **Step 4: run → PASS.** Then **live smoke** (terminal up): pull one real chain window and confirm hourly bars:
`.venv/bin/python -c "from src.engine_v2.options.theta_client import ThetaClient as C; from src.engine_v2.options.intraday import pull_option_intraday; d=pull_option_intraday(C(timeout=120),'SPY','2024-01-19','2024-01-16','2024-01-16'); print(d.shape); print(d.head())"` → expect rows with hourly timestamps. (If terminal busy/down, note and proceed — unit tests don't need it.)

- [ ] **Step 5: full suite + commit.**
```bash
git add src/engine_v2/options/intraday.py tests/engine_v2/options/test_intraday_pull.py
git commit -m "feat: intraday option puller (hourly OHLC) + held_contracts + intraday_marks"
```

---

### Task 2: intraday-TP in `run_wheel`

**Files:** Modify `src/engine_v2/options/wheel.py`; Test `tests/engine_v2/options/test_wheel_intraday_tp.py`.

**Interfaces:** `run_wheel(chain, cfg, intraday=None)`.

- [ ] **Step 1: failing test**

```python
# tests/engine_v2/options/test_wheel_intraday_tp.py
import pandas as pd, pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]
def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"]=pd.to_datetime(ch["date"]); ch["expiry"]=pd.to_datetime(ch["expiry"]); return ch

CFG = dict(starting_capital=50_000.0, dte_min=1, dte_max=60, take_profit_pct=0.50, commission_per_contract=0.0)

def test_intraday_tp_fires_before_eod():
    # sell 470 put @2.00 credit on d0; on d1 EOD ask is 1.80 (NO EOD TP: >1.00), but
    # intraday close dips to 0.90 at 11:30 -> intraday TP fires there at 0.90.
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",1.75,1.80,1.775,1.775,-0.25,0.1,473.0],
    ]
    intraday = {(pd.Timestamp("2024-01-19"),470.0,"P"): pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-03 10:30","2024-01-03 11:30","2024-01-03 12:30"]),
        "close": [1.60, 0.90, 1.10]})}
    res = run_wheel(_chain(rows), WheelConfig(**CFG), intraday=intraday)
    acts = [t.action for t in res.trades]
    assert acts[:2] == ["SELL_PUT","CLOSE_PUT"]
    close_tr = [t for t in res.trades if t.action=="CLOSE_PUT"][0]
    assert close_tr.price_per_contract == pytest.approx(0.90)          # filled at intraday close
    assert close_tr.date == pd.Timestamp("2024-01-03 11:30")           # stamped intraday
    assert res.final_cash == pytest.approx(50_000 + 200 - 90)          # +credit -90 buyback

def test_intraday_none_matches_eod():
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",0.80,0.90,0.85,0.85,-0.15,0.1,476.0],
    ]
    ch = _chain(rows)
    a = run_wheel(ch, WheelConfig(**CFG))
    b = run_wheel(ch, WheelConfig(**CFG), intraday=None)
    assert [t.action for t in a.trades] == [t.action for t in b.trades]
    assert a.final_cash == b.final_cash
```

- [ ] **Step 2: run → FAIL** (`run_wheel` has no `intraday` param).

- [ ] **Step 3: edit `run_wheel`** — add `intraday=None`; replace the existing take-profit block (the `if (cfg.take_profit_pct is not None and mark is not None and d < c.expiry ...)` section) with an intraday-first version:

```python
def run_wheel(chain: pd.DataFrame, cfg: WheelConfig, intraday=None) -> WheelResult:
    ...
        if short is not None:
            c = short["contract"]; n = short["contracts"]
            mark = option_mark(chain, d, c)
            tp_fired = False
            if cfg.take_profit_pct is not None and d < c.expiry:
                thresh = (1 - cfg.take_profit_pct) * short["credit"]
                key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
                if intraday is not None and key in intraday:
                    bars = intraday[key]
                    day = bars[bars["timestamp"].dt.normalize() == d].sort_values("timestamp")
                    for _, bar in day.iterrows():
                        if bar["close"] <= thresh:
                            cash -= bar["close"] * mult * n + cfg.commission_per_contract * n
                            trades.append(Trade(bar["timestamp"],
                                "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                c, n, float(bar["close"]), cash))
                            short = None; closed_today = c; tp_fired = True
                            break
                if not tp_fired and short is not None and mark is not None and mark.ask <= thresh:
                    cash -= buy_cost(mark, n, cfg)
                    trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                        c, n, mark.ask, cash))
                    short = None; closed_today = c
            # expiry resolution (unchanged) ...
```
Keep the expiry block and everything else exactly as-is. (The `closed_today`/policy-B logic already exists; intraday close sets it too.)

- [ ] **Step 4: run → PASS** (2). Then `.venv/bin/pytest tests/ -q` — the FULL existing wheel suite must stay green (intraday=None path unchanged).

- [ ] **Step 5: commit.**
```bash
git add src/engine_v2/options/wheel.py tests/engine_v2/options/test_wheel_intraday_tp.py
git commit -m "feat: intraday take-profit resolution in run_wheel (backward-compatible)"
```

---

### Task 3: `run_wheel_intraday` two-pass + committed fixture

**Files:** Modify `src/engine_v2/options/intraday.py` (add `run_wheel_intraday`); Create committed `fixtures/spy_wheel_intraday_sample.parquet`; Test `tests/engine_v2/options/test_intraday_twopass.py`.

**Interfaces:** `run_wheel_intraday(chain, cfg, intraday_df) -> WheelResult` (offline form: takes a pre-built intraday tidy frame; builds the dict + runs pass 2). A live form is out of scope for tests (documented).

- [ ] **Step 1: failing test** (synthetic offline two-pass)

```python
# tests/engine_v2/options/test_intraday_twopass.py
import pandas as pd, pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.intraday import run_wheel_intraday

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]
def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"]=pd.to_datetime(ch["date"]); ch["expiry"]=pd.to_datetime(ch["expiry"]); return ch

def test_two_pass_intraday_changes_tp():
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",1.75,1.80,1.775,1.775,-0.25,0.1,473.0],
    ]
    ch = _chain(rows)
    intraday_df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-03 11:30"]),
        "expiry": pd.to_datetime(["2024-01-19"]), "strike":[470.0], "right":["P"],
        "close":[0.90], "high":[1.0], "low":[0.85], "volume":[10]})
    cfg = WheelConfig(starting_capital=50_000.0, dte_min=1, dte_max=60,
                      take_profit_pct=0.50, commission_per_contract=0.0)
    eod = run_wheel(ch, cfg)
    intr = run_wheel_intraday(ch, cfg, intraday_df)
    # EOD never TP's here (ask 1.80 > 1.00); intraday does, at 0.90
    assert "CLOSE_PUT" not in [t.action for t in eod.trades]
    assert "CLOSE_PUT" in [t.action for t in intr.trades]
```

- [ ] **Step 2: run → FAIL.**

- [ ] **Step 3: add to `intraday.py`**
```python
def run_wheel_intraday(chain, cfg, intraday_df):
    from .wheel import run_wheel
    return run_wheel(chain, cfg, intraday=intraday_marks(intraday_df))
```

- [ ] **Step 4: run → PASS.** Build the committed sample fixture from the real terminal (terminal up): pull hourly OHLC for the held contracts of the EOD wheel on `fixtures/spy_wheel_cycle.parquet`, save a SMALL slice. If the terminal is saturated by the bulk pull or down, SKIP building the committed fixture and note it (the two-pass test above is synthetic and self-sufficient); leave `run_wheel_intraday` covered by the synthetic test only.
```python
# best-effort fixture build (only if terminal free):
.venv/bin/python -c "
import pandas as pd
from src.engine_v2.options.theta_client import ThetaClient
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.intraday import pull_option_intraday, held_contracts
ch=pd.read_parquet('fixtures/spy_wheel_cycle.parquet'); cfg=WheelConfig(dte_min=20,dte_max=45)
res=run_wheel(ch,cfg); c=ThetaClient(timeout=180)
h=held_contracts(res)[0]
df=pull_option_intraday(c,'SPY',h[0],h[3],h[4],interval='1h',strike_range=5)
df.to_parquet('fixtures/spy_wheel_intraday_sample.parquet'); print(df.shape)
"
```

- [ ] **Step 5: full suite + commit** (fixture only if built).
```bash
git add src/engine_v2/options/intraday.py tests/engine_v2/options/test_intraday_twopass.py
git add fixtures/spy_wheel_intraday_sample.parquet 2>/dev/null || true
git commit -m "feat: run_wheel_intraday two-pass orchestration"
```

---

### Task 4: dashboard intraday toggle

**Files:** Modify `dashboard/views/wheel.py`; Test add to `tests/test_wheel_dashboard.py`.

- [ ] **Step 1: failing test** — add a case asserting an `intraday_tp` checkbox exists on the Wheel page.

```python
# add to tests/test_wheel_dashboard.py
def test_wheel_page_has_intraday_toggle():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    assert any(cb.key == "intraday_tp" for cb in at.checkbox)
```

- [ ] **Step 2: run → FAIL.**

- [ ] **Step 3: edit `dashboard/views/wheel.py`** — add a checkbox and use the two-pass when on + a sample exists:
```python
    import os as _os
    intraday_on = st.checkbox("Intraday take-profit (hourly)", key="intraday_tp")
    INTRA = "fixtures/spy_wheel_intraday_sample.parquet"
    ...
    if st.button("Run", key="run_wheel", type="primary"):
        cfg = WheelConfig(...)
        if intraday_on and _os.path.exists(INTRA):
            from src.engine_v2.options.intraday import run_wheel_intraday
            res = run_wheel_intraday(ch, cfg, pd.read_parquet(INTRA))
        else:
            res = run_wheel(ch, cfg)
            if intraday_on:
                st.info("No intraday sample for this dataset — ran EOD.")
        rep = wheel_report(res, ch, cfg)
        ...
```
(Keep the rest of the page unchanged.)

- [ ] **Step 4: run the dashboard tests** (`.venv/bin/pytest tests/test_wheel_dashboard.py -v`) → PASS. Then full suite.

- [ ] **Step 5: commit.**
```bash
git add dashboard/views/wheel.py tests/test_wheel_dashboard.py
git commit -m "feat: dashboard intraday take-profit toggle"
```

---

## Self-Review
Coverage: puller + held_contracts + marks (T1); intraday-TP engine, backward-compatible (T2); two-pass (T3); dashboard toggle (T4). Non-goals (intraday entries/assignment, full-history pull) absent. Types: `(expiry, strike, right)` key consistent across intraday_marks/run_wheel/held_contracts; `Trade`/`WheelResult`/`WheelConfig` used as defined. Risk: intraday fill uses trade `close` not ask (documented simplification — no intraday quotes on STANDARD); live fixture build is best-effort (synthetic tests are self-sufficient).
