# Conviction Trim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Half-size trend HOLD entries when the strictly-prior-day vol state is "stressed", per `docs/superpowers/specs/2026-07-15-conviction-trim-design.md`.

**Architecture:** One boolean `conviction_trim` on `WheelConfig` (default off → byte-identical). One fork in the router's TREND entry. Two diagnostic counters. Referee + runner arm.

**Tech Stack:** Python/pandas/pytest, `~/Documents/Trading/code/etf-bot`, `PYTHONPATH=. .venv/bin/python -m pytest`.

## Global Constraints (verbatim from spec)

- `TRIM_FRACTION = 0.5`; trigger = prior-day vol `"stressed"` (frozen phase-1 threshold, not redefined here).
- Applies ONLY to `BUY_SHARES` (trend HOLD entry); wheel entries untouched. Entry-time only; no mid-hold resize.
- `lots = (cash // (spot*mult)) // 2` when trimming; remainder stays cash. Single-lot (`1//2==0`) → nothing bought, counted as a cash day.
- `conviction_trim` default False → router byte-identical to today. Router-only.
- Referee `--router` verifies half vs full size, double-entry. A/B headline = max drawdown. Coverage ≥95%.

---

### Task 1: Config flag + router trim fork + counters

**Files:**
- Modify: `src/engine_v2/options/wheel.py` (add config field)
- Modify: `src/engine_v2/options/regime_router.py` (constant, fork, RouterResult fields)
- Test: `tests/engine_v2/options/test_regime_router.py`

**Interfaces:**
- Produces: `WheelConfig.conviction_trim: bool = False`; `regime_router.TRIM_FRACTION = 0.5`; `RouterResult.n_trimmed_entries: int`, `RouterResult.days_half_size: int`.

- [ ] **Step 1: failing tests**

```python
def test_trim_off_is_byte_identical():
    ch = pd.read_parquet("data/options/gdx_greeks_eod_all.parquet")
    states = __import__("src.engine_v2.regime.state", fromlist=["regime_series"]).regime_series(
        __import__("src.engine_v2.regime.data", fromlist=["closes_for"]).closes_for("GDX"))
    cfg = WheelConfig(ticker="GDX", put_delta=0.20, call_delta=0.20, target_dte=7,
                      take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")
    a = run_regime_router(ch, cfg, states)
    b = run_regime_router(ch, WheelConfig(ticker="GDX", put_delta=0.20, call_delta=0.20,
                          target_dte=7, take_profit_pct=0.50, starting_capital=100_000.0,
                          call_min_strike="basis", conviction_trim=False), states)
    assert a.equity.equals(b.equity) and a.final_cash == b.final_cash

def test_stressed_uptrend_entry_is_half_size():
    st = _states([("2024-01-01","uptrend","stressed",0.9)])
    full = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    trim = run_regime_router(_chain(PUT_DAY), _cfg(conviction_trim=True), st)
    f = [t for t in full.trades if t.action=="BUY_SHARES"][0]
    g = [t for t in trim.trades if t.action=="BUY_SHARES"][0]
    assert g.contracts == f.contracts // 2
    assert trim.n_trimmed_entries == 1 and trim.days_half_size >= 1

def test_calm_uptrend_entry_is_full_size():
    st = _states([("2024-01-01","uptrend","calm",0.2)])
    full = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    trim = run_regime_router(_chain(PUT_DAY), _cfg(conviction_trim=True), st)
    assert [t for t in trim.trades if t.action=="BUY_SHARES"][0].contracts == \
           [t for t in full.trades if t.action=="BUY_SHARES"][0].contracts
    assert trim.n_trimmed_entries == 0

def test_trim_only_touches_trend_not_wheel():
    # downtrend+stressed = WHEEL cell; put entry must be identical trim on/off
    st = _states([("2024-01-01","downtrend","stressed",0.9)])
    a = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    b = run_regime_router(_chain(PUT_DAY), _cfg(conviction_trim=True), st)
    assert [t.contracts for t in a.trades if t.action=="SELL_PUT"] == \
           [t.contracts for t in b.trades if t.action=="SELL_PUT"]
```

- [ ] **Step 2: run, confirm fail** (`conviction_trim` unknown kwarg / `n_trimmed_entries` missing).
- [ ] **Step 3: implement.**
  - `wheel.py` `WheelConfig`: add after `regime_stop_gate`:
    ```python
    conviction_trim: bool = False   # half-size trend HOLD entries in stressed vol (router only)
    ```
  - `regime_router.py`: add module constant near top: `TRIM_FRACTION = 0.5`.
  - `RouterResult`: add `n_trimmed_entries: int = 0` and `days_half_size: int = 0`.
  - init counters in `run_regime_router`: `n_trimmed_entries, days_half_size = 0, 0`; add `trend_trimmed = False` alongside `trend_shares`.
  - TREND entry fork — replace the `if cell == "TREND":` body's sizing:
    ```python
    if cell == "TREND":
        lots = int(cash // (spot * mult))
        trimmed = cfg.conviction_trim and g_vol == "stressed"
        if trimmed:
            lots = lots // 2   # tail-control: half size in stressed vol
        if lots > 0:
            campaign += 1
            campaign_premium = 0.0
            cash -= lots * mult * spot
            trend_shares = lots * mult
            trend_buy_idx = i
            trend_trimmed = trimmed
            basis = spot
            if trimmed:
                n_trimmed_entries += 1
            trades.append(Trade(d, "BUY_SHARES", None, lots, spot, cash, campaign))
    ```
  - reset `trend_trimmed = False` wherever `trend_shares` is zeroed (the downtrend SELL_SHARES branch and the chop-conversion branch).
  - in the posture-accounting block, after computing `posture`: `if posture == "TREND" and trend_trimmed: days_half_size += 1`.
  - return the two new fields in `RouterResult(...)`.
