# Dashboard Basis-Default + Auto Plain-Wheel Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the wheel dashboard always use the basis floor (no defense dropdown) and show it against the plain wheel side by side on every run.

**Architecture:** Dashboard-only change to `dashboard/views/wheel.py`. Task 1 removes the defense chooser and hardcodes `call_min_strike="basis"`, deleting the now-dead gate/states code (single-arm, still renders as before). Task 2 adds a second plain-wheel run, a headline comparison table, a dual equity curve, and a history field. Engine and all `scripts/` consumers are untouched.

**Tech Stack:** Python, Streamlit, pandas, Plotly, pytest with `streamlit.testing.v1.AppTest`.

## Global Constraints

- Engine untouched: `call_min_strike` stays a `WheelConfig` field (default `None`); plain wheel stays reachable in code. No edits outside `dashboard/views/wheel.py` and `tests/test_wheel_dashboard.py`.
- `WheelConfig.call_min_strike` values: `"basis"` (floor on) or `None` (plain).
- `wheel_report(res, ch, cfg)` is called once per arm; benchmarks (`rep.benchmark_underlying`, `rep.benchmark_spy`) are arm-independent — read them from the basis arm.
- `charts.equity_curve(equity: pd.Series, benchmarks: dict)` — extra series (the plain curve) are passed as entries in the `benchmarks` dict.
- Run tests with `.venv/bin/pytest` from repo root; full suite via `make test`.
- Fixture ticker label: `"SPY (2024 sample fixture)"`; fixture path `fixtures/spy_wheel_cycle.parquet`.

---

### Task 1: Remove the defense dropdown, hardcode the basis floor

**Files:**
- Modify: `dashboard/views/wheel.py` (delete `DEFENSES`/`_HELP`/`w_defense` selectbox at lines ~97–145; hardcode cfg; drop `states` block at ~164–169; delete gate-diagnostics block at ~254–267)
- Test: `tests/test_wheel_dashboard.py`

**Interfaces:**
- Consumes: existing inputs (`w_pd`, `w_cd`, `w_tdte`, `w_tp`, `w_cap`, `wheel_start`, `wheel_end`, `intraday_tp`, ticker selectbox).
- Produces: a `cfg` built with `call_min_strike="basis"`; single-arm stash `("_wheel_result" = (res, rep, cfg, ch))` unchanged in shape for now.

- [ ] **Step 1: Write the failing test — defense selectbox is gone**

Add to `tests/test_wheel_dashboard.py`:

```python
def test_wheel_page_has_no_defense_selectbox():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    assert not at.exception
    keys = {s.key for s in at.selectbox}
    assert "w_defense" not in keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_wheel_dashboard.py::test_wheel_page_has_no_defense_selectbox -v`
Expected: FAIL (`w_defense` still present).

- [ ] **Step 3: Delete the defense menu block**

In `dashboard/views/wheel.py`, delete the entire `DEFENSES = {...}` dict, the `_HELP = {...}` dict, the stale-key reset line `if st.session_state.get("w_defense") not in DEFENSES: st.session_state.pop("w_defense", None)`, and the `defense_name = st.selectbox("Defense", ...)` widget plus its trailing `st.caption(_HELP[defense_name])` (lines ~97–145). Keep the `intra = ...` / `intraday_on = st.checkbox(...)` block that follows.

- [ ] **Step 4: Hardcode the basis floor in cfg and drop the states block**

Replace the `cfg = WheelConfig(...**DEFENSES[defense_name])` construction with:

```python
        cfg = WheelConfig(starting_capital=float(capital), put_delta=put_delta,
                          call_delta=call_delta, target_dte=int(target_dte),
                          take_profit_pct=tp_slider / 100.0, ticker=ticker,
                          call_min_strike="basis")
```

Delete the `states = None` / `if cfg.any_regime_gate:` block (no gate is ever armed now). In both run branches pass `regime_states=None`:

```python
        if intraday_on and intra:
            from src.engine_v2.options.intraday import run_wheel_intraday
            res = run_wheel_intraday(ch, cfg, pd.read_parquet(intra), regime_states=None)
        else:
            res = run_wheel(ch, cfg, regime_states=None)
```

- [ ] **Step 5: Delete the dead gate-diagnostics render block**

