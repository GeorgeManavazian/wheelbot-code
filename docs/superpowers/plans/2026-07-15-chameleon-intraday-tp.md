# Chameleon intraday-TP mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the regime router's WHEEL posture the wheel's next-valid-hour take-profit fill, so router-vs-wheel is an intraday-vs-intraday comparison, while routing decisions stay EOD.

**Architecture:** Transplant the wheel's proven intraday-TP block (`wheel.py:150-181`) into the router's short-management block (`regime_router.py:101-108`), gated behind a new `intraday=None` param that keeps `None` byte-identical to today. Add a thin `run_regime_router_intraday` wrapper mirroring `run_wheel_intraday`, extend the `--router` referee with a `--hourly` mode, and produce the hourly A/B report.

**Tech Stack:** Python 3, pandas, pytest. Run tests with `.venv/bin/python -m pytest` (the `.venv/bin/pytest` shebang is broken in this checkout).

## Global Constraints

- **Seen four only:** SPY, GDX, SLV, XOP. Unseen (XBI EEM EWZ TLT ARKK) stay behind `--after-basket-run`; do not run them — spends the wheel's pre-registration.
- **Byte-identical invariant:** `run_regime_router(..., intraday=None)` must produce output identical to the current router. This is load-bearing; protects the 2026-07-15 deep-audit-clean status.
- **close > 0 filter everywhere:** a zero-close hourly bar is not a price (XOP 2020 +2,582% phantom-fill bug). Trigger and fill both use valid prints only.
- **XOP arm starts 2020-07-01** (unadjusted 1:4 reverse split 2020-03-31). Carried via `DEFAULT_CLEAN_START` — do not remove.
- **Router v1 refuses roll/stop/gates/liquidate.** Do not add them.
- **Citation gate:** no hourly router number is quotable until `audit_defense_execution.py --router --hourly` exits 0 on the seen four.
- **Data on disk:** `data/options/{ticker}_ohlc_1h_all.parquet` (hourly bars, all 9 tickers incl. SPY), `data/options/{ticker}_greeks_eod_all.parquet` (EOD chains). No new pulls needed.

---

### Task 1: Engine — `intraday=` param + TP transplant + fill counters

**Files:**
- Modify: `src/engine_v2/options/regime_router.py` (signature `:52-53`, `RouterResult` `:22-35`, short-management TP `:101-108`, result construction `:233-239`)
- Test: `tests/test_regime_router_intraday.py` (create)

**Interfaces:**
- Consumes: `RouterResult`, `run_regime_router`, `WheelConfig`, `Trade` (existing).
- Produces:
  - `run_regime_router(chain, cfg, regime_states, intraday=None) -> RouterResult`
    where `intraday` is `dict[(pd.Timestamp expiry, float strike, str right)] -> DataFrame[timestamp, close]` (same shape `intraday_marks` produces and `run_wheel` consumes), or `None`.
  - `RouterResult.intraday_tp_fills: int`, `RouterResult.eod_tp_fills: int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_regime_router_intraday.py`:

```python
import pandas as pd
import numpy as np
from src.engine_v2.options.regime_router import run_regime_router
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.portfolio import DEFAULT_CLEAN_START
from src.engine_v2.options.intraday import intraday_marks
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")


def _load(t):
    ch = pd.read_parquet(chain_path(t))
    ch["date"] = pd.to_datetime(ch["date"])
    if t in DEFAULT_CLEAN_START:
        ch = ch[ch["date"] >= DEFAULT_CLEAN_START[t]].reset_index(drop=True)
    return ch, regime_series(closes_for(t))


def test_intraday_none_is_byte_identical():
    # The load-bearing invariant: intraday=None must equal the current router
    # exactly, on every seen ticker. Protects the deep-audit-clean status.
    for t in ["SPY", "GDX", "SLV", "XOP"]:
        ch, states = _load(t)
        cfg = WheelConfig(ticker=t, **BASE)
        a = run_regime_router(ch, cfg, states)
        b = run_regime_router(ch, cfg, states, intraday=None)
        assert a.equity.equals(b.equity), t
        assert len(a.trades) == len(b.trades), t
        for ta, tb in zip(a.trades, b.trades):
            assert ta.action == tb.action and ta.date == tb.date, t
            assert ta.price_per_contract == tb.price_per_contract, t
        assert a.intraday_tp_fills == 0 and a.eod_tp_fills >= 0


def test_intraday_tp_fills_at_next_valid_bar():
    # A synthetic short whose price crosses the TP threshold mid-day fills at
    # the NEXT valid bar's close, not the crossing bar, and is booked intraday.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    # find a SELL_PUT leg that survived to EOD-TP or expiry in the base run
    sells = [t for t in base.trades if t.action == "SELL_PUT"]
    assert sells, "expected at least one SELL_PUT in the SPY router run"
    leg = sells[0]
    c = leg.contract
    credit = leg.price_per_contract
    thresh = (1 - cfg.take_profit_pct) * credit
    open_day = pd.Timestamp(leg.date).normalize()
    # one intraday day AFTER open: a crossing bar then a higher valid next bar
    day = open_day + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=h) for h in (10, 11, 12)],
        "close":     [credit, thresh * 0.5, thresh * 0.9],  # bar1 crosses, fill at bar2
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    fills = [t for t in res.trades
             if t.action == "CLOSE_PUT" and t.contract == c
             and pd.Timestamp(t.date) == day + pd.Timedelta(hours=11)]
    assert fills, "expected an intraday CLOSE_PUT at the next valid bar (11:00)"
    assert abs(fills[0].price_per_contract - thresh * 0.5) < 1e-9
    assert res.intraday_tp_fills >= 1


def test_last_bar_cross_falls_through_to_eod():
    # A cross on the day's LAST bar has no next bar -> no intraday fill.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    leg = [t for t in base.trades if t.action == "SELL_PUT"][0]
    c = leg.contract
    thresh = (1 - cfg.take_profit_pct) * leg.price_per_contract
    day = pd.Timestamp(leg.date).normalize() + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=11)],
        "close":     [thresh + 1.0, thresh * 0.5],  # cross only on the LAST bar
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    assert not [t for t in res.trades if t.action == "CLOSE_PUT"
                and t.contract == c
                and pd.Timestamp(t.date) == day + pd.Timedelta(hours=11)]


def test_zero_close_bar_never_triggers():
    # A zero-close bar below threshold is not a price and must not fill.
    ch, states = _load("SPY")
    cfg = WheelConfig(ticker="SPY", **BASE)
    base = run_regime_router(ch, cfg, states)
    leg = [t for t in base.trades if t.action == "SELL_PUT"][0]
    c = leg.contract
    thresh = (1 - cfg.take_profit_pct) * leg.price_per_contract
    day = pd.Timestamp(leg.date).normalize() + pd.Timedelta(days=1)
    intr = {(pd.Timestamp(c.expiry), float(c.strike), c.right): pd.DataFrame({
        "timestamp": [day + pd.Timedelta(hours=h) for h in (10, 11, 12)],
        "close":     [0.0, 0.0, 0.0],  # phantom bars: below thresh but invalid
    })}
    res = run_regime_router(ch, cfg, states, intraday=intr)
    assert not [t for t in res.trades if t.action == "CLOSE_PUT"
                and t.contract == c and pd.Timestamp(t.date).normalize() == day]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_regime_router_intraday.py -v`
Expected: FAIL — `TypeError: run_regime_router() got an unexpected keyword argument 'intraday'` (and `AttributeError` on `intraday_tp_fills`).

- [ ] **Step 3: Add the `intraday` param and result fields**

In `src/engine_v2/options/regime_router.py`, extend `RouterResult` (after `days_half_size` at `:35`):

```python
    days_half_size: int = 0       # days holding a trimmed trend position
    intraday_tp_fills: int = 0    # TP closes filled at a next-valid hourly bar
    eod_tp_fills: int = 0         # TP closes filled at EOD ask (no intraday hit)
```

