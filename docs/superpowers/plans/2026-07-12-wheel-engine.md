# Wheel Engine Multi-Ticker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic expiry-first contract selection, per-ticker support, honest benchmarks, cash-yield knob, and flat-day accounting — per `docs/superpowers/specs/2026-07-12-wheel-multi-ticker-design.md` and the binding API contract `2026-07-12-wheel-engine-api-contract.md`.

**Architecture:** Three-layer change moving outward: `select.py` (new two-stage selector), `wheel.py` (config + loop), `report.py` (benchmarks + stats). Plus a small new `data.py` for ticker discovery. Dashboard files are **out of scope** (owned by the other agent; see contract).

**Tech Stack:** pandas, pytest. Fixtures: `fixtures/spy_options_small.parquet`, `fixtures/spy_wheel_cycle.parquet`.

## Global Constraints

- **Never touch `dashboard/**`** — binding ownership seam in the API contract.
- Baseline: 204 tests passing (1 known flake in `tests/test_workbench_dashboard.py` — pre-existing, not ours, do not chase it). Keep every non-flake test green.
- `dte` is **calendar days** (`chain.py:31`).
- Band rule, exact: `floor = max(5, target_dte - 2)`, `ceiling = target_dte + 3`.
- Selection uses only expiries **visible on the decision date** — never the full expiry history (look-ahead).
- New `WheelConfig` defaults: `put_delta=0.20, call_delta=0.20, target_dte=7, cash_yield=0.0, ticker="SPY"`. `dte_min`/`dte_max` **deleted**.
- Commit after every task with the message given in the task.

---

### Task 1: New selector — `derived_band` + `select_contract`

**Files:**
- Modify: `src/engine_v2/options/select.py`
- Test: `tests/engine_v2/options/test_select.py`

**Interfaces:**
- Consumes: `Contract` from `.chain` (unchanged).
- Produces: `derived_band(target_dte: int) -> tuple[int, int]`; `select_contract(chain, date, right, target_delta, target_dte, root) -> Contract | None`. `select_strike_by_delta` is **deleted** (Task 2 removes its last caller; within this task, update `test_select.py` fully).
- Keep `option_mark`, `intrinsic_value`, `expiry_underlying` unchanged.

- [ ] **Step 1: Write the failing tests** (replace `test_select_30_delta_put` and add new; keep the other existing tests):

```python
from src.engine_v2.options.select import (
    select_contract, derived_band, option_mark, intrinsic_value, expiry_underlying)

def test_derived_band():
    assert derived_band(7) == (5, 10)
    assert derived_band(30) == (28, 33)
    assert derived_band(5) == (5, 8)      # floor clamps at 5

def test_select_contract_expiry_first_then_strike():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=30, root="SPY")
    assert c is not None and c.right == "P" and c.root == "SPY"
    lo, hi = derived_band(30)
    day = ch[(ch["date"] == d) & (ch["right"] == "P")]
    dtes = day.groupby("expiry")["dte"].first()
    eligible = dtes[(dtes >= lo) & (dtes <= hi)]
    # 1) chosen expiry is the eligible one nearest 30
    err = (eligible - 30).abs()
    assert (pd.Timestamp(c.expiry) in eligible[err == err.min()].index)
    # 2) within that expiry, strike is nearest-delta
    e = day[day["expiry"] == c.expiry]
    best = (e["delta"].abs() - 0.30).abs().min()
    got = (e[e["strike"] == c.strike]["delta"].abs() - 0.30).abs().iloc[0]
    assert got == pytest.approx(best, abs=1e-9)

def test_select_contract_deterministic():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    a = select_contract(ch, d, "P", 0.30, target_dte=30, root="SPY")
    b = select_contract(ch, d, "P", 0.30, target_dte=30, root="SPY")
    assert a == b

def test_select_contract_sits_out_when_band_empty():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    # target so small the band [5,8] may or may not exist in fixture; force miss:
    ch2 = ch[ch["dte"] > 200]
    assert select_contract(ch2, d, "P", 0.30, target_dte=7, root="SPY") is None

def test_select_contract_root_propagates():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_contract(ch, d, "P", 0.30, target_dte=30, root="GDX")
    assert c.root == "GDX"
```

Also update `test_option_mark_and_absent` and `test_expiry_underlying` to obtain their contract via `select_contract(ch, d, "P", 0.30, target_dte=30, root="SPY")`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_select.py -q`
Expected: FAIL with `ImportError: cannot import name 'select_contract'`

- [ ] **Step 3: Implement** — replace `select_strike_by_delta` in `select.py` with:

```python
def derived_band(target_dte: int) -> tuple[int, int]:
    """DTE guard rail derived from the target. Floor rejects expiry stubs;
    ceiling rejects a monthly when the weekly is absent. Display-only upstream."""
    return max(5, target_dte - 2), target_dte + 3

