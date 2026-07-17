# Universal Wheel Dashboard Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-ticker Wheel dashboard page with the universal chop-scanner rotation bot, with an honest twin win-rate summary.

**Architecture:** A new `dashboard/views/universal_wheel.py` takes the "Wheel" nav slot and runs `run_portfolio_wheel(..., selector="chop", n_slots=N)` over the fixed 9-ticker universe. Two new pure helpers in `report.py` — `portfolio_campaign_table` (per-campaign P&L, portfolio-aware) and `portfolio_summary_stats` (twin win-rate + tile numbers) — feed the summary. The old `wheel.py` view is removed.

**Tech Stack:** Python 3, Streamlit, Plotly, pandas, pytest, `streamlit.testing.v1.AppTest`.

## Global Constraints

- Run tests with `PYTHONPATH=. .venv/bin/python -m pytest` — the `.venv/bin/pytest` shebang is broken.
- `selector="chop"` and `call_min_strike="basis"` are always on (not knobs). Universe = `ROTATION_TIE_ORDER` (9 tickers), shown read-only.
- Reserved five (XBI EEM EWZ TLT ARKK) must never appear on the page; the engine refuses them regardless.
- Twin win-rate: **finished-rentals win %** = of campaigns with `open_at_end == False`, share with `pnl_realized > 0`; **sold-today win %** = of ALL campaigns, share with `pnl_mtm > 0`.
- `run_portfolio_wheel(chains, cfg, regime_states, clean_start=None, selector="vol_pctile", n_slots=1) -> PortfolioResult` with fields `equity, trades, final_cash, final_shares (dict), days_flat, n_campaigns_opened, route_events`.
- `Trade(date, action, contract, contracts, price_per_contract, cash_after, campaign_id=0)`; `Contract(root, expiry, strike, right)` (frozen). `contract_multiplier=100`, `commission_per_contract=0.65`.
- `report.py` already imports `from dataclasses import dataclass`, `import pandas as pd`, `from ..backtest import metrics_simple as m`.
- Branch: `universal-wheel-page`. Spec: `docs/superpowers/specs/2026-07-17-universal-wheel-page-design.md`.

---

### Task 1: `portfolio_campaign_table` helper

**Files:**
- Modify: `src/engine_v2/options/report.py` (append the function)
- Test: `tests/engine_v2/options/test_portfolio_campaign_table.py`

**Interfaces:**
- Consumes: a `PortfolioResult` (uses `.trades`), a `WheelConfig` (uses `contract_multiplier`, `commission_per_contract`), and `last_spots: dict[str, float]`.
- Produces: `portfolio_campaign_table(result, cfg, last_spots) -> pd.DataFrame` with columns `campaign_id, ticker, opened, closed, n_trades, pnl_realized, shares_held, collateral, open_at_end, pnl_mtm, pct_return`. Groups trades by `campaign_id` (interleaving-safe).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_portfolio_campaign_table.py
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.wheel import Trade, WheelConfig
from src.engine_v2.options.portfolio import PortfolioResult
from src.engine_v2.options.report import portfolio_campaign_table

MULT, COMM = 100, 0.65


def _cfg():
    return WheelConfig(ticker="SPY", put_delta=0.20, call_delta=0.50,
                       target_dte=7, take_profit_pct=0.50, call_min_strike="basis")


def _result():
    d = pd.Timestamp("2021-01-04")
    put1 = Contract("SPY", pd.Timestamp("2021-01-15"), 400.0, "P")
    put2 = Contract("GDX", pd.Timestamp("2021-01-15"), 30.0, "P")
    trades = [
        # campaign 1: SPY put sold for 2.00, expires worthless -> closed winner
        Trade(d, "SELL_PUT", put1, 1, 2.00, 100_000.0, campaign_id=1),
        Trade(pd.Timestamp("2021-01-15"), "PUT_EXPIRED", put1, 1, 0.0, 100_000.0, campaign_id=1),
        # campaign 2: GDX put sold for 1.00, assigned at 30, still holding -> open
        Trade(d, "SELL_PUT", put2, 1, 1.00, 100_000.0, campaign_id=2),
        Trade(pd.Timestamp("2021-01-15"), "ASSIGNED", put2, 1, 30.0, 97_000.0, campaign_id=2),
    ]
    return PortfolioResult(pd.Series({d: 100_000.0}), trades, 97_000.0,
                           {"GDX": 100}, n_campaigns_opened=2)


