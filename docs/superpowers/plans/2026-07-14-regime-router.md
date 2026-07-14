# Regime Router (Regime Bot v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `run_regime_router` per `docs/superpowers/specs/2026-07-14-regime-router-design.md` — a per-ticker strategy router (uptrend→hold, chop/panic→wheel+basis, quiet-decline→cash) with referee coverage and the pre-registered dual-benchmark A/B.

**Architecture:** One new module `src/engine_v2/options/regime_router.py` wrapping the solo wheel mechanics (verbatim ordering, imported helpers) in a posture dispatch — same pattern as `portfolio.py`. All-chop states must reproduce solo `run_wheel(call_min_strike="basis")` byte-identically. Referee gains `--router`; runner produces the dual-benchmark report.

**Tech Stack:** Python/pandas/pytest, repo `~/Documents/Trading/code/etf-bot`, `PYTHONPATH=. .venv/bin/python -m pytest`.

## Global Constraints (verbatim from spec)

- Routing table + Approach A transitions exactly as spec'd; zero new tunables; EOD fills.
- Frozen config: 20Δ both legs, 7 DTE target, 50% TP, $100k, `call_min_strike="basis"`, cash yield 0.
- State reads strictly-prior-day, 14-day staleness; unknown → WHEEL + `route_state_unknown` warning.
- Trend shares: 100-lots, `lots = cash // (spot*100)`, fill at EOD spot, actions `BUY_SHARES`/`SELL_SHARES` (`contract=None`).
- Trend shares sold ONLY on trend=="downtrend" strictly-prior state (chop keeps + converts to wheel shares at purchase basis). Wheel shares never regime-sold. Covered calls opened only when the day's cell is a WHEEL cell.
- Transitions resolve before entries within a day; same-day redeploy after `SELL_SHARES` is legal.
- Universe SPY GDX SLV XOP; XOP from 2020-07-01; unseen tickers hard-refused everywhere.
- Coverage ≥95% engine_v2; solo/`portfolio` suites untouched.

---

### Task 1: Router skeleton + WHEEL posture (wheel-equivalence anchor)

**Files:**
- Create: `src/engine_v2/options/regime_router.py`
- Test: `tests/engine_v2/options/test_regime_router.py`

**Interfaces:**
- Produces: `run_regime_router(chain, cfg, regime_states) -> RouterResult`; `RouterResult(equity, trades, final_cash, final_shares, residual_settled, warnings, route_log, days_in_posture)`; internal `_cell(states, d) -> ("TREND"|"WHEEL"|"CASH", trend, vol, unknown_flag)`.

- [ ] **Step 1: failing tests** — fixtures copied from `test_portfolio.py` style (`_chain`, `_cfg` with `call_min_strike="basis"`, `_states(rows)` of `(date, trend, vol, vol_pctile)`):

```python
def test_all_chop_states_byte_identical_to_solo_wheel():
    ch = pd.read_parquet("data/options/spy_greeks_eod_all.parquet")
    dates = pd.to_datetime(ch["date"]).sort_values().unique()
    st = pd.DataFrame({"trend": "chop", "vol": "normal", "vol_pctile": 0.5},
                      index=pd.DatetimeIndex(dates) - pd.Timedelta(days=1))
    cfg = WheelConfig(ticker="SPY", put_delta=0.20, call_delta=0.20, target_dte=7,
                      take_profit_pct=0.50, starting_capital=100_000.0,
                      call_min_strike="basis")
    solo = run_wheel(ch, cfg)
    rt = run_regime_router(ch, cfg, st)
    assert [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in solo.trades] == \
           [(t.date, t.action, t.contracts, t.price_per_contract, t.cash_after)
            for t in rt.trades]
    assert solo.equity.equals(rt.equity) and solo.final_cash == rt.final_cash

def test_missing_states_raises():
    with pytest.raises(ValueError):
        run_regime_router(_chain(PUT_DAY), _cfg(), None)

def test_solo_only_flags_raise():
    st = _states([("2024-01-01","chop","normal",0.5)])
    for kw in ({"roll_tested_puts": True}, {"put_stop_mult": 3.0},
               {"regime_entry_gate": True}, {"liquidate_assignment": True}):
        with pytest.raises(ValueError):
            run_regime_router(_chain(PUT_DAY), _cfg(**kw), st)

def test_unknown_state_routes_to_wheel_and_warns():
    st = _states([("2023-06-01","chop","normal",0.5)])   # stale >14d
    res = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    assert [t for t in res.trades if t.action == "SELL_PUT"]
    assert any(w[1] == "route_state_unknown" for w in res.warnings)
```