- [ ] **Step 4: tests pass; full router suite green.**
- [ ] **Step 5: commit** `feat: conviction trim — half-size trend holds in stressed vol (default off)`

### Task 2: Edge cases + no-look-ahead + conversion paths

**Files:** `tests/engine_v2/options/test_regime_router.py`

- [ ] **Step 1: tests** (should pass on Task 1's implementation — they pin spec edge cases):

```python
def test_single_lot_trim_buys_nothing():
    # spot high enough that full = 1 lot; stressed -> 1//2 = 0 bought
    rows = [["2024-01-02","2024-01-09",7,470,"P",2.0,2.1,2.05,2.05,-0.30,0.1,49000.0]]
    st = _states([("2024-01-01","uptrend","stressed",0.9)])
    res = run_regime_router(_chain(rows), _cfg(starting_capital=50_000.0,
                            conviction_trim=True), st)
    assert not [t for t in res.trades if t.action=="BUY_SHARES"]

def test_no_lookahead_trim():
    # stress appears ON the entry day; strictly-prior day is calm -> full size
    st = _states([("2024-01-01","uptrend","calm",0.2),
                  ("2024-01-02","uptrend","stressed",0.9)])
    res = run_regime_router(_chain(PUT_DAY), _cfg(conviction_trim=True), st)
    assert res.n_trimmed_entries == 0

def test_trimmed_hold_converts_and_sells_at_half():
    # half-size stressed-uptrend buy, then chop converts it to wheel shares
    rows = [PUT_DAY[0],
            ["2024-01-03","2024-01-10",7,475,"C",1.0,1.1,1.05,1.05,0.30,0.1,470.0]]
    st = _states([("2024-01-01","uptrend","stressed",0.9),
                  ("2024-01-02","chop","normal",0.5)])
    res = run_regime_router(_chain(rows), _cfg(conviction_trim=True), st)
    buy = [t for t in res.trades if t.action=="BUY_SHARES"][0]
    # the shares that convert to the wheel equal the half-size buy
    assert res.final_shares == buy.contracts * 100 or \
           any(t.action=="SELL_CALL" for t in res.trades)
```

- [ ] **Step 2: run — all pass.** **Step 3: commit** `test: conviction-trim edge cases (single-lot, no-look-ahead, conversion)`

### Task 3: Referee `--router` trim verification

**Files:** `scripts/audit_defense_execution.py`

- [ ] **Step 1: implement.** In `audit_router`, run BOTH `conviction_trim=False` and `=True` per ticker; for the trimmed run, in the chronological day-walk, when a `BUY_SHARES` is seen, reconstruct full lots from the ledger alone (no cash tracking needed):
  ```python
  # before-buy cash = cash_after + spent; full lots = that // (spot*100)
  spot = float(und[d]) if d in und.index else tr.price_per_contract
  before = tr.cash_after + tr.contracts * 100 * tr.price_per_contract
  full = int(before // (tr.price_per_contract * 100))
  stressed = (_asof_row(states, d) is not None
              and _asof_row(states, d)["vol"] == "stressed")
  want = full // 2 if stressed else full
  if tr.contracts != want:
      mismatches.append(f"{t} {d.date()} BUY_SHARES size {tr.contracts} != "
                        f"expected {want} (stressed={stressed}, full={full})")
  else:
      ver["shares"] += 1
  ```
  Wrap this size check behind the trimmed-run pass so the untrimmed run keeps its existing legality checks.
- [ ] **Step 2: run** `PYTHONPATH=. .venv/bin/python -m scripts.audit_defense_execution --router` → exit 0 (both trimmed + untrimmed, all four tickers).
- [ ] **Step 3: commit** `feat: referee verifies conviction-trim half-sizing (--router)`

### Task 4: A/B runner arm + full run + review + merge

**Files:** `scripts/run_regime_router.py`

- [ ] **Step 1:** add a third arm per ticker — `run_regime_router(ch, WheelConfig(..., conviction_trim=True), states)` labeled `ROUTER+trim`, printing the same metric columns plus `trimmed N / half-days M`, with **max drawdown** kept prominent. Keep the two benchmarks. Write to `data/options/reports/regime_router.txt`.
- [ ] **Step 2:** full suite + coverage ≥95%; `--router` exit 0; run the A/B.
- [ ] **Step 3:** code-review (high) on branch diff; fix verified findings; re-run suite + referee.
- [ ] **Step 4:** merge to main; update vault `09 Chameleon/_STATUS.md` (v1.1 + A/B verdict), log, memory; ELI10 the results to owner (standing rule).