def test_two_campaigns_one_closed_one_open():
    ct = portfolio_campaign_table(_result(), _cfg(), {"SPY": 405.0, "GDX": 25.0})
    assert list(ct["campaign_id"]) == [1, 2]
    c1 = ct[ct.campaign_id == 1].iloc[0]
    c2 = ct[ct.campaign_id == 2].iloc[0]
    # campaign 1: premium in, expired worthless -> realized = 2*100 - 0.65
    assert abs(c1["pnl_realized"] - (2.00 * MULT - COMM)) < 1e-9
    assert c1["open_at_end"] == False
    assert c1["shares_held"] == 0
    assert abs(c1["collateral"] - 400.0 * MULT) < 1e-9
    # campaign 2: premium 1*100-0.65, then assigned -3000, holding 100 shares
    assert abs(c2["pnl_realized"] - (1.00 * MULT - COMM - 30.0 * MULT)) < 1e-9
    assert c2["open_at_end"] == True
    assert c2["shares_held"] == 100
    # pnl_mtm marks the 100 GDX shares at last_spot 25
    assert abs(c2["pnl_mtm"] - (c2["pnl_realized"] + 100 * 25.0)) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_campaign_table.py -v`
Expected: FAIL — `cannot import name 'portfolio_campaign_table'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/engine_v2/options/report.py`:

```python
def portfolio_campaign_table(result, cfg, last_spots) -> pd.DataFrame:
    """One row per campaign for a PortfolioResult. campaign_table can't be
    reused: portfolio campaigns interleave across tickers by date (not
    contiguous) and final_shares is a dict. Groups by campaign_id. pnl_realized
    is exact cash flow; pnl_mtm marks any still-held shares at last_spots."""
    mult, comm = cfg.contract_multiplier, cfg.commission_per_contract
    agg = {}
    for t in result.trades:
        cid = t.campaign_id
        c = agg.get(cid)
        if c is None:
            c = agg[cid] = dict(campaign_id=cid, ticker=t.contract.root,
                                opened=t.date, closed=t.date, n_trades=0,
                                pnl_realized=0.0, shares_held=0, collateral=0.0)
        c["n_trades"] += 1
        c["closed"] = t.date
        a, n, px = t.action, t.contracts, t.price_per_contract
        if a in ("SELL_PUT", "SELL_CALL"):
            c["pnl_realized"] += px * mult * n - comm * n
            if a == "SELL_PUT" and c["collateral"] == 0.0:
                c["collateral"] = t.contract.strike * mult * n
        elif a in ("CLOSE_PUT", "CLOSE_CALL"):
            c["pnl_realized"] -= px * mult * n + comm * n
        elif a == "ASSIGNED":
            c["pnl_realized"] -= t.contract.strike * mult * n
            c["shares_held"] += mult * n
        elif a == "CALLED_AWAY":
            c["pnl_realized"] += t.contract.strike * mult * n
            c["shares_held"] -= mult * n
    rows = []
    for c in agg.values():
        held = c["shares_held"]
        c["open_at_end"] = held > 0
        c["pnl_mtm"] = c["pnl_realized"] + held * last_spots.get(c["ticker"], 0.0)
        c["pct_return"] = c["pnl_realized"] / c["collateral"] if c["collateral"] else 0.0
        rows.append(c)
    return pd.DataFrame(rows, columns=["campaign_id", "ticker", "opened", "closed",
        "n_trades", "pnl_realized", "shares_held", "collateral", "open_at_end",
        "pnl_mtm", "pct_return"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_campaign_table.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/test_portfolio_campaign_table.py
git commit -m "feat(report): portfolio_campaign_table — per-campaign P&L, interleave-safe"
```

---

### Task 2: `portfolio_summary_stats` (twin win-rate)

**Files:**
- Modify: `src/engine_v2/options/report.py` (append the function)
- Test: `tests/engine_v2/options/test_portfolio_summary_stats.py`

**Interfaces:**
- Consumes: the DataFrame from `portfolio_campaign_table` (Task 1).
- Produces: `portfolio_summary_stats(campaigns) -> dict` with keys `finished_win_rate, soldtoday_win_rate, avg_pct_per_win, n_open, n_campaigns`.
  - `finished_win_rate` = of `open_at_end == False` campaigns, share with `pnl_realized > 0` (NaN if none closed).
  - `soldtoday_win_rate` = of ALL campaigns, share with `pnl_mtm > 0`.
  - `avg_pct_per_win` = mean `pct_return` over closed winners (NaN if none).
  - `n_open` = count `open_at_end == True`; `n_campaigns` = row count.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_portfolio_summary_stats.py
import math
import pandas as pd
from src.engine_v2.options.report import portfolio_summary_stats


def _campaigns():
    # one closed winner, one open-and-underwater (pnl_mtm < 0)
    return pd.DataFrame([
        dict(campaign_id=1, open_at_end=False, pnl_realized=199.35, pnl_mtm=199.35, pct_return=0.005),
        dict(campaign_id=2, open_at_end=True, pnl_realized=-2900.65, pnl_mtm=-400.65, pct_return=-0.9),
    ])


def test_twin_win_rates_diverge():
    s = portfolio_summary_stats(_campaigns())
    # finished: only campaign 1 is closed, and it won -> 100%
    assert s["finished_win_rate"] == 1.0
    # sold-today: campaign 1 pnl_mtm>0 (win), campaign 2 pnl_mtm<0 (loss) -> 50%
    assert s["soldtoday_win_rate"] == 0.5
    assert s["n_open"] == 1
    assert s["n_campaigns"] == 2
    # avg % per win = pct_return of the single closed winner
    assert abs(s["avg_pct_per_win"] - 0.005) < 1e-9


def test_all_closed_winners_gap_is_zero():
    df = pd.DataFrame([
        dict(campaign_id=1, open_at_end=False, pnl_realized=100.0, pnl_mtm=100.0, pct_return=0.01),
        dict(campaign_id=2, open_at_end=False, pnl_realized=50.0, pnl_mtm=50.0, pct_return=0.02),
    ])
    s = portfolio_summary_stats(df)
    assert s["finished_win_rate"] == 1.0
    assert s["soldtoday_win_rate"] == 1.0   # no hidden losses -> numbers agree
    assert s["n_open"] == 0


def test_no_closed_campaigns_finished_rate_is_nan():
    df = pd.DataFrame([
        dict(campaign_id=1, open_at_end=True, pnl_realized=-10.0, pnl_mtm=5.0, pct_return=-0.1),
    ])
    s = portfolio_summary_stats(df)
    assert math.isnan(s["finished_win_rate"])
    assert math.isnan(s["avg_pct_per_win"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_summary_stats.py -v`
Expected: FAIL — `cannot import name 'portfolio_summary_stats'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/engine_v2/options/report.py`:

```python
def portfolio_summary_stats(campaigns) -> dict:
    """Twin win-rate + tile numbers from a portfolio_campaign_table frame.
    finished_win_rate hides nothing dishonestly (closed campaigns only);
    soldtoday_win_rate marks open positions to market so hidden losses surface.
    The gap between them is the honesty signal."""
    closed = campaigns[~campaigns["open_at_end"]]
    winners = closed[closed["pnl_realized"] > 0]
    return {
        "finished_win_rate": float((closed["pnl_realized"] > 0).mean()) if len(closed) else float("nan"),
        "soldtoday_win_rate": float((campaigns["pnl_mtm"] > 0).mean()) if len(campaigns) else float("nan"),
        "avg_pct_per_win": float(winners["pct_return"].mean()) if len(winners) else float("nan"),
        "n_open": int(campaigns["open_at_end"].sum()),
        "n_campaigns": int(len(campaigns)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_summary_stats.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/report.py tests/engine_v2/options/test_portfolio_summary_stats.py
git commit -m "feat(report): portfolio_summary_stats — twin win-rate (finished vs sold-today)"
```

---

### Task 3: Universal wheel page + nav repoint + remove old wheel

**Files:**
- Create: `dashboard/views/universal_wheel.py`
- Delete: `dashboard/views/wheel.py`
- Modify: `dashboard/app.py` (Wheel nav slot)
- Delete: `tests/test_wheel_dashboard.py` (single-ticker wheel page test — replaced)
- Test: `tests/test_universal_wheel_dashboard.py`

**Interfaces:**
- Consumes: `run_portfolio_wheel`, `ROTATION_TIE_ORDER` (portfolio.py); `portfolio_campaign_table`, `portfolio_summary_stats` (Tasks 1-2); `charts.equity_curve`; `labels.humanize`; `regime_series`, `closes_for`; `WheelConfig`; `chain_path`; `metrics_simple as m`.
- Produces: `dashboard/views/universal_wheel.py::render`; the Wheel nav slot points to it.

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/test_universal_wheel_dashboard.py
from streamlit.testing.v1 import AppTest


def test_universal_wheel_page_runs_and_summarizes():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    assert not at.exception
    at.switch_page("views/universal_wheel.py").run(timeout=60)
    assert not at.exception
    # narrow the window so the smoke run is fast, then run
    at.date_input(key="uw_start").set_value(__import__("datetime").date(2021, 1, 4)).run(timeout=60)
    at.date_input(key="uw_end").set_value(__import__("datetime").date(2021, 6, 30)).run(timeout=60)
    at.button(key="run_uw").click().run(timeout=180)
    assert not at.exception
    assert len(at.metric) >= 4     # P&L / finished-win / sold-today-win / avg%


def test_universal_wheel_no_reserved_tickers_on_page():
    at = AppTest.from_file("dashboard/app.py").run(timeout=60)
    at.switch_page("views/universal_wheel.py").run(timeout=60)
    assert not at.exception
    # the universe banner + any markdown must not name a reserved ticker
    blob = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    for reserved in ["XBI", "EEM", "EWZ", "TLT", "ARKK"]:
        assert reserved not in blob
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_universal_wheel_dashboard.py -v`
Expected: FAIL — `switch_page("views/universal_wheel.py")` errors (page absent).

- [ ] **Step 3: Write the page**

```python
# dashboard/views/universal_wheel.py
"""Universal wheel: the chop-scanner rotation bot. No ticker picker — it scans
the fixed 9-ticker universe and rents the plain+basis wheel on whatever is in
good chop weather, N at a time. Replaces the single-ticker wheel page."""
import pandas as pd
import streamlit as st
from dashboard import charts, labels
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.select import derived_band
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                             DEFAULT_CLEAN_START)
from src.engine_v2.options.report import portfolio_campaign_table, portfolio_summary_stats
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for
from src.engine_v2.backtest import metrics_simple as m

UNIVERSE = list(ROTATION_TIE_ORDER)
XOP_CLEAN_START = pd.Timestamp("2020-07-01")


@st.cache_data(ttl=3600, show_spinner=False)
def _load():
    chains = {}
    for t in UNIVERSE:
        ch = pd.read_parquet(chain_path(t)); ch["date"] = pd.to_datetime(ch["date"])
        chains[t] = ch
    states = {t: regime_series(closes_for(t)) for t in UNIVERSE}
    return chains, states


def render():
    st.title("Universal wheel — chop scanner")
    st.caption("Scans the whole universe and rents the plain+basis wheel on "
               "whatever is in good chop weather (range-bound, not stressed), "
               "N tickers at a time. Exploration only.")
    st.markdown("**Universe (fixed):** " + " · ".join(UNIVERSE))

    chains, states = _load()
    all_dates = sorted({d for t in UNIVERSE for d in chains[t]["date"]})
    min_d, max_d = all_dates[0].date(), all_dates[-1].date()

    dc1, dc2 = st.columns(2)
    start = dc1.date_input("Start", value=min_d, min_value=min_d, max_value=max_d, key="uw_start")
    end = dc2.date_input("End", value=max_d, min_value=min_d, max_value=max_d, key="uw_end")

    c1, c2, c3 = st.columns(3)
    put_delta = c1.number_input("Put delta", 0.05, 0.50, 0.20, 0.05, key="uw_pd")
    call_delta = c1.number_input("Call delta", 0.05, 0.50, 0.50, 0.05, key="uw_cd")
    target_dte = c2.number_input("Target DTE", 5, 60, 7, key="uw_tdte")
    band_lo, band_hi = derived_band(int(target_dte))
    c2.caption(f"trades expiries {band_lo}–{band_hi} days out")
    tp = c3.slider("Take-profit (% of credit)", 1, 100, 50, key="uw_tp")
    capital = c3.number_input("Capital", 10_000, 1_000_000, 100_000, 10_000, key="uw_cap")
    n_slots = st.slider("How many tickers at once (N)", 1, len(UNIVERSE), 5, key="uw_n")

    if pd.Timestamp(start) < XOP_CLEAN_START:
        st.caption("Note: XOP is split-broken before 2020-07-01; the engine clean-starts "
                   "it at 2020-07-01 automatically.")

    if st.button("Run", key="run_uw", type="primary"):
        w = {t: chains[t][(chains[t]["date"] >= pd.Timestamp(start))
                          & (chains[t]["date"] <= pd.Timestamp(end))] for t in UNIVERSE}
        if sum(len(w[t]["date"].unique()) for t in UNIVERSE) < 5:
            st.warning("Window too short."); st.stop()
        cfg = WheelConfig(ticker="SPY", put_delta=put_delta, call_delta=call_delta,
                          target_dte=int(target_dte), take_profit_pct=tp / 100.0,
                          starting_capital=float(capital), call_min_strike="basis")
        res = run_portfolio_wheel(w, cfg, states, selector="chop", n_slots=int(n_slots))
        last_spots = {t: float(w[t].groupby("date")["underlying"].first().iloc[-1])
                      for t in UNIVERSE if len(w[t])}
        st.session_state["_uw"] = (res, cfg, last_spots)

    stashed = st.session_state.get("_uw")
    if stashed is None:
        return
    res, cfg, last_spots = stashed
    ct = portfolio_campaign_table(res, cfg, last_spots)
    s = portfolio_summary_stats(ct)
    pnl = res.equity.iloc[-1] - cfg.starting_capital
    idle = res.days_flat / len(res.equity) if len(res.equity) else 0.0

    a, b, c, d = st.columns(4)
    a.metric("P&L", f"${pnl:,.0f}", f"{res.equity.iloc[-1]/cfg.starting_capital-1:+.1%}")
    b.metric("Finished-rentals win %",
             "—" if pd.isna(s["finished_win_rate"]) else f"{s['finished_win_rate']:.0%}")
    c.metric("Sold-today win %",
             "—" if pd.isna(s["soldtoday_win_rate"]) else f"{s['soldtoday_win_rate']:.0%}")
    d.metric("Avg % per win",
             "—" if pd.isna(s["avg_pct_per_win"]) else f"{s['avg_pct_per_win']:+.2%}")
    st.caption("The gap between the two win rates is money hidden in shares the bot "
               "is still holding — if they're equal, nothing's hidden. "
               f"({s['n_open']} campaigns still open.)")

    e, f, g, h = st.columns(4)
    e.metric("# campaigns", f"{res.n_campaigns_opened:,}")
    f.metric("# trades", f"{len(res.trades):,}")
    g.metric("Max drawdown", f"{m.max_drawdown(res.equity):.1%}")
    h.metric("Idle % (cash)", f"{idle:.0%}")

    st.subheader("Account value")
    st.plotly_chart(charts.equity_curve(res.equity, {}), width="stretch")

    st.subheader("Rentals by ticker")
    per = ct.groupby("ticker").size().sort_values(ascending=False)
    st.dataframe(per.rename("campaigns").reset_index(), width="stretch", hide_index=True)

    st.subheader("Campaign blotter")
    disp = ct.sort_values("opened", ascending=False).copy()
    for col in ("opened", "closed"):
        disp[col] = pd.to_datetime(disp[col]).dt.strftime("%Y-%m-%d")
    disp["pnl_realized"] = disp["pnl_realized"].map(lambda x: f"${x:,.0f}")
    disp["pct_return"] = disp["pct_return"].map(lambda x: f"{x:+.1%}")
    disp["status"] = disp["open_at_end"].map(lambda o: "open (holding shares)" if o else "closed")
    st.dataframe(labels.humanize(disp[["ticker", "opened", "closed", "n_trades",
                 "pnl_realized", "pct_return", "status"]]),
                 width="stretch", hide_index=True)
```

- [ ] **Step 4: Repoint the nav and delete the old wheel view**

In `dashboard/app.py`, change the import and the Wheel page:

```python
from dashboard.views import chameleon, regime, run, universal_wheel, wheel_history
# ...
    st.Page(universal_wheel.render, title="Wheel", url_path="wheel"),
```

(Remove `wheel` from the import list and the old `st.Page(wheel.render, ...)` line.)

Then delete the replaced files:

```bash
git rm dashboard/views/wheel.py tests/test_wheel_dashboard.py
```

- [ ] **Step 5: Run the page tests**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_universal_wheel_dashboard.py -v`
Expected: PASS (2 tests) — page runs a rotation result and shows ≥4 tiles; no reserved ticker appears.

- [ ] **Step 6: Commit**

```bash
git add dashboard/views/universal_wheel.py dashboard/app.py tests/test_universal_wheel_dashboard.py
git commit -m "feat(dashboard): universal wheel page replaces the single-ticker wheel"
```

---

### Task 4: Full-suite regression

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: all green. Specifically confirm the History page tests still pass (the old `wheel.py` removal did not break `dashboard.wheel_history`).

- [ ] **Step 2: Manual smoke (optional but recommended)**

Run: `.venv/bin/python -m streamlit run dashboard/app.py`
Check: the "Wheel" tab is now the universal scanner (no ticker picker), a run over a short window renders the twin win-rate tiles + campaign blotter, and changing N re-runs.

- [ ] **Step 3: Commit any residual cleanup**

```bash
git add -A && git commit -m "chore: universal wheel page — suite green" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- `portfolio_campaign_table` (interleave-safe, per-campaign P&L, open/mtm) → Task 1. ✓
- `portfolio_summary_stats` twin win-rate → Task 2. ✓
- Universal page: read-only universe, knobs + N slider + dates, selector=chop/basis frozen, run_portfolio_wheel → Task 3. ✓
- Summary tiles (P&L, twin win %, avg % per win, #campaigns, #trades, maxDD, idle %) + gap caption → Task 3. ✓
- Equity curve, per-ticker entries, campaign blotter → Task 3. ✓
- Nav repoint + remove old wheel.py + its test → Task 3. ✓
- Tests: campaign-table unit, twin win-rate unit, page smoke, reserved-unreachable, removal regression → Tasks 1,2,3,4. ✓
- Reserved five never on page / refused → Task 3 (no picker, fixed universe) + test. ✓
- History deferred (no logging) — page simply doesn't call log_run; History unaffected → Task 4 confirms. ✓

**Placeholder scan:** No TBD/TODO; every code step complete; every test asserts real values.

**Type consistency:** `portfolio_campaign_table(result, cfg, last_spots)` columns match between Task 1 definition and Task 3 use; `portfolio_summary_stats(campaigns)` keys (`finished_win_rate`, `soldtoday_win_rate`, `avg_pct_per_win`, `n_open`, `n_campaigns`) consistent Tasks 2/3; `run_portfolio_wheel(..., selector="chop", n_slots=)` consistent; `charts.equity_curve(equity, {})` matches its `(equity, benchmarks)` signature.

**One risk flagged for the executor:** Task 3's smoke test narrows the date window to make the full-universe run fast; if `AppTest` cannot set `uw_start`/`uw_end` before the run completes in time, raise the `run_uw` click timeout (already 180s) rather than widening the window. The page's `_load()` is `@st.cache_data` so repeated reruns within a test don't re-read parquets.