- [ ] **Step 2: run, confirm ImportError.**
- [ ] **Step 3: implement.** Module skeleton: validation (`regime_states is None` → ValueError; any of `roll_tested_puts / put_stop_mult / liquidate_assignment / any_regime_gate` → ValueError); `_cell` from `_state_before`; day loop that for v1 of this task ONLY handles the WHEEL posture — a faithful transplant of the solo loop's plain+basis path (TP EOD → expiry resolution with late-gap rule → covered-call entry with basis floor → put entry), with the put/call ENTRY steps guarded by `cell in ("WHEEL",)` or unknown, and `route_log`/`days_in_posture` recorded daily. Structure copied from `portfolio.py`'s manage block (same field names: `short`, `shares`, `phase`, `basis`, `premium`). Cash-yield accrual, `closed_today` churn block, residual settle at end — all verbatim solo semantics.
- [ ] **Step 4: tests pass** (all-chop equivalence is the gate — exact).
- [ ] **Step 5: commit** `feat: regime router skeleton — WHEEL posture byte-identical to solo wheel+basis`

### Task 2: TREND + CASH cells

**Files:** same.

**Interfaces:**
- Produces: `BUY_SHARES`/`SELL_SHARES` trades (`contract=None`, price=EOD spot, 100-lots); `days_in_posture` counting TREND/WHEEL/CASH.

- [ ] **Step 1: failing tests**

```python
UP = [("2024-01-01","uptrend","calm",0.2)]
def test_uptrend_buys_100_lots_at_eod_spot():
    res = run_regime_router(_chain(PUT_DAY), _cfg(), _states(UP))
    buys = [t for t in res.trades if t.action == "BUY_SHARES"]
    assert buys and buys[0].price_per_contract == 472.0
    assert buys[0].contracts == int(50_000 // (472.0 * 100))   # lots
    assert res.final_cash == pytest.approx(50_000 - buys[0].contracts * 100 * 472.0)

def test_uptrend_holds_no_calls_written():
    rows = PUT_DAY + [["2024-01-03","2024-01-10",7,470,"C",2.0,2.1,2.05,2.05,0.30,0.1,474.0]]
    res = run_regime_router(_chain(rows), _cfg(), _states(UP))
    assert not [t for t in res.trades if t.action == "SELL_CALL"]

def test_cash_cell_no_entries_days_counted():
    st = _states([("2024-01-01","downtrend","normal",0.5)])
    res = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    assert not [t for t in res.trades if t.action in ("SELL_PUT","BUY_SHARES")]
    assert res.days_in_posture["CASH"] == 1
```

- [ ] **Step 2: red.** **Step 3:** entry dispatch on flat: cell TREND → buy lots (own campaign id); cell CASH → count; equity marks include trend shares at spot. **Step 4: green + suite.** **Step 5: commit** `feat: router TREND and CASH cells`

### Task 3: Border transitions (the four edges + sequencing)

**Files:** same.

- [ ] **Step 1: failing tests** — synthetic multi-day chains per edge:

```python
def test_uptrend_to_downtrend_sells_next_close():
    rows = [PUT_DAY[0],
            ["2024-01-03","2024-01-10",7,470,"P",2.0,2.1,2.05,2.05,-0.30,0.1,468.0]]
    st = _states([("2024-01-01","uptrend","calm",0.2),
                  ("2024-01-02","downtrend","normal",0.5)])  # flip seen from 01-03
    res = run_regime_router(_chain(rows), _cfg(), st)
    acts = [t.action for t in res.trades]
    assert acts[0] == "BUY_SHARES"
    sells = [t for t in res.trades if t.action == "SELL_SHARES"]
    assert sells and pd.Timestamp(sells[0].date) == pd.Timestamp("2024-01-03")
    assert sells[0].price_per_contract == 468.0   # that day's spot

def test_uptrend_to_chop_keeps_shares_starts_covered_calls_at_purchase_basis():
    rows = [PUT_DAY[0],
            # chop day offers a call BELOW purchase price 472 and one above
            ["2024-01-03","2024-01-10",7,465,"C",3.0,3.1,3.05,3.05,0.30,0.1,470.0],
            ["2024-01-03","2024-01-10",7,475,"C",0.5,0.6,0.55,0.55,0.05,0.1,470.0]]
    st = _states([("2024-01-01","uptrend","calm",0.2),
                  ("2024-01-02","chop","normal",0.5)])
    res = run_regime_router(_chain(rows), _cfg(), st)
    assert not [t for t in res.trades if t.action == "SELL_SHARES"]
    calls = [t for t in res.trades if t.action == "SELL_CALL"]
    assert calls and calls[0].contract.strike >= 472.0   # basis floor = purchase px

def test_chop_wheel_campaign_survives_flip_to_expiry():
    rows = [PUT_DAY[0],
            ["2024-01-03","2024-01-09",6,470,"P",2.0,2.1,2.05,2.05,-0.30,0.1,472.0],
            ["2024-01-09","2024-01-09",0,470,"P",0.05,0.10,0.07,0.07,-0.01,0.1,475.0]]
    st = _states([("2024-01-01","chop","normal",0.5),
                  ("2024-01-02","uptrend","calm",0.2)])   # flip mid-campaign
    res = run_regime_router(_chain(rows), _cfg(), st)
    acts = [t.action for t in res.trades]
    assert "SELL_PUT" in acts and "PUT_EXPIRED" in acts   # ran to natural end

def test_panic_assignment_then_uptrend_flip_keeps_shares_no_calls():
    rows = [
        ["2024-01-02","2024-01-09",7,470,"P",2.0,2.1,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-09","2024-01-09",0,470,"P",5.0,5.1,5.05,5.05,-0.99,0.1,465.0],
        ["2024-01-10","2024-01-17",7,475,"C",1.0,1.1,1.05,1.05,0.30,0.1,466.0]]
    st = _states([("2024-01-01","downtrend","stressed",0.9),
                  ("2024-01-09","uptrend","calm",0.2)])
    res = run_regime_router(_chain(rows), _cfg(), st)
    assert [t for t in res.trades if t.action == "ASSIGNED"]
    assert not [t for t in res.trades if t.action in ("SELL_CALL","SELL_SHARES")]

def test_same_day_state_flip_ignored():
    st = _states([("2024-01-01","uptrend","calm",0.2),
                  ("2024-01-02","downtrend","normal",0.5)])
    res = run_regime_router(_chain(PUT_DAY), _cfg(), st)
    assert [t for t in res.trades if t.action == "BUY_SHARES"]   # prior-day rules

def test_future_state_rows_do_not_change_decisions():
    a = run_regime_router(_chain(PUT_DAY), _cfg(), _states(UP))
    b = run_regime_router(_chain(PUT_DAY), _cfg(),
                          _states(UP + [("2024-06-01","downtrend","normal",0.5)]))
    assert [(t.date, t.action) for t in a.trades] == [(t.date, t.action) for t in b.trades]
```

