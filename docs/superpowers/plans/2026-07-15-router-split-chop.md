# v2 Router split-chop-by-200 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `split_chop` router rule that reclassifies a chop day trading at/above its 200-day SMA as an uptrend (hold), while chop below the line stays wheel — gated behind a default-OFF flag that keeps v1 byte-identical.

**Architecture:** One classification change in `regime_router._cell`, gated by `WheelConfig.split_chop`. Chop-recovering maps to the existing TREND cell AND relabels the returned trend to `"uptrend"` so downstream transitions treat it as a genuine hold. The referee's independent `_cell_local` mirrors the split; a `split_chop=True` arm is added to the referee and the A/B run script.

**Tech Stack:** Python 3, pandas, pytest. Run tests with `.venv/bin/python -m pytest` (the `.venv/bin/pytest` shebang is broken in this checkout).

## Global Constraints

- **PRE-REGISTERED / FROZEN:** the rule is frozen by the spec before any unseen-ticker run. Do not tune any threshold. `px_vs_200 >= 0` is the boundary — exact, not swept.
- **Byte-identical when `split_chop=False`:** `run_regime_router` output must be identical to v1 (merged @ `be2bc3d`) on all four seen tickers. Load-bearing; test-pinned.
- **Relabel to uptrend:** a chop-recovering day returns cell `"TREND"` AND trend `"uptrend"` (not `"chop"`) — the router's transitions key on the returned trend (`g_trend=="chop"` hands shares to the wheel, `g_trend=="downtrend"` force-sells), so it must present as uptrend or the hold is defeated.
- **No look-ahead:** `px_vs_200` is read from the last state row STRICTLY before day `d`, same staleness bound as `_state_before`. Missing/stale → treat as `< 0` (stays WHEEL).
- **Referee independence:** the referee re-derives the split with its OWN `px_vs_200` lookup — never imports the engine's `_cell`.
- **Seen four only** (SPY GDX SLV XOP); unseen refuse without `--after-basket-run`. No new knobs beyond the one bool. Chop below 200 stays WHEEL — do not touch the winning bucket.

---

### Task 1: Engine — `split_chop` config + `_px_before` + `_cell` split

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (`WheelConfig`, after `conviction_trim` at `:54`)
- Modify: `src/engine_v2/options/regime_router.py` (add `_px_before`; change `_cell` `:40-49`; call site `:91`)
- Test: `tests/test_router_split_chop.py` (create)

**Interfaces:**
- Consumes: `_state_before`, `is_unpaid_decline`, `GATE_STALENESS_DAYS` (existing, `wheel.py`); `regime_series`, `closes_for` (existing).
- Produces:
  - `WheelConfig.split_chop: bool = False`
  - `_px_before(states, d) -> float | None` in `regime_router.py`
  - `_cell(states, d, split_chop=False) -> tuple[str, str, str, bool]` (cell, trend, vol, unknown)
  - `run_regime_router(chain, cfg, regime_states, intraday=None)` now honors `cfg.split_chop`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_router_split_chop.py`:

```python
import pandas as pd
import numpy as np
from src.engine_v2.options.regime_router import run_regime_router, _cell, _px_before
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.portfolio import DEFAULT_CLEAN_START
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7, take_profit_pct=0.50,
            starting_capital=100_000.0, call_min_strike="basis")


def _load(t):
    ch = pd.read_parquet(chain_path(t)); ch["date"] = pd.to_datetime(ch["date"])
    if t in DEFAULT_CLEAN_START:
        ch = ch[ch["date"] >= DEFAULT_CLEAN_START[t]].reset_index(drop=True)
    return ch, regime_series(closes_for(t))


def _states(rows):
    # rows: list of (date, trend, vol, px_vs_200) -> a regime_series-shaped frame
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({"trend": [r[1] for r in rows], "vol": [r[2] for r in rows],
                         "px_vs_200": [r[3] for r in rows]}, index=idx)