def select_contract(chain, date, right, target_delta, target_dte, root):
    """Expiry FIRST (nearest target_dte within derived_band, from expiries visible
    on `date` only), THEN strike (nearest |delta| within that one expiry).
    Deterministic; returns None -> sit in cash."""
    lo, hi = derived_band(target_dte)
    cand = chain[(chain["date"] == date) & (chain["right"] == right)]
    if cand.empty:
        return None
    dtes = cand.groupby("expiry")["dte"].first()
    dtes = dtes[(dtes >= lo) & (dtes <= hi)]
    if dtes.empty:
        return None
    err = (dtes - target_dte).abs()
    best_exp = dtes[err == err.min()].index.max()   # tie -> longer-dated
    e = cand[cand["expiry"] == best_exp]
    row = e.loc[(e["delta"].abs() - abs(target_delta)).abs().idxmin()]
    return Contract(root, row["expiry"], float(row["strike"]), right)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_select.py -q`
Expected: PASS. (`test_wheel_*` will fail until Task 2 — expected; do not fix here.)

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/select.py tests/engine_v2/options/test_select.py
git commit -m "feat: expiry-first deterministic contract selection with derived DTE band"
```

---

### Task 2: `wheel.py` — new config, cash yield, flat-day count

**Files:**
- Modify: `src/engine_v2/options/wheel.py`
- Test: `tests/engine_v2/options/test_wheel_engine.py`, `tests/engine_v2/options/test_wheel_parts.py`, plus any test constructing `WheelConfig(dte_min=..., dte_max=...)` (grep and migrate all).

**Interfaces:**
- Consumes: `select_contract`, `derived_band` from Task 1.
- Produces: `WheelConfig` with fields exactly: `starting_capital=100_000.0, put_delta=0.20, call_delta=0.20, target_dte=7, take_profit_pct=0.50, cash_yield=0.0, ticker="SPY", contract_multiplier=100, commission_per_contract=0.65`. `WheelResult` gains `days_flat: int = 0`. `run_wheel(chain, cfg, intraday=None)` signature unchanged.

- [ ] **Step 1: Write failing tests** (add; migrate existing configs mechanically to `target_dte=30` where they used `dte_min=25, dte_max=45`):

```python
def test_config_has_no_dte_window():
    import dataclasses
    names = {f.name for f in dataclasses.fields(WheelConfig)}
    assert "target_dte" in names and "cash_yield" in names and "ticker" in names
    assert "dte_min" not in names and "dte_max" not in names

def test_contracts_carry_ticker():
    ch = _cycle_chain()
    res = run_wheel(ch, WheelConfig(target_dte=30, ticker="GDX"))
    sells = [t for t in res.trades if t.action.startswith("SELL")]
    assert sells and all(t.contract.root == "GDX" for t in sells)

def test_cash_yield_accrues_on_idle_cash():
    ch = _cycle_chain()
    flat = run_wheel(ch, WheelConfig(target_dte=30, put_delta=0.0001))  # never trades
    paid = run_wheel(ch, WheelConfig(target_dte=30, put_delta=0.0001, cash_yield=0.05))
    days = (ch["date"].max() - ch["date"].min()).days
    expect = 100_000.0 * ((1 + 0.05 / 365) ** days - 1)
    assert paid.equity.iloc[-1] - flat.equity.iloc[-1] == pytest.approx(expect, rel=0.02)

def test_days_flat_counted():
    ch = _cycle_chain()
    res = run_wheel(ch, WheelConfig(target_dte=7))
    assert res.days_flat >= 0
    never = run_wheel(ch[ch["dte"] > 200], WheelConfig(target_dte=7))
    assert never.days_flat == len(never.equity)

def test_hold_to_expiry_at_tp_100():
    ch = _cycle_chain()
    res = run_wheel(ch, WheelConfig(target_dte=30, take_profit_pct=1.0))
    assert not [t for t in res.trades if t.action.startswith("CLOSE")]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_wheel_engine.py -q`
Expected: FAIL (`target_dte` unknown / `days_flat` missing).

- [ ] **Step 3: Implement in `wheel.py`:**

1. Replace the `WheelConfig` fields as in Interfaces (delete `dte_min`/`dte_max`).
2. Import `select_contract` instead of `select_strike_by_delta`; both call sites become
   `select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, cfg.ticker)` (and `"C"`/`call_delta`).
3. `WheelResult` gains `days_flat: int = 0`.
4. In the daily loop, before section 1:

```python
        if prev_d is not None and cfg.cash_yield > 0:
            cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days
        prev_d = d
```

   (initialize `prev_d = None` before the loop; apply to `cash` only — shares/liability are not collateral).
5. TP guard becomes `if cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0 and d < c.expiry:` — `>= 1.0` means hold to expiry per the contract.
6. After section 2, count flat days:

```python
        if short is None and not (phase == "CALL" and shares >= mult):
            days_flat += 1
```

   (initialize `days_flat = 0`; a day holding only shares with no writable call is *not* flat — it is exposed).
7. Return `WheelResult(..., days_flat=days_flat)`.

- [ ] **Step 4: Migrate every other test file** that constructs `WheelConfig` with `dte_min/dte_max` (grep: `grep -rln "dte_min" tests/`) — replace with `target_dte=30` (the old 25–45 window's center; band (28,33) still selects from the fixture's monthly expiries). Fixture note: if a fixture only carries one expiry near 30 DTE this is exactly equivalent.

- [ ] **Step 5: Run full options suite**

Run: `.venv/bin/python -m pytest tests/engine_v2/options tests/test_wheel_dashboard.py -q`
Expected: engine tests PASS; `test_wheel_dashboard.py` may fail on `WheelConfig(dte_min=...)` — if it constructs configs, migrate those call sites too (test files are not `dashboard/**`).

- [ ] **Step 6: Commit**

```bash
git add -A src/engine_v2/options/wheel.py tests/
git commit -m "feat: target-DTE wheel config, cash-yield accrual, flat-day accounting"
```

---

### Task 3: `report.py` — honest benchmarks + new stats

**Files:**
- Modify: `src/engine_v2/options/report.py`
- Test: `tests/engine_v2/options/test_report.py`, `tests/engine_v2/options/test_report_format.py`

**Interfaces:**
- Consumes: `WheelResult.days_flat`, trades (Task 2).
- Produces:
  - `buy_hold_curve(chain, starting_capital) -> pd.Series` (rename of `spy_buy_hold`; series name `"buy_hold"`).
  - `spy_curve(starting_capital, index, path="data/options/spy_greeks_eod_all.parquet") -> pd.Series | None` — loads real SPY underlying, reindex-ffill to `index`; `None` if file missing.
  - `WheelReport`: `benchmark` **renamed** `benchmark_underlying`; new `benchmark_spy: dict | None`; `benchmark_recent` renamed `benchmark_underlying_recent`.
  - `wheel_report(result, chain, cfg, recent_start="2021-07-01", spy_path="data/options/spy_greeks_eod_all.parquet")`.
  - `wheel_stats` gains: `"realized_dte"` (`pd.Series`, `value_counts().sort_index()` of `(t.contract.expiry - t.date).days` over SELL trades), `"n_days_flat"`, `"pct_days_flat"` (needs `result` — change signature to `wheel_stats(result, cfg)` and update callers).

- [ ] **Step 1: Write failing tests:**

```python
def test_benchmark_underlying_is_this_chain():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg, spy_path="nonexistent.parquet")
    assert rep.benchmark_spy is None
    assert rep.benchmark_underlying["total_return"] == pytest.approx(
        float(ch.groupby("date")["underlying"].first().iloc[-1]
              / ch.groupby("date")["underlying"].first().iloc[0] - 1), rel=1e-6)

def test_spy_benchmark_loads_real_spy(tmp_path):
    res, ch, cfg = _run()
    spy = ch.copy(); spy["underlying"] = spy["underlying"] * 2  # distinct series
    p = tmp_path / "spy.parquet"; spy.to_parquet(p)
    rep = wheel_report(res, ch, cfg, spy_path=str(p))
    assert rep.benchmark_spy is not None

def test_stats_have_dte_distribution_and_flat_days():
    res, ch, cfg = _run()
    rep = wheel_report(res, ch, cfg, spy_path="nonexistent.parquet")
    assert rep.stats["n_days_flat"] == res.days_flat
    assert 0.0 <= rep.stats["pct_days_flat"] <= 1.0
    assert rep.stats["realized_dte"].sum() == rep.stats["n_puts_sold"] + rep.stats["n_calls_sold"]
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/engine_v2/options/test_report.py -q` → FAIL (attribute names).

- [ ] **Step 3: Implement** per Interfaces. `spy_curve`:

```python
def spy_curve(starting_capital, index, path="data/options/spy_greeks_eod_all.parquet"):
    import os
    if not os.path.exists(path):
        return None
    und = pd.read_parquet(path, columns=["date", "underlying"]).groupby("date")["underlying"].first()
    und = und.reindex(index).ffill().dropna()
    if und.empty:
        return None
    return (starting_capital * und / und.iloc[0]).rename("spy_buy_hold")
```