Change the signature (`:52-53`):

```python
def run_regime_router(chain: pd.DataFrame, cfg: WheelConfig,
                      regime_states: pd.DataFrame, intraday=None) -> RouterResult:
```

Initialize counters next to the other accumulators (after `unknown_logged_days = set()` at `:79`):

```python
    unknown_logged_days = set()
    intraday_tp_fills, eod_tp_fills = 0, 0
```

- [ ] **Step 4: Transplant the intraday-first TP into the short-management block**

Replace the EOD-only TP block (`regime_router.py:101-108`, the `if (cfg.take_profit_pct ...` through the EOD `CLOSE_PUT/CLOSE_CALL` append) with the two-tier version:

```python
            if cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0 and d < c.expiry:
                thresh = (1 - cfg.take_profit_pct) * short["credit"]
                tp_fired = False
                key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
                if intraday is not None and key in intraday:
                    bars = intraday[key]
                    # close > 0 only: a bar with no trade arrives as close=0 and
                    # is not a price. Trigger and fill both use valid prints;
                    # "next bar" means next VALID bar (mirrors wheel.py:153-174).
                    day = (bars[(bars["timestamp"].dt.normalize() == d)
                                & (bars["close"] > 0)]
                           .sort_values("timestamp").reset_index(drop=True))
                    # decide on bar i, fill at bar i+1's close. A cross on the
                    # day's LAST bar has no next bar -> EOD check decides instead.
                    for i in range(len(day) - 1):
                        if day.iloc[i]["close"] <= thresh:
                            fill = day.iloc[i + 1]
                            cost = fill["close"] * mult * n + cfg.commission_per_contract * n
                            cash -= cost; campaign_premium -= cost
                            trades.append(Trade(fill["timestamp"],
                                "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                c, n, float(fill["close"]), cash, campaign))
                            short = None; closed_today = c; tp_fired = True
                            intraday_tp_fills += 1
                            break
                if not tp_fired and short is not None and mark is not None and mark.ask <= thresh:
                    cost = buy_cost(mark, n, cfg)
                    cash -= cost; campaign_premium -= cost
                    trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                        c, n, mark.ask, cash, campaign))
                    short = None; closed_today = c
                    eod_tp_fills += 1
```

Note: `mult` (`:65`), `n` and `c` (`:99`), `mark` (`:100`), `buy_cost` (imported `:15`) are already in scope. When `intraday is None`, only the `if not tp_fired ...` EOD branch runs — identical to the replaced code, guaranteeing byte-identical output.

- [ ] **Step 5: Thread the counters into the result**

In the `return RouterResult(...)` (`:233-239`), add the two fields:

```python
                        n_trimmed_entries=n_trimmed_entries,
                        days_half_size=days_half_size,
                        intraday_tp_fills=intraday_tp_fills,
                        eod_tp_fills=eod_tp_fills)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_regime_router_intraday.py -v`
Expected: PASS (all four tests).

- [ ] **Step 7: Run the full router suite for regressions**

Run: `.venv/bin/python -m pytest tests/ -k "router or regime" -q`
Expected: PASS, no regressions (the all-chop byte-identical anchor still green).

- [ ] **Step 8: Commit**

```bash
git add src/engine_v2/options/regime_router.py tests/test_regime_router_intraday.py
git commit -m "feat: intraday-TP fills in regime router WHEEL posture (intraday= param, byte-identical when None)"
```

---

### Task 2: Wrapper — `run_regime_router_intraday`

**Files:**
- Modify: `src/engine_v2/options/intraday.py` (append after `run_wheel_intraday` at `:77-80`)
- Test: `tests/test_regime_router_intraday.py` (add one test)