def test_split_chop_off_is_byte_identical():
    # Load-bearing: split_chop=False must equal v1 on every seen ticker.
    for t in ["SPY", "GDX", "SLV", "XOP"]:
        ch, st = _load(t)
        base = run_regime_router(ch, WheelConfig(ticker=t, **BASE), st)
        off = run_regime_router(ch, WheelConfig(ticker=t, **BASE, split_chop=False), st)
        assert base.equity.equals(off.equity), t
        assert len(base.trades) == len(off.trades), t
        for a, b in zip(base.trades, off.trades):
            assert a.action == b.action and a.date == b.date, t
            assert a.price_per_contract == b.price_per_contract, t


def test_cell_chop_above_200_becomes_trend_when_on():
    # decision day d=2020-01-10 reads the strictly-prior row 2020-01-09.
    st = _states([("2020-01-09", "chop", "normal", 0.03)])
    d = pd.Timestamp("2020-01-10")
    assert _cell(st, d, split_chop=False) == ("WHEEL", "chop", "normal", False)
    assert _cell(st, d, split_chop=True) == ("TREND", "uptrend", "normal", False)


def test_cell_chop_below_200_stays_wheel_when_on():
    st = _states([("2020-01-09", "chop", "normal", -0.04)])
    d = pd.Timestamp("2020-01-10")
    assert _cell(st, d, split_chop=True) == ("WHEEL", "chop", "normal", False)


def test_cell_uptrend_and_downtrend_unaffected_by_split():
    up = _states([("2020-01-09", "uptrend", "calm", 0.10)])
    dn = _states([("2020-01-09", "downtrend", "calm", -0.10)])
    d = pd.Timestamp("2020-01-10")
    assert _cell(up, d, split_chop=True) == ("TREND", "uptrend", "calm", False)
    # downtrend+calm is an unpaid decline -> CASH, unchanged
    assert _cell(dn, d, split_chop=True) == ("CASH", "downtrend", "calm", False)


def test_px_before_is_strictly_prior_and_stale_safe():
    st = _states([("2020-01-09", "chop", "normal", 0.03)])
    # day-of row must NOT be read; only strictly-prior
    assert _px_before(st, pd.Timestamp("2020-01-10")) == 0.03
    assert _px_before(st, pd.Timestamp("2020-01-09")) is None   # no strictly-prior row
    # far-future date beyond staleness -> None
    assert _px_before(st, pd.Timestamp("2021-01-01")) is None