Delete the block starting `if getattr(rep, "gates", None) is not None:` through its final `st.caption(...)` (lines ~254–267). No gate is armed, so it can never render.

- [ ] **Step 6: Fix the history log defense field**

In the `log_run({...})` call, change `"defense": defense_name,` to `"defense": "basis",` (the run of record; `defense_name` no longer exists).

- [ ] **Step 7: Run the new + smoke tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wheel_dashboard.py -v`
Expected: PASS — `test_wheel_page_has_no_defense_selectbox` and `test_wheel_page_runs_and_renders_metrics` both green.

- [ ] **Step 8: Commit**

```bash
git add dashboard/views/wheel.py tests/test_wheel_dashboard.py
git commit -m "feat(dashboard): hardcode basis floor, remove defense dropdown"
```

---

### Task 2: Two-arm run, comparison table, dual equity curve

**Files:**
- Modify: `dashboard/views/wheel.py` (run branch: add plain arm + two-arm stash; render: comparison table, dual equity curve, basis-bound details, history field)
- Test: `tests/test_wheel_dashboard.py`

**Interfaces:**
- Consumes: `cfg` (basis) and `ch` from Task 1; `run_wheel` / `run_wheel_intraday`; `wheel_report`; `charts.equity_curve`.
- Produces: two-arm stash `("_wheel_result" = (res_basis, rep_basis, res_plain, rep_plain, cfg, ch))`; a `st.dataframe` comparison table indexed by arm name; the equity chart's `benchmarks` dict includes a `"Plain wheel"` series.

- [ ] **Step 1: Write the failing test — comparison table has basis and plain rows**

Add to `tests/test_wheel_dashboard.py`:

```python
def test_wheel_run_shows_basis_vs_plain_table():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/wheel.py").run(timeout=60)
    at.selectbox(key="wheel_data").set_value(FIXTURE_TICKER).run(timeout=60)
    at.button(key="run_wheel").click().run(timeout=120)
    assert not at.exception
    # the comparison table is a dataframe indexed by arm name
    idx = [str(x) for df in at.dataframe for x in df.value.index]
    assert "Basis wheel" in idx
    assert "Plain wheel" in idx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_wheel_dashboard.py::test_wheel_run_shows_basis_vs_plain_table -v`
Expected: FAIL (no comparison table yet; only the blotter dataframe, no "Basis wheel" index).

- [ ] **Step 3: Add the plain arm and two-arm stash**

In the Run button branch, after computing `res` (rename to `res_basis`) for the basis cfg, add a plain arm and stash both. Replace the run + stash lines with:

```python
        plain_cfg = replace(cfg, call_min_strike=None)
        if intraday_on and intra:
            from src.engine_v2.options.intraday import run_wheel_intraday
            intra_df = pd.read_parquet(intra)
            res_basis = run_wheel_intraday(ch, cfg, intra_df, regime_states=None)
            res_plain = run_wheel_intraday(ch, plain_cfg, intra_df, regime_states=None)
        else:
            res_basis = run_wheel(ch, cfg, regime_states=None)
            res_plain = run_wheel(ch, plain_cfg, regime_states=None)
        rep_basis = wheel_report(res_basis, ch, cfg)
        rep_plain = wheel_report(res_plain, ch, plain_cfg)
        st.session_state["_wheel_result"] = (res_basis, rep_basis, res_plain, rep_plain, cfg, ch)