**Interfaces:**
- Consumes: `run_regime_router` (Task 1), `intraday_marks` (existing, `intraday.py:47`).
- Produces: `run_regime_router_intraday(chain, cfg, intraday_df, regime_states) -> RouterResult`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_regime_router_intraday.py`:

```python
def test_wrapper_equals_manual_intraday_call():
    from src.engine_v2.options.intraday import run_regime_router_intraday
    ch, states = _load("GDX")
    cfg = WheelConfig(ticker="GDX", **BASE)
    ih = pd.read_parquet("data/options/gdx_ohlc_1h_all.parquet")
    a = run_regime_router_intraday(ch, cfg, ih, states)
    b = run_regime_router(ch, cfg, states, intraday=intraday_marks(ih))
    assert a.equity.equals(b.equity)
    assert len(a.trades) == len(b.trades)
    assert a.intraday_tp_fills == b.intraday_tp_fills
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_regime_router_intraday.py::test_wrapper_equals_manual_intraday_call -v`
Expected: FAIL — `ImportError: cannot import name 'run_regime_router_intraday'`.

- [ ] **Step 3: Add the wrapper**

Append to `src/engine_v2/options/intraday.py`:

```python
def run_regime_router_intraday(chain, cfg, intraday_df, regime_states):
    from .regime_router import run_regime_router
    return run_regime_router(chain, cfg, regime_states,
                             intraday=intraday_marks(intraday_df))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_regime_router_intraday.py::test_wrapper_equals_manual_intraday_call -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/intraday.py tests/test_regime_router_intraday.py
git commit -m "feat: run_regime_router_intraday wrapper (mirrors run_wheel_intraday)"
```

---

### Task 3: Referee — `--router --hourly` citation gate

**Files:**
- Modify: `scripts/audit_defense_execution.py` (`audit_router` `:498`, option-leg walk `:570-607`, `main` dispatch `:660-670`)
- Test: run the referee directly (this task's deliverable is an exit-0 audit, not a pytest).

**Interfaces:**
- Consumes: `run_regime_router_intraday` (Task 2), `_load_bars_independent` (existing, `:226`), `run_regime_router` (Task 1), `regime_series`, `closes_for`, `positions_from_trades`, `option_mark` (all existing in the module).
- Produces: `audit_router(hourly=False)`; `--router --hourly` dispatch.

The EOD share-action day-walk (`:517-568`) is unchanged — routing stays EOD. Only the option-leg termination derivation (`:570-607`) changes: in hourly mode it derives INTRADAY_TP (bar walk) first, then EOD_TP, then EXPIRY — exactly as `audit_hourly` does (`:267-329`).

- [ ] **Step 1: Parametrize `audit_router` with `hourly`**

Change the signature (`:498`):

```python
def audit_router(hourly=False):
```

After the `res = run_regime_router(ch, cfg, states)` line (`:511`), branch the engine run and load bars for hourly mode:

```python
        cfg = WheelConfig(ticker=t, **BASE, call_min_strike="basis")
        cbars_by_key = None
        if hourly:
            from src.engine_v2.options.intraday import run_regime_router_intraday
            cbars_by_key = _load_bars_independent(t)
            if cbars_by_key is None:
                lines.append(f"{t:<4} router-hourly  SKIPPED (no hourly parquet on disk)")
                continue
            ih = pd.read_parquet(f"data/options/{t.lower()}_ohlc_1h_all.parquet")
            ih["timestamp"] = pd.to_datetime(ih["timestamp"])
            res = run_regime_router_intraday(ch, cfg, ih, states)
        else:
            res = run_regime_router(ch, cfg, states)