def test_missing_px_stays_wheel():
    # a chop row with NaN px_vs_200 -> _px_before None -> stays WHEEL
    st = _states([("2020-01-09", "chop", "normal", np.nan)])
    assert _cell(st, pd.Timestamp("2020-01-10"), split_chop=True) == ("WHEEL", "chop", "normal", False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_router_split_chop.py -v`
Expected: FAIL — `ImportError: cannot import name '_px_before'` and `TypeError: _cell() takes 2 positional arguments but 3 were given` / `WheelConfig` has no `split_chop`.

- [ ] **Step 3: Add the `split_chop` config field**

In `src/engine_v2/options/wheel.py`, after the `conviction_trim` field (`:54`):

```python
    conviction_trim: bool = False     # half-size trend HOLD entries in stressed vol (router only)
    split_chop: bool = False          # chop with px>=200d SMA -> treated as uptrend/hold (router only)
```

- [ ] **Step 4: Add `_px_before` and split `_cell` in `regime_router.py`**

In `src/engine_v2/options/regime_router.py`, the imports already include `_state_before` and `is_unpaid_decline` from `.wheel` (`:14-15`). Add `GATE_STALENESS_DAYS` to that import:

```python
from .wheel import (Trade, WheelConfig, is_unpaid_decline, _state_before,
                    sell_proceeds, buy_cost, GATE_STALENESS_DAYS)
```

Add `_px_before` immediately before `_cell` (near `:38`):

```python
def _px_before(states: pd.DataFrame, d: pd.Timestamp):
    """px_vs_200 from the last state row STRICTLY before d, same staleness rule
    as _state_before. None when missing/stale/NaN -> caller treats as < 0."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return None
    px = states.iloc[pos].get("px_vs_200")
    if px is None or pd.isna(px):
        return None
    return float(px)
```

Change `_cell` (`:40-49`) to take `split_chop` and apply the split. Replace the whole function:

```python
def _cell(states: pd.DataFrame, d: pd.Timestamp, split_chop: bool = False):
    """(cell, trend, vol, unknown) from the strictly-prior-day state.
    Cells: TREND (uptrend), WHEEL (chop, downtrend+stressed, unknown),
    CASH (downtrend + calm/normal). With split_chop, a chop day whose price is
    at/above its 200d SMA (px_vs_200 >= 0) is reclassified uptrend -> TREND; the
    returned trend is relabeled 'uptrend' so the router's transitions treat it as
    a genuine hold (not handed to the wheel). Chop below the line stays WHEEL."""
    trend, vol = _state_before(states, d)
    if (trend, vol) == ("unknown", "unknown"):
        return "WHEEL", trend, vol, True
    if trend == "uptrend":
        return "TREND", trend, vol, False
    if split_chop and trend == "chop":
        px = _px_before(states, d)
        if px is not None and px >= 0:
            return "TREND", "uptrend", vol, False
    if is_unpaid_decline(trend, vol):
        return "CASH", trend, vol, False
    return "WHEEL", trend, vol, False   # chop (any vol) or downtrend+stressed
```

Note: the split is checked BEFORE `is_unpaid_decline` (which only fires on `downtrend`, so ordering vs it is irrelevant for chop) and AFTER the `uptrend` short-circuit (a true uptrend never reaches the chop branch). This preserves every non-chop classification exactly.

- [ ] **Step 5: Pass `cfg.split_chop` into the `_cell` call**

In `regime_router.py:91`, change:

```python
        cell, g_trend, g_vol, unknown = _cell(regime_states, d, cfg.split_chop)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_router_split_chop.py -v`
Expected: PASS (all six tests, including byte-identical on the four tickers).

- [ ] **Step 7: Run the router/regime suite for regressions**

Run: `.venv/bin/python -m pytest tests/ -k "router or regime or intraday" -q`
Expected: PASS, no regressions (v1 all-chop anchor + intraday tests still green — `split_chop` defaults OFF everywhere).

- [ ] **Step 8: Commit**

```bash
git add src/engine_v2/options/wheel.py src/engine_v2/options/regime_router.py tests/test_router_split_chop.py
git commit -m "feat: split_chop router rule — chop above 200d SMA holds (default OFF, byte-identical when off)"
```

---

### Task 2: Referee — `_cell_local` split + `audit_router` split arm

**Files:**
- Modify: `scripts/audit_defense_execution.py` (`_cell_local` `:483-495`; `audit_router` — add split arm near the conviction-trim arm `:615-644`)
- Test: run the referee directly — deliverable is exit 0, not a pytest.

**Interfaces:**
- Consumes: `_asof_row` (existing), `run_regime_router` with `split_chop=True` (Task 1), `_cell_local`.
- Produces: `_cell_local(states, d, split_chop=False)`; a `router+split` audit arm.

The referee's share-action day-walk keys on `_cell_local`'s returned `(cell, trend)`. Mirroring Task 1, a chop-recovering day must return `("TREND", "uptrend")` so the day-walk treats it as a hold (no hand-to-wheel, no forced sale).

- [ ] **Step 1: Add `split_chop` to `_cell_local`**

Change `_cell_local` (`:483-495`) to:

```python
def _cell_local(states, d, split_chop=False):
    """Audit's own routing cell: uptrend->TREND; downtrend+stressed->WHEEL;
    downtrend else->CASH; chop or unknown->WHEEL. With split_chop, chop with
    px_vs_200 >= 0 -> ('TREND','uptrend') (mirrors the engine's relabel)."""
    row = _asof_row(states, d)
    if row is None:
        return "WHEEL", "unknown"
    if row["trend"] == "uptrend":
        return "TREND", row["trend"]
    if split_chop and row["trend"] == "chop":
        px = row.get("px_vs_200")
        if px is not None and not pd.isna(px) and float(px) >= 0:
            return "TREND", "uptrend"
    if row["trend"] == "downtrend" and row["vol"] == "stressed":
        return "WHEEL", row["trend"]
    if row["trend"] == "downtrend":
        return "CASH", row["trend"]
    return "WHEEL", row["trend"]
```

Note: `_asof_row` uses `max_stale_days=14`; the engine's `_px_before`/`_state_before` use `GATE_STALENESS_DAYS`. These staleness windows are the referee's own independent choice (it already re-derives `_cell_local` with `_asof_row` for v1), so leave `_asof_row` as-is — the referee's independence includes its own as-of rule, and v1 already passes with it.

- [ ] **Step 2: Thread `split_chop` through the day-walk that calls `_cell_local`**

`_cell_local` is called at `:539` inside `audit_router`'s share-action day-walk. That walk runs once per `res`. To audit the split arm, the walk must use the same `split_chop` the arm's `res` was produced with. The cleanest change: the existing day-walk audits the main `res` (v1, split_chop off) — leave it. Add the split arm as a SEPARATE re-derivation block (like the trim arm), each with its own `res` and its own day-walk call passing `split_chop=True`. See Step 3.

- [ ] **Step 3: Add the split arm to `audit_router`**

In `audit_router`, after the conviction-trim arm block (ends ~`:644`, before `grand_checked += ...`), add a split-arm re-derivation. It mirrors the trim arm's shape but re-derives routes with `split_chop=True` and checks that every share action + option entry lands in a cell the LOCAL split classifier agrees with:

```python
        # split-chop arm (spec 2026-07-15-router-split-chop): every route decision
        # re-derived with the LOCAL split cell. A chop day at/above 200d must route
        # TREND (hold) — no SELL_PUT / SELL_CALL on it; chop below stays WHEEL.
        split_cfg = WheelConfig(ticker=t, **BASE, call_min_strike="basis", split_chop=True)
        split_res = run_regime_router(ch, split_cfg, states)
        smm, sver = [], 0
        sevents = {}
        for tr in sorted(split_res.trades, key=lambda x: pd.Timestamp(x.date)):
            sevents.setdefault(pd.Timestamp(tr.date).normalize(), []).append(tr)
        for d in und.index:
            d = pd.Timestamp(d)
            cell, _ = _cell_local(states, d, split_chop=True)
            for tr in sevents.get(d.normalize(), []):
                if tr.action in ("SELL_PUT", "SELL_CALL"):
                    if cell != "WHEEL":
                        smm.append(f"{t} {d.date()} {tr.action} in non-WHEEL split cell ({cell})")
                    else:
                        sver += 1
                elif tr.action == "BUY_SHARES":
                    if cell != "TREND":
                        smm.append(f"{t} {d.date()} BUY_SHARES in non-TREND split cell ({cell})")
                    else:
                        sver += 1
        lines.append(f"{t:<4} router+split  routed-actions {sver:>4}  mismatches {len(smm)}")
        total_mm.extend(smm)
        grand_checked += sver
```

- [ ] **Step 4: Run the router referee — must exit 0 (v1 + trim + split all clean)**

Run: `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution --router; echo "exit=$?"`
Expected: per-ticker `router`, `router+trim`, and new `router+split` lines with `mismatches 0`; `ROUTER EXECUTION VERIFIED ...`; `exit=0`. (This run is slow — allow up to 10 minutes; run in background and wait for real output.)

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_defense_execution.py
git commit -m "feat: referee split-chop arm — re-derives split routes independently (exit 0)"
```

---

### Task 3: Run script — `ROUTER+split` A/B arm

**Files:**
- Modify: `scripts/run_regime_router.py` (`main` `:47-77`)

**Interfaces:**
- Consumes: `run_regime_router` with `split_chop=True` (Task 1), existing `line`/`perf` helpers.
- Produces: a `ROUTER+split` row in the A/B report (per seen ticker).

The in-sample A/B so the effect is visible. The existing seen-only allow-list (`:40-45`) is unchanged; `split_chop` stays OFF in the primary `ROUTER` arm.

- [ ] **Step 1: Add the split arm alongside the existing arms**

In `scripts/run_regime_router.py`, in the per-ticker loop, after the `trim = run_regime_router(...)` line (`:56-57`), add:

```python
        split = run_regime_router(ch, WheelConfig(ticker=t, **BASE,
                                                  split_chop=True), states)
```

After the `ROUTER+trim` line append (`:71-73`), add a `ROUTER+split` line. Include its posture mix and whipsaws so the shift toward holding is visible:

```python
        sdip = split.days_in_posture
        stransitions = sum(1 for a, b in zip(split.route_log, split.route_log[1:])
                           if a[3] != b[3])
        lines.append(line("ROUTER+split", split.equity,
                          f"| days T/W/C {sdip['TREND']}/{sdip['WHEEL']}/{sdip['CASH']}"
                          f"  transitions {stransitions}  whipsaws {split.whipsaw_pairs}"
                          f"  <-- chop>=200d held (frozen, judge on basket run)"))
```

- [ ] **Step 2: Run the A/B — confirm the split arm renders**

Run: `PYTHONPATH=. .venv/bin/python scripts/run_regime_router.py`
Expected: each seen ticker's block now has a `ROUTER+split` row with more TREND days / fewer WHEEL days than `ROUTER`, and it writes `regime_router.txt` (unchanged filename for the no-flag run). Numbers will look strong in-sample — that is expected and not evidence of generalization.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_regime_router.py
git commit -m "feat: ROUTER+split A/B arm (chop>=200d held) — in-sample view, frozen for basket"
```

---

## Self-Review

**Spec coverage:**
- `WheelConfig.split_chop` default False → Task 1 Step 3 ✓
- `_px_before` (strictly-prior, staleness-bounded, None on missing/stale) → Task 1 Step 4 + tests ✓
- `_cell` split returning `("TREND","uptrend",vol,False)` for chop≥200 → Task 1 Step 4 + `test_cell_chop_above_200_becomes_trend_when_on` ✓
- Byte-identical when OFF (all 4 tickers) → Task 1 `test_split_chop_off_is_byte_identical` ✓
- Look-ahead guard (strictly-prior px) → Task 1 `test_px_before_is_strictly_prior_and_stale_safe` ✓
- Missing/stale px stays WHEEL → Task 1 `test_missing_px_stays_wheel` ✓
- Referee `_cell_local` split + independent px lookup + relabel → Task 2 Step 1 ✓
- Referee split arm, `--router` exit 0 → Task 2 Steps 3-4 ✓
- Run-script `ROUTER+split` A/B arm → Task 3 ✓
- Seen-4 guard unchanged; no new knobs; chop<200 stays WHEEL → preserved (allow-list untouched; only the one bool added; split branch only reclassifies px≥0) ✓
- Frozen/pre-registered → spec is the freeze; plan adds no tuning ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. Clean.

**Type consistency:** `_cell(states, d, split_chop=False) -> (cell, trend, vol, unknown)` and `_cell_local(states, d, split_chop=False) -> (cell, trend)` used consistently. `_px_before(states, d) -> float | None`. `WheelConfig.split_chop: bool`. `run_regime_router(chain, cfg, regime_states, intraday=None)` honors `cfg.split_chop`. All consistent across Tasks 1-3.