```

Add `from dataclasses import replace` to the imports at the top of the run branch (or module top, matching existing import style). Update the `pnl`/`log_run` lines below to read from `res_basis`/`rep_basis`, and add `"plain_total_return": rep_plain.metrics["total_return"],` to the `log_run` dict.

- [ ] **Step 4: Update the render unpack + add the comparison table**

Change `res, rep, cfg, ch = stashed` to `res, rep, res_plain, rep_plain, cfg, ch = stashed` (keeping `res`/`rep` as the basis arm so the existing detail sections below need no changes). Immediately after unpacking, before the existing metric tiles, build and render the comparison table:

```python
        rows = {}
        def _arm_row(equity, m):
            return {"P&L": float(equity.iloc[-1] - cfg.starting_capital),
                    "Return": m["total_return"], "Sharpe": m["sharpe"],
                    "MaxDD": m["max_drawdown"]}
        rows["Basis wheel"] = _arm_row(res.equity, rep.metrics)
        rows["Plain wheel"] = _arm_row(res_plain.equity, rep_plain.metrics)
        bu = rep.benchmark_underlying
        rows[f"Buy-hold {rep.ticker}"] = {"P&L": None, "Return": bu["total_return"],
                                          "Sharpe": None, "MaxDD": bu.get("max_drawdown")}
        if rep.benchmark_spy is not None and rep.ticker != "SPY":
            bs = rep.benchmark_spy
            rows["Buy-hold SPY"] = {"P&L": None, "Return": bs["total_return"],
                                    "Sharpe": None, "MaxDD": bs.get("max_drawdown")}
        cmp_df = pd.DataFrame.from_dict(rows, orient="index")
        st.subheader("Basis wheel vs plain wheel")
        st.dataframe(cmp_df.style.format({
            "P&L": lambda x: "" if pd.isna(x) else f"${x:,.0f}",
            "Return": lambda x: "" if pd.isna(x) else f"{x:+.2%}",
            "Sharpe": lambda x: "" if pd.isna(x) else f"{x:.2f}",
            "MaxDD": lambda x: "" if pd.isna(x) else f"{x:.2%}"}), width="stretch")
```

- [ ] **Step 5: Overlay the plain curve on the equity chart**

In the "Equity vs buy & hold" section, add the plain arm to the `bench` dict before `charts.equity_curve` is called:

```python
        bench["Plain wheel"] = res_plain.equity.reindex(res.equity.index).ffill()
```

Place this line after the existing `bench[rep.ticker] = ...` / `bench["SPY"] = ...` assignments and before `st.plotly_chart(charts.equity_curve(res.equity, bench), ...)`.

- [ ] **Step 6: Run the new test to verify it passes**

Run: `.venv/bin/pytest tests/test_wheel_dashboard.py::test_wheel_run_shows_basis_vs_plain_table -v`
Expected: PASS — the comparison dataframe carries "Basis wheel" and "Plain wheel" index rows.

- [ ] **Step 7: Run the full dashboard test file**

Run: `.venv/bin/pytest tests/test_wheel_dashboard.py -v`
Expected: all PASS (smoke test still finds ≥4 metric tiles; comparison + no-defense tests green).

- [ ] **Step 8: Run the full suite**

Run: `make test`
Expected: entire suite green (no engine/referee/router change, so nothing else should move).

- [ ] **Step 9: Commit**

```bash
git add dashboard/views/wheel.py tests/test_wheel_dashboard.py
git commit -m "feat(dashboard): auto basis-vs-plain comparison table + dual equity curve"
```

---

## Self-Review

**Spec coverage:**
- Remove defense dropdown → Task 1 Steps 3–4. ✓
- Basis floor hardcoded every run → Task 1 Step 4. ✓
- Drop states/gate dependency → Task 1 Steps 4–5. ✓
- Two arms every run → Task 2 Step 3. ✓
- Comparison table (basis/plain/buy-hold rows, P&L/Return/Sharpe/MaxDD) → Task 2 Step 4. ✓
- Dual equity curve → Task 2 Step 5. ✓
- Detail sections bound to basis arm → Task 2 Step 4 (keeps `res`/`rep` = basis). ✓
- Delete gate diagnostics block → Task 1 Step 5. ✓
- History `defense="basis"` + `plain_total_return` → Task 1 Step 6, Task 2 Step 3. ✓
- Engine untouched → Global Constraints; only two files modified. ✓
- Tests updated (no dropdown, two-arm, comparison, gate block gone) → Task 1 Step 1, Task 2 Step 1. (Gate-block-gone is covered behaviorally by the smoke test running without exception; no dedicated assertion needed since the block is unconditionally deleted.)

**Placeholder scan:** Removed the scratch `_row`/`rep_` helper from Task 2 Step 4 — only `_arm_row` remains. No TBD/TODO. ✓

**Type consistency:** stash tuple is `(res_basis, rep_basis, res_plain, rep_plain, cfg, ch)` in Task 2 Step 3 and unpacked identically in Step 4. `_arm_row(equity, metrics)` returns the four-key dict used to build `cmp_df`. `replace(cfg, call_min_strike=None)` needs `from dataclasses import replace` (added Step 3). ✓