```

- [ ] **Step 2: Add intraday-first derivation to the option-leg walk**

Replace the per-leg `expected` derivation inside the leg loop (`:577-585`, the `expected = None` through the `else: expected = ("EXPIRY", d); break`) with a version that walks hourly bars first in hourly mode:

```python
            expected = None   # (kind, when[, price])
            for d in [dd for dd in und.index if pd.Timestamp(dd) > opened]:
                d = pd.Timestamp(d)
                if d < pd.Timestamp(c.expiry):
                    if hourly:
                        key = (pd.Timestamp(c.expiry), float(c.strike), c.right)
                        cb = cbars_by_key.get(key)
                        day = (cb[cb["timestamp"].dt.normalize() == d].reset_index(drop=True)
                               if cb is not None else None)
                        hit = None
                        if day is not None and len(day) > 1:
                            for i in range(len(day) - 1):
                                if day.iloc[i]["close"] <= thresh_mult * credit:
                                    hit = (day.iloc[i + 1]["timestamp"],
                                           float(day.iloc[i + 1]["close"]))
                                    break
                        if hit is not None:
                            expected = ("TP", hit[0], hit[1]); break
                    mark = option_mark(by_date[d], d, c)
                    if mark is not None and mark.ask <= thresh_mult * credit:
                        expected = ("TP", d, None); break
                else:
                    expected = ("EXPIRY", d, None); break
```

Then update the match block below it (`:592-607`) to unpack the 3-tuple and, for hourly TP, compare the exact fill timestamp (not just the day):

```python
            loc = f"{t}/router{'-hourly' if hourly else ''} {c.strike}{c.right} exp {pd.Timestamp(c.expiry).date()}"
            if expected is None:
                if leg["close_action"] != "OPEN_AT_END":
                    mismatches.append(f"{loc}: no trigger derived, ledger "
                                      f"{leg['close_action']}")
                continue
            kind, when, price = expected
            got_when = (pd.Timestamp(leg["closed"])
                        if leg["closed"] is not None else None)
            if kind == "TP":
                ok = leg["close_action"] in ("CLOSE_PUT", "CLOSE_CALL")
                if hourly and price is not None:  # intraday: exact timestamp match
                    ok = ok and got_when is not None and got_when == pd.Timestamp(when)
                else:                              # EOD: day-level match
                    ok = ok and got_when is not None and got_when.normalize() == pd.Timestamp(when).normalize()
                if ok:
                    ver["tp"] += 1
                else:
                    mismatches.append(f"{loc}: derived TP {when}, ledger "
                                      f"{leg['close_action']} at {got_when}")
            else:
                if got_when is not None and got_when.normalize() == pd.Timestamp(when).normalize():
                    ver["expiry"] += 1
                else:
                    mismatches.append(f"{loc}: derived expiry {pd.Timestamp(when).date()}, "
                                      f"ledger {leg['close_action']} at {got_when}")
```

Note: the conviction-trim arm (`:615-644`) stays EOD (`run_regime_router(ch, trim_cfg, states)`) — trim is a share-sizing check, orthogonal to intraday fills. Leave it unchanged; it runs in both modes. Update its label line and the final "VERIFIED" print to mention hourly when `hourly` is true:

```python
    print("\nROUTER-HOURLY EXECUTION VERIFIED: every route, share action, and "
          "intraday/EOD leg termination re-derived; ledger agrees."
          if hourly else
          "\nROUTER EXECUTION VERIFIED: every route, share action, and leg "
          "termination re-derived; ledger agrees.")
```

- [ ] **Step 3: Wire `--router --hourly` in `main`**

Change the `--router` dispatch (`:661-663`):

```python
    if "--router" in sys.argv:
        audit_router(hourly="--hourly" in sys.argv)
        return
```

- [ ] **Step 4: Run the EOD router referee — must still exit 0 (no regression)**

Run: `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution --router; echo "exit=$?"`
Expected: `ROUTER EXECUTION VERIFIED ...`, `exit=0`.

- [ ] **Step 5: Run the hourly router referee — the citation gate**

Run: `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution --router --hourly; echo "exit=$?"`
Expected: per-ticker `router-hourly` lines with `mismatches 0`, `ROUTER-HOURLY EXECUTION VERIFIED ...`, `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/audit_defense_execution.py
git commit -m "feat: --router --hourly referee re-derives intraday router fills (citation gate)"
```

---

### Task 4: Run script — produce the hourly A/B report

**Files:**
- Modify: `scripts/run_regime_router.py` (add `--hourly` flag, `:36-83`)

**Interfaces:**
- Consumes: `run_regime_router_intraday` (Task 2), existing `line`/`perf` helpers, `run_wheel` (existing intraday=None solo baseline).
- Produces: `data/options/reports/regime_router_hourly.txt` and stdout A/B.

The router-hourly headline result the owner asked for. Gated by the seen-only allow-list already in the script (`:40-43`) and the Task 3 citation gate.

- [ ] **Step 1: Add the `--hourly` flag and hourly router arm**

In `scripts/run_regime_router.py`, add an import (after `:12`):

```python
from src.engine_v2.options.intraday import run_regime_router_intraday
```

In `main` (`:36-37`), read the flag right after computing `tickers`:

```python
    tickers = [t.upper() for t in sys.argv[1:] if not t.startswith("--")] or SEEN
    hourly = "--hourly" in sys.argv