- [ ] **Step 2: red.** **Step 3:** transitions before entries: trend-shares + prior-day trend=="downtrend" → `SELL_SHARES` at today's spot (may redeploy same day); trend-shares + chop → convert in place to wheel shares (`phase="CALL"`, `basis=purchase_px`, premium continues accruing under same campaign); wheel-shares + TREND cell → covered-call entry suppressed (call opening requires WHEEL cell). **Step 4: green + full suite (incl. Task 1 anchor re-run).** **Step 5: commit** `feat: router border transitions (approach A)`

### Task 4: Runner + report

**Files:**
- Create: `scripts/run_regime_router.py`

Runner mirrors `run_portfolio_rotation.py`: for each of SPY GDX SLV XOP (XOP chain filtered ≥2020-07-01): build `regime_series(closes_for(t))`, run router; run solo `run_wheel` basis same window (benchmark 2); `buy_hold_curve` (benchmark 1); print/write per-ticker block with P&L/total/CAGR/Sharpe/maxDD (reuse `metrics_simple` via the portfolio runner's `perf` helper), per-year returns (`m.yearly_returns`), `days_in_posture`, transition count (route_log posture changes), whipsaw count (`SELL_SHARES` ≤10 trading days after its `BUY_SHARES` — count via equity index positions), state-unknown count. Output `data/options/reports/regime_router.txt`. UNSEEN set hard-refused (copy guard from `run_regime_gates.py`).

- [ ] Implement → run on SPY only for smoke → full run deferred to Task 6. Commit `feat: regime-router A/B runner (dual benchmarks)`

### Task 5: Referee `--router`

**Files:**
- Modify: `scripts/audit_defense_execution.py`

`audit_router()`: per ticker, rebuild inputs exactly as the runner, run router; then with LOCAL `_asof`/`_unpaid` (already present) plus a local cell function (`uptrend→TREND; downtrend+stressed→WHEEL; downtrend else→CASH; chop/None→WHEEL`):
1. every `BUY_SHARES` on derived-TREND day with no shares held; every `SELL_SHARES` on a day whose derived trend=="downtrend" AND shares were trend-provenance (reconstruct provenance: shares from `BUY_SHARES` = trend; from `ASSIGNED` = wheel);
2. every `SELL_PUT`/`SELL_CALL` on derived WHEEL-cell days;
3. leg walk TP/expiry per existing portfolio-audit logic (option trades filtered `contract is not None`);
4. double-entry: derived forced-sale day (trend shares held + derived downtrend) without `SELL_SHARES` = mismatch;
5. print verified counts; exit 1 on mismatch.
- [ ] Run `--router`, expect exit 0 on all four tickers. Commit `feat: execution audit covers regime router`

### Task 6: A/B run, review, merge, vault

- [ ] Full suite + coverage ≥95%; `--router` exit 0.
- [ ] `scripts/run_regime_router.py` full four-ticker run → report.
- [ ] code-review (high) on branch diff; fix verified findings; re-run suite + referee.
- [ ] Merge to main; update vault `09 Regime Bot/_STATUS.md` (built + verdicts), log entry, memory; ELI10 the results to owner (standing rule).