`format_report`: benchmark block prints "Benchmark — buy-hold {cfg.ticker}" and, when present, "Benchmark — buy-hold SPY"; append `flat {pct_days_flat:.0%} of days` to the stats line, and a `realized DTE: {min}..{max} (median {median})` line. Fix `test_report_format.py` expectations accordingly.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/engine_v2/options -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/
git commit -m "feat: per-underlying + real-SPY benchmarks, realized-DTE and flat-day stats"
```

---

### Task 4: `data.py` — ticker discovery (parallel-safe; independent of Tasks 1–3)

**Files:**
- Create: `src/engine_v2/options/data.py`
- Test: `tests/engine_v2/options/test_data.py`

**Interfaces:**
- Produces: `available_tickers(data_dir="data/options") -> list[str]` (upper-case, sorted, from `{t}_greeks_eod_all.parquet`); `intraday_path(ticker, data_dir="data/options") -> str | None`; `chain_path(ticker, data_dir="data/options") -> str`.

- [ ] **Step 1: Failing tests:**

```python
from src.engine_v2.options.data import available_tickers, intraday_path, chain_path

def test_available_tickers(tmp_path):
    for n in ("spy_greeks_eod_all.parquet", "gdx_greeks_eod_all.parquet",
              "gdx_ohlc_1h_all.parquet", "junk.parquet"):
        (tmp_path / n).touch()
    assert available_tickers(str(tmp_path)) == ["GDX", "SPY"]
    assert intraday_path("GDX", str(tmp_path)) is not None
    assert intraday_path("SPY", str(tmp_path)) is None
    assert chain_path("GDX", str(tmp_path)).endswith("gdx_greeks_eod_all.parquet")
```

- [ ] **Step 2: Verify fail** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** (glob `*_greeks_eod_all.parquet`, strip suffix, upper).
- [ ] **Step 4: Verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat: per-ticker data discovery helpers"`.

---

### Task 5: Intraday TP fills at the NEXT hourly bar

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (the intraday TP block)
- Test: `tests/engine_v2/options/test_wheel_intraday_tp.py`

**Interfaces:** consumes the `intraday` marks dict (unchanged shape). Behavior change only: when hourly bar *i* crosses the threshold, fill at bar *i+1*'s close; if *i* is the day's last bar, fall back to the EOD ask path (existing code already runs after).

- [ ] **Step 1: Failing test:**

```python
def test_tp_fills_next_bar_not_crossing_bar():
    # bars: 10:30 crosses (0.9), 11:30 = 1.1 -> fill must be 1.1
    ...build two-bar intraday frame for the held contract, thresh 1.0...
    res = run_wheel(ch, cfg, intraday=marks)
    close_trade = [t for t in res.trades if t.action == "CLOSE_PUT"][0]
    assert close_trade.price_per_contract == pytest.approx(1.1)
    assert close_trade.date == bars.iloc[1]["timestamp"]

def test_tp_crossing_on_last_bar_falls_back_to_eod():
    ...single-bar day crossing -> no intraday fill; EOD ask path decides...
```

(Adapt the fixture-building helpers already present in this test file.)

- [ ] **Step 2: Verify fail.** Current code fills at the crossing bar.
- [ ] **Step 3: Implement** — in the `for _, bar in day.iterrows()` loop, iterate with position lookahead (`day.iloc[i+1]`); on cross at `i`, fill at `day.iloc[i+1]["close"]` and stamp its timestamp; if `i` is last, do not fill intraday (EOD check below handles it).
- [ ] **Step 4: Verify pass**, run whole options suite.
- [ ] **Step 5: Commit** — `git commit -m "feat: intraday take-profit fills at next bar (no same-bar fills)"`.

---

### Task 6: Full-suite green + the two SPY runs

**Files:**
- Create: `scripts/run_spy_baselines.py`

**Interfaces:** consumes everything above. Produces two `format_report` outputs saved to `data/options/reports/` (gitignored) and printed.

- [ ] **Step 1:** `.venv/bin/python -m pytest -q` → everything green except the known workbench flake.
- [ ] **Step 2:** Write `scripts/run_spy_baselines.py`:

```python
"""Audit run (30-delta / target 30 -- the config the published +58.3% believed it
was testing) and SPY-as-basket-member run (20-delta / target 7). Raw numbers only."""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report, format_report

ch = pd.read_parquet("data/options/spy_greeks_eod_all.parquet")
outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
for name, cfg in [
    ("audit_30d_t30", WheelConfig(put_delta=0.30, call_delta=0.30, target_dte=30, ticker="SPY")),
    ("basket_20d_t7", WheelConfig(put_delta=0.20, call_delta=0.20, target_dte=7,  ticker="SPY")),
]:
    res = run_wheel(ch, cfg)
    txt = format_report(wheel_report(res, ch, cfg))
    (outd / f"{name}.txt").write_text(txt)
    print(f"\n=== {name} ===\n{txt}")
```

- [ ] **Step 3:** Run it. **Report raw output verbatim to the owner — no interpretation attached** (pre-registered rule).
- [ ] **Step 4: Commit** — `git commit -m "feat: SPY baseline runner (audit + basket-config)"`.