```

After `router = run_regime_router(ch, cfg, states)` (`:55`), add the hourly arm:

```python
        router = run_regime_router(ch, cfg, states)
        router_h = None
        if hourly:
            ih = pd.read_parquet(f"data/options/{t.lower()}_ohlc_1h_all.parquet")
            ih["timestamp"] = pd.to_datetime(ih["timestamp"])
            router_h = run_regime_router_intraday(ch, cfg, ih, states)
```

After the `ROUTER` line append (`:66-69`), add the hourly line when present:

```python
        if router_h is not None:
            lines.append(line("ROUTER-hourly", router_h.equity,
                              f"| intraday-TP {router_h.intraday_tp_fills}"
                              f"  EOD-TP {router_h.eod_tp_fills}"
                              f"  (intraday fills optimistic-biased: next-bar close,"
                              f" hourly trade prints, no quotes)"))
```

- [ ] **Step 2: Route the output filename for the hourly run**

Change the filename block (`:80-82`):

```python
    if hourly:
        fname = ("regime_router_hourly.txt" if tickers == SEEN
                 else f"regime_router_hourly_{'_'.join(t.lower() for t in tickers)}.txt")
    else:
        fname = ("regime_router.txt" if tickers == SEEN
                 else f"regime_router_{'_'.join(t.lower() for t in tickers)}.txt")
```

- [ ] **Step 3: Confirm the citation gate is green, then run the A/B**

Run: `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution --router --hourly; echo "exit=$?"`
Expected: `exit=0` (must pass before citing any number below).

Run: `PYTHONPATH=. .venv/bin/python scripts/run_regime_router.py --hourly`
Expected: A/B table per seen ticker with a `ROUTER-hourly` row and intraday/EOD-TP counts; writes `data/options/reports/regime_router_hourly.txt`.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_regime_router.py data/options/reports/regime_router_hourly.txt
git commit -m "feat: --hourly A/B for the regime router (ROUTER-hourly vs solo wheel vs buy-hold)"
```

---

## Self-Review

**Spec coverage:**
- Engine `intraday=` param + TP transplant + byte-identical invariant → Task 1 ✓
- `run_regime_router_intraday` wrapper → Task 2 ✓
- Referee `--router --hourly` citation gate → Task 3 ✓
- Fill-type counter in `RouterResult` → Task 1 (Steps 3, 5) ✓
- Contamination guards (close>0, XOP 2020-07+) → Global Constraints + carried in Task 1 code and `DEFAULT_CLEAN_START` ✓
- Seen-4 only / unseen guard → Global Constraints; run script's existing allow-list (`:40-43`) unchanged ✓
- Reporting / honesty flag → Task 4 (`ROUTER-hourly` line states optimistic bias) ✓
- Tests (invariant, intraday-fires, last-bar fallback, close=0 ignored, wrapper, referee exit 0) → Tasks 1-3 ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. Clean.

**Type consistency:** `run_regime_router(chain, cfg, regime_states, intraday=None)` and `run_regime_router_intraday(chain, cfg, intraday_df, regime_states)` used identically across Tasks 1-4. `RouterResult.intraday_tp_fills` / `.eod_tp_fills` defined in Task 1, consumed in Tasks 2-4. `audit_router(hourly=False)` defined and dispatched in Task 3. Consistent.
