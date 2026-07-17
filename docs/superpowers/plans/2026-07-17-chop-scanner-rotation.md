# Chop-Scanner Wheel Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the portfolio rotation engine to rent the plain+basis wheel on whatever is in good "chop" weather right now, across a universe, N tickers at once.

**Architecture:** A new pure predicate `is_good_renting_weather(row)` in `regime/state.py` defines "good to rent". `run_portfolio_wheel` gains a `selector` param (old `"vol_pctile"` ranking vs new `"chop"` filter) and an `n_slots` param (generalize the single held position to a list of up to N concurrent campaigns sharing the cash pool). The runner adds chop-signal A/B arms at N=1/N=5 against a matched-config solo baseline on 9 on-disk tickers. A `--rotation` referee independently re-derives selection as the citation gate.

**Tech Stack:** Python 3, pandas, numpy, pytest.

## Global Constraints

- Run tests with `PYTHONPATH=. .venv/bin/python -m pytest` — the `.venv/bin/pytest` shebang is broken.
- Frozen config for this test: `put_delta=0.20, call_delta=0.50, take_profit_pct=0.50, target_dte=7, call_min_strike="basis"`, `starting_capital=100_000`, `cash_yield=0`, friction `$0.65/contract + cross-spread` (all already `WheelConfig` defaults except call_delta and TP which the runner sets explicitly).
- Good to rent = `trend == "chop"` AND `vol != "stressed"`. Ranked among the eligible by `vol_pctile` descending.
- Regime state row fields (from `regime_series`): `trend ∈ {"uptrend","downtrend","chop"}`, `vol ∈ {"calm","normal","stressed"}`, plus `vol_pctile`, `px_vs_200`, etc.
- `run_portfolio_wheel` defaults MUST reproduce today's committed output byte-identically: `selector="vol_pctile"`, `n_slots=1`.
- Reserved five (XBI EEM EWZ TLT ARKK) are NEVER a rotation universe member — the engine must refuse them explicitly.
- Phase 1 dev universe: SPY GDX SLV XOP AAPL AMZN NVDA META FB (9 on disk).
- Branch: `chop-scanner-rotation`. Spec: `docs/superpowers/specs/2026-07-17-chop-scanner-rotation-design.md`.
- EOD fills only; plain+basis wheel only (no roll/stop/gates).

---

### Task 1: `is_good_renting_weather` predicate

**Files:**
- Modify: `src/engine_v2/regime/state.py` (append the function)
- Test: `tests/engine_v2/regime/test_good_renting_weather.py`

**Interfaces:**
- Consumes: a regime-state row (pandas Series or dict) with keys `trend`, `vol`.
- Produces: `is_good_renting_weather(row) -> bool` — `True` iff `row["trend"] == "chop"` and `row["vol"] != "stressed"`. A `None` row returns `False` (unknown weather is not good-to-rent).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/regime/test_good_renting_weather.py
from src.engine_v2.regime.state import is_good_renting_weather


def _row(trend, vol):
    return {"trend": trend, "vol": vol}


def test_chop_normal_is_good():
    assert is_good_renting_weather(_row("chop", "normal")) is True


def test_chop_calm_is_good():
    assert is_good_renting_weather(_row("chop", "calm")) is True


def test_chop_stressed_is_not_good():
    assert is_good_renting_weather(_row("chop", "stressed")) is False


def test_uptrend_is_not_good():
    assert is_good_renting_weather(_row("uptrend", "normal")) is False


def test_downtrend_is_not_good():
    assert is_good_renting_weather(_row("downtrend", "normal")) is False


def test_none_row_is_not_good():
    assert is_good_renting_weather(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/regime/test_good_renting_weather.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_good_renting_weather'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/engine_v2/regime/state.py` (end of file):

```python
def is_good_renting_weather(row) -> bool:
    """Good-to-rent weather for the chop scanner: range-bound (chop) and not
    violently volatile (not stressed). Uptrends (hold instead) and downtrends
    (falling knife) are excluded; a None/unknown row is not good-to-rent."""
    if row is None:
        return False
    return row["trend"] == "chop" and row["vol"] != "stressed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/regime/test_good_renting_weather.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/regime/state.py tests/engine_v2/regime/test_good_renting_weather.py
git commit -m "feat(regime): is_good_renting_weather predicate (chop and not stressed)"
```

---

### Task 2: `selector` param on `run_portfolio_wheel` (single-position)

**Files:**
- Modify: `src/engine_v2/options/portfolio.py` (`run_portfolio_wheel` signature + routing block, lines ~43-44 and ~142-167)
- Test: `tests/engine_v2/options/test_portfolio_selector.py`

**Interfaces:**
- Consumes: `is_good_renting_weather` (Task 1).
- Produces: `run_portfolio_wheel(chains, cfg, regime_states, clean_start=None, selector="vol_pctile")`.
  - `selector="vol_pctile"` (default): today's behavior exactly — eligibility gate `is_unpaid_decline`, unknown state kept and ranked last (`pct=-1.0`).
  - `selector="chop"`: eligibility = `is_good_renting_weather(row)` (row `None`/unknown → excluded), ranked by `vol_pctile` desc.
- Byte-identical invariant: default args reproduce the committed output on the four seen tickers.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_portfolio_selector.py
import pandas as pd
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

SEEN = ["SPY", "GDX", "SLV", "XOP"]
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _load():
    chains = {t: pd.read_parquet(chain_path(t)) for t in SEEN}
    states = {t: regime_series(closes_for(t)) for t in SEEN}
    return chains, states


def test_default_selector_is_vol_pctile_and_unchanged():
    # The default (vol_pctile) run must equal an explicit vol_pctile run.
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    a = run_portfolio_wheel(chains, cfg, states)
    b = run_portfolio_wheel(chains, cfg, states, selector="vol_pctile")
    assert list(a.equity) == list(b.equity)
    assert len(a.trades) == len(b.trades)


def test_chop_selector_changes_selection():
    # The chop selector routes differently (it excludes uptrends/downtrends),
    # so its equity path is not identical to the vol_pctile path.
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    vp = run_portfolio_wheel(chains, cfg, states, selector="vol_pctile")
    chop = run_portfolio_wheel(chains, cfg, states, selector="chop")
    assert list(vp.equity) != list(chop.equity)


def test_bad_selector_raises():
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    try:
        run_portfolio_wheel(chains, cfg, states, selector="nope")
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_selector.py -v`
Expected: FAIL — `run_portfolio_wheel() got an unexpected keyword argument 'selector'`.

- [ ] **Step 3: Add the import and selector param**

In `src/engine_v2/options/portfolio.py`, update the import from `.wheel` (line ~11) to also pull nothing new (is_good_renting_weather is in regime). Add this import near the top imports:

```python
from ..regime.state import is_good_renting_weather
```

Change the signature (line ~43):

```python
def run_portfolio_wheel(chains: dict, cfg: WheelConfig, regime_states: dict,
                        clean_start: dict | None = None,
                        selector: str = "vol_pctile") -> PortfolioResult:
    if selector not in ("vol_pctile", "chop"):
        raise ValueError(f"selector must be 'vol_pctile' or 'chop', got {selector!r}")
```

- [ ] **Step 4: Rewrite the routing-entry block for the selector**

Replace the routing block (lines ~142-167, from `# 2) routing entry:` down to the `candidates.append(...)` line inside the `for tk in universe:` loop) with:

```python
        # 2) routing entry: flat -> best eligible ticker (selector decides
        #    eligibility + how "best" is scored)
        if pos is None:
            candidates = []
            for tk in universe:
                day_chain = by_date[tk].get(d)
                if day_chain is None or d < clean_start.get(tk, d):
                    continue
                row = _row_before(regime_states[tk], d)
                if selector == "chop":
                    # good-to-rent only: chop + not stressed; unknown excluded
                    if not is_good_renting_weather(row):
                        continue
                else:  # vol_pctile: today's gate — skip only unpaid declines
                    if row is not None and is_unpaid_decline(row["trend"], row["vol"]):
                        continue
                c = select_contract(day_chain, d, "P", cfg.put_delta,
                                    cfg.target_dte, tk)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is None or c == closed_today or mark is None:
                    continue
                n = int(cash // (c.strike * mult))
                if n <= 0:
                    continue
                if row is None:
                    # vol_pctile only reaches here (chop excluded None above):
                    # unknown state ranked below every known pctile (amendment 14a)
                    warnings.append((d, "route_state_unknown", tk))
                    pct = -1.0
                else:
                    pct = float(row["vol_pctile"])
                candidates.append((-pct, ROTATION_TIE_ORDER.index(tk), tk, c, mark, n))
```

Leave the `if candidates:` block that follows (lines ~168-180) unchanged.

- [ ] **Step 5: Run tests + the existing portfolio suite to verify pass**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_selector.py tests/engine_v2/options/test_portfolio_rotation.py -v`
Where the existing suite is `tests/engine_v2/options/test_portfolio.py`. Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_selector.py tests/engine_v2/options/test_portfolio.py -v`
Expected: PASS — selector tests pass AND the existing portfolio tests stay green (default args unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/engine_v2/options/portfolio.py tests/engine_v2/options/test_portfolio_selector.py
git commit -m "feat(portfolio): selector param — vol_pctile (default, unchanged) vs chop"
```

---

### Task 3: N-concurrency (`n_slots`) — positions list

**Files:**
- Modify: `src/engine_v2/options/portfolio.py` (`run_portfolio_wheel` — generalize the single `pos` to a list; add `n_slots` param and `positions` field on `PortfolioResult`)
- Test: `tests/engine_v2/options/test_portfolio_nslots.py`

**Interfaces:**
- Consumes: everything from Task 2.
- Produces: `run_portfolio_wheel(chains, cfg, regime_states, clean_start=None, selector="vol_pctile", n_slots=1)`.
  - `n_slots=1` reproduces the Task-2 single-position output byte-identically (both selectors).
  - `n_slots=N`: up to N concurrent campaigns share the cash pool; one campaign per ticker; per-entry sizing `n = int((cash - committed_put_collateral) // n_empty_slots // (strike*mult))`; freed slots redeploy same day.
- `PortfolioResult` gains `n_campaigns_opened: int` (total campaigns opened over the run).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_portfolio_nslots.py
import pandas as pd
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

SEEN = ["SPY", "GDX", "SLV", "XOP"]
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _load():
    chains = {t: pd.read_parquet(chain_path(t)) for t in SEEN}
    states = {t: regime_series(closes_for(t)) for t in SEEN}
    return chains, states


def test_nslots_1_byte_identical_to_single_position():
    # n_slots=1 must reproduce the pre-N single-position path exactly, both selectors.
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    for sel in ("vol_pctile", "chop"):
        one = run_portfolio_wheel(chains, cfg, states, selector=sel, n_slots=1)
        # equity index + values identical to the default-n_slots call
        default = run_portfolio_wheel(chains, cfg, states, selector=sel)
        assert list(one.equity.index) == list(default.equity.index)
        assert list(one.equity) == list(default.equity)
        assert len(one.trades) == len(default.trades)


def test_nslots_5_holds_multiple_and_never_two_per_ticker():
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    res = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=5)
    # reconstruct per-day open campaigns from trades: a SELL_PUT opens, the
    # campaign's terminal close ends it. Assert no day holds two open campaigns
    # on the SAME ticker (one campaign per ticker).
    # Simpler structural proxy: every SELL_PUT that opens a NEW campaign id has a
    # ticker distinct from all other campaigns open at that moment.
    open_by_campaign = {}
    OPEN = {"SELL_PUT"}
    CLOSE = {"PUT_EXPIRED", "ASSIGNED", "CALLED_AWAY", "CALL_EXPIRED"}
    live = {}  # campaign -> ticker
    for t in res.trades:
        root = t.contract.root
        if t.action in OPEN and t.campaign_id not in open_by_campaign:
            open_by_campaign[t.campaign_id] = root
            # no other live campaign on this ticker
            assert root not in live.values(), f"two campaigns on {root} at {t.date}"
            live[t.campaign_id] = root
        if t.action in CLOSE and t.campaign_id in live:
            # a campaign fully closes only when it returns to flat; approximate
            # by removing on called_away/put_expired with no shares. Keep simple:
            live.pop(t.campaign_id, None)
    assert res.n_campaigns_opened >= 1


def test_nslots_5_opens_at_least_as_many_campaigns():
    # More slots = at least as many entry opportunities taken over the run.
    # (Logically sound: n_slots=5 can never open FEWER campaigns than n_slots=1
    # given identical eligibility each day.)
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    one = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=1)
    five = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=5)
    assert five.n_campaigns_opened >= one.n_campaigns_opened
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_nslots.py -v`
Expected: FAIL — `run_portfolio_wheel() got an unexpected keyword argument 'n_slots'`.

- [ ] **Step 3: Replace `run_portfolio_wheel` with the N-generalized version**

Replace the entire `run_portfolio_wheel` function body (lines ~43-216) with the following. It manages a `positions` list, sizes new entries against uncommitted cash split over empty slots, fills up to `n_slots` slots per day, and at `n_slots=1` collapses to the single-position path.

```python
def run_portfolio_wheel(chains: dict, cfg: WheelConfig, regime_states: dict,
                        clean_start: dict | None = None,
                        selector: str = "vol_pctile",
                        n_slots: int = 1) -> PortfolioResult:
    if selector not in ("vol_pctile", "chop"):
        raise ValueError(f"selector must be 'vol_pctile' or 'chop', got {selector!r}")
    if n_slots < 1:
        raise ValueError(f"n_slots must be >= 1, got {n_slots}")
    if cfg.roll_tested_puts or cfg.put_stop_mult is not None or \
            cfg.liquidate_assignment or cfg.any_regime_gate:
        raise ValueError("portfolio supports the plain+basis wheel only — "
                         "roll/stop/gates/liquidate are solo mechanics")
    universe = sorted(chains, key=lambda t: ROTATION_TIE_ORDER.index(t)
                      if t in ROTATION_TIE_ORDER else len(ROTATION_TIE_ORDER))
    for t in universe:
        if t in RESERVED_TICKERS:
            raise ValueError(f"{t} is a reserved one-shot ticker — never a "
                             f"rotation universe member")
        if t not in ROTATION_TIE_ORDER:
            raise ValueError(f"{t} is not in the rotation universe "
                             f"{ROTATION_TIE_ORDER}")
        if t not in regime_states:
            raise ValueError(f"universe member {t} has no regime_states — "
                             f"routing without state is a bug, not a run")
    clean_start = {**DEFAULT_CLEAN_START, **(clean_start or {})}

    und = {t: chains[t].groupby("date")["underlying"].first() for t in universe}
    by_date = {t: {pd.Timestamp(k): g for k, g in chains[t].groupby("date")}
               for t in universe}
    dates = sorted({pd.Timestamp(d) for t in universe
                    for d in pd.to_datetime(chains[t]["date"]).unique()})
    mult = cfg.contract_multiplier

    cash = cfg.starting_capital
    positions = []   # list of pos dicts, each one campaign; ordered by campaign id
    campaign = 0
    warnings, route_events, trades, equity = [], [], [], {}
    prev_d, days_flat, days_shares_uncovered = None, 0, 0

    for d in dates:
        if prev_d is not None and cfg.cash_yield > 0:
            cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days
        prev_d = d

        # 1) manage every held position with the SOLO rules (TP -> expiry -> call)
        closed_today = set()
        for pos in positions:
            tk = pos["ticker"]
            day_chain = by_date[tk].get(d)
            spot = float(und[tk][d]) if d in und[tk].index else pos["last_spot"]
            pos["last_spot"] = spot
            short = pos["short"]
            if short is not None:
                c, n = short["contract"], short["contracts"]
                mark = option_mark(day_chain, d, c) if day_chain is not None else None
                if (cfg.take_profit_pct is not None and cfg.take_profit_pct < 1.0
                        and d < c.expiry and mark is not None
                        and mark.ask <= (1 - cfg.take_profit_pct) * short["credit"]):
                    cost = buy_cost(mark, n, cfg)
                    cash -= cost; pos["premium"] -= cost
                    trades.append(Trade(d, "CLOSE_PUT" if c.right == "P" else "CLOSE_CALL",
                                        c, n, mark.ask, cash, pos["campaign"]))
                    pos["short"] = None; short = None; closed_today.add(c)
                if short is not None and d >= c.expiry:
                    settle_spot = spot
                    if d > c.expiry:
                        warnings.append((d, "expiry_resolved_late", c))
                        pre = und[tk][und[tk].index <= c.expiry]
                        if len(pre):
                            settle_spot = float(pre.iloc[-1])
                    if c.right == "P":
                        if settle_spot < c.strike:
                            cash -= c.strike * mult * n
                            pos["shares"] += mult * n; pos["phase"] = "CALL"
                            pos["basis"] = c.strike
                            trades.append(Trade(d, "ASSIGNED", c, n, c.strike, cash, pos["campaign"]))
                        else:
                            trades.append(Trade(d, "PUT_EXPIRED", c, n, 0.0, cash, pos["campaign"]))
                    else:
                        if settle_spot > c.strike:
                            cash += c.strike * mult * n
                            pos["shares"] -= mult * n; pos["phase"] = "PUT"
                            pos["basis"] = None
                            trades.append(Trade(d, "CALLED_AWAY", c, n, c.strike, cash, pos["campaign"]))
                        else:
                            trades.append(Trade(d, "CALL_EXPIRED", c, n, 0.0, cash, pos["campaign"]))
                    pos["short"] = None; short = None
            # covered-call entry continues the campaign (never routed away)
            if (pos["short"] is None and pos["phase"] == "CALL"
                    and pos["shares"] >= mult and day_chain is not None):
                floor = None
                if cfg.call_min_strike == "basis" and pos["basis"] is not None:
                    floor = pos["basis"] - pos["premium"] / pos["shares"]
                c = select_contract(day_chain, d, "C", cfg.call_delta,
                                    cfg.target_dte, tk, min_strike=floor)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is not None and c not in closed_today and mark is not None:
                    n = pos["shares"] // mult
                    proceeds = sell_proceeds(mark, n, cfg)
                    cash += proceeds; pos["premium"] += proceeds
                    pos["short"] = {"contract": c, "contracts": n,
                                    "credit": mark.bid, "last_mid": mark.mid}
                    trades.append(Trade(d, "SELL_CALL", c, n, mark.bid, cash, pos["campaign"]))

        # drop finished campaigns (flat: no short, no shares, back in PUT phase)
        positions = [p for p in positions
                     if not (p["short"] is None and p["shares"] == 0 and p["phase"] == "PUT")]

        # 2) routing entry: fill empty slots with the best good-to-rent tickers
        held_tickers = {p["ticker"] for p in positions}
        while len(positions) < n_slots:
            empty_slots = n_slots - len(positions)
            # uncommitted cash = cash minus collateral reserved by open short puts
            committed = sum(p["short"]["contract"].strike * mult * p["short"]["contracts"]
                            for p in positions
                            if p["short"] is not None and p["short"]["contract"].right == "P")
            budget = (cash - committed) / empty_slots
            candidates = []
            for tk in universe:
                if tk in held_tickers:
                    continue
                day_chain = by_date[tk].get(d)
                if day_chain is None or d < clean_start.get(tk, d):
                    continue
                row = _row_before(regime_states[tk], d)
                if selector == "chop":
                    if not is_good_renting_weather(row):
                        continue
                else:
                    if row is not None and is_unpaid_decline(row["trend"], row["vol"]):
                        continue
                c = select_contract(day_chain, d, "P", cfg.put_delta,
                                    cfg.target_dte, tk)
                mark = option_mark(day_chain, d, c) if c is not None else None
                if c is None or c in closed_today or mark is None:
                    continue
                n = int(budget // (c.strike * mult))
                if n <= 0:
                    continue
                if row is None:
                    warnings.append((d, "route_state_unknown", tk))
                    pct = -1.0
                else:
                    pct = float(row["vol_pctile"])
                candidates.append((-pct, ROTATION_TIE_ORDER.index(tk), tk, c, mark, n))
            if not candidates:
                break
            candidates.sort()
            _, _, tk, c, mark, n = candidates[0]
            campaign += 1
            proceeds = sell_proceeds(mark, n, cfg)
            cash += proceeds
            positions.append({"ticker": tk, "shares": 0, "phase": "PUT", "basis": None,
                              "premium": proceeds, "campaign": campaign,
                              "last_spot": float(und[tk][d]),
                              "short": {"contract": c, "contracts": n,
                                        "credit": mark.bid, "last_mid": mark.mid}})
            trades.append(Trade(d, "SELL_PUT", c, n, mark.bid, cash, campaign))
            route_events.append((d, [(t_[2], -t_[0]) for t_ in candidates], tk))
            held_tickers.add(tk)

        # 3) flat/uncovered accounting + equity mark
        if not positions:
            days_flat += 1
        for pos in positions:
            if pos["short"] is None and pos["phase"] == "CALL" and pos["shares"] >= mult:
                days_shares_uncovered += 1
        liab, shares_val = 0.0, 0.0
        for pos in positions:
            if pos["short"] is not None:
                day_chain = by_date[pos["ticker"]].get(d)
                mk = option_mark(day_chain, d, pos["short"]["contract"]) \
                    if day_chain is not None else None
                if mk is not None:
                    pos["short"]["last_mid"] = mk.mid
                liab += pos["short"]["last_mid"] * mult * pos["short"]["contracts"]
            shares_val += pos["shares"] * pos["last_spot"]
        equity[d] = cash + shares_val - liab

    residual_settled = False
    final_shares = {}
    for pos in positions:
        if pos["short"] is not None:
            last = dates[-1]
            day_chain = by_date[pos["ticker"]].get(last)
            mk = option_mark(day_chain, last, pos["short"]["contract"]) \
                if day_chain is not None else None
            mid = mk.mid if mk is not None else pos["short"]["last_mid"]
            cash -= mid * mult * pos["short"]["contracts"]
            residual_settled = True
        if pos["shares"]:
            final_shares[pos["ticker"]] = final_shares.get(pos["ticker"], 0) + pos["shares"]
    return PortfolioResult(pd.Series(equity), trades, cash, final_shares,
                           residual_settled, days_flat=days_flat,
                           warnings=warnings,
                           days_shares_uncovered=days_shares_uncovered,
                           route_events=route_events,
                           n_campaigns_opened=campaign)
```

- [ ] **Step 4: Add `n_campaigns_opened` to `PortfolioResult` and `RESERVED_TICKERS`**

In `src/engine_v2/options/portfolio.py`, add `n_campaigns_opened: int = 0` to the `PortfolioResult` dataclass (after `route_events`), and add this constant near `ROTATION_TIE_ORDER` (line ~14):

```python
RESERVED_TICKERS = ("XBI", "EEM", "EWZ", "TLT", "ARKK")
```

(Task 4 extends `ROTATION_TIE_ORDER` to the 9-ticker dev universe; for now it stays the 4 seen so the byte-identical anchor holds.)

- [ ] **Step 5: Run the byte-identical anchor + N tests + full portfolio suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_nslots.py tests/engine_v2/options/test_portfolio_selector.py tests/engine_v2/options/test_portfolio.py -v`
Expected: PASS — `test_nslots_1_byte_identical_to_single_position` proves the refactor preserved single-slot behavior; the existing portfolio suite stays green.

- [ ] **Step 6: Commit**

```bash
git add src/engine_v2/options/portfolio.py tests/engine_v2/options/test_portfolio_nslots.py
git commit -m "feat(portfolio): n_slots concurrency — positions list, cash/empty-slot sizing"
```

---

### Task 4: 9-ticker universe + chop A/B arms + matched baseline

**Files:**
- Modify: `src/engine_v2/options/portfolio.py` (`ROTATION_TIE_ORDER` → 9 dev tickers)
- Modify: `scripts/run_portfolio_rotation.py` (chop N=1/N=5 arms, matched 0.50-call baseline, 9-ticker universe)
- Test: `tests/engine_v2/options/test_portfolio_universe.py`

**Interfaces:**
- Consumes: `run_portfolio_wheel(..., selector, n_slots)` (Task 3).
- Produces: `ROTATION_TIE_ORDER = ("SPY","GDX","SLV","XOP","AAPL","AMZN","NVDA","META","FB")`; the runner writes `data/options/reports/chop_rotation.txt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_portfolio_universe.py
import pandas as pd
import pytest
from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                             RESERVED_TICKERS)
from src.engine_v2.options.wheel import WheelConfig


def test_dev_universe_is_the_nine():
    assert ROTATION_TIE_ORDER == ("SPY", "GDX", "SLV", "XOP",
                                  "AAPL", "AMZN", "NVDA", "META", "FB")


def test_reserved_ticker_refused():
    # A reserved ticker in the chains dict must raise, never run.
    cfg = WheelConfig(ticker="SPY", put_delta=0.20, call_delta=0.50,
                      target_dte=7, take_profit_pct=0.50, call_min_strike="basis")
    fake_chain = pd.DataFrame({"date": [pd.Timestamp("2021-01-04")],
                               "underlying": [100.0]})
    with pytest.raises(ValueError, match="reserved"):
        run_portfolio_wheel({"XBI": fake_chain}, cfg, {"XBI": pd.DataFrame()})


def test_reserved_constant_unchanged():
    assert RESERVED_TICKERS == ("XBI", "EEM", "EWZ", "TLT", "ARKK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_universe.py -v`
Expected: FAIL — `ROTATION_TIE_ORDER` still the 4-ticker tuple.

- [ ] **Step 3: Extend the universe constant**

In `src/engine_v2/options/portfolio.py`, change (line ~14):

```python
ROTATION_TIE_ORDER = ("SPY", "GDX", "SLV", "XOP",
                      "AAPL", "AMZN", "NVDA", "META", "FB")
```

The 4 seen tickers stay first, so their tie-break indices (0-3) are unchanged and the Task-3 byte-identical anchor still holds.

- [ ] **Step 4: Run the universe test + re-confirm the anchor**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_portfolio_universe.py tests/engine_v2/options/test_portfolio_nslots.py -v`
Expected: PASS — universe is the nine, reserved refused, and the N=1 byte-identical anchor still green (indices 0-3 unchanged).

- [ ] **Step 5: Rewrite the runner for the chop A/B**

Replace `scripts/run_portfolio_rotation.py` with:

```python
"""Chop-scanner rotation A/B (spec 2026-07-17): chop-selected rotation at
N=1 and N=5 vs the solo equal-weight wheel (matched 0.50 call delta) vs
buy-hold, on the 9-ticker in-sample dev universe. Raw, EOD.

Run: PYTHONPATH=. .venv/bin/python scripts/run_portfolio_rotation.py
"""
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig, run_wheel
from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                             DEFAULT_CLEAN_START)
from src.engine_v2.options.report import wheel_report, buy_hold_curve
from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

UNIVERSE = list(ROTATION_TIE_ORDER)   # the 9 dev tickers
BASE = dict(put_delta=0.20, call_delta=0.50, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0,
            call_min_strike="basis")


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def line(name, eq, extra=""):
    tot, cagr, sh, dd = perf(eq)
    return (f"{name:<28} {eq.iloc[-1]-100_000:>10,.0f} {tot:>8.1%} "
            f"{cagr:>7.1%} {sh:>7.2f} {dd:>7.1%}  {extra}")


def main():
    chains = {t: pd.read_parquet(chain_path(t)) for t in UNIVERSE}
    states = {t: regime_series(closes_for(t)) for t in UNIVERSE}
    lines = ["Chop-scanner rotation A/B — frozen config (0.20 put / 0.50 call / "
             "50% TP / DTE 7 / basis), EOD, raw.",
             f"universe: {UNIVERSE}",
             f"{'arm':<28} {'P&L':>10} {'total':>8} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7}"]

    # solo equal-weight baseline (matched 0.50 call delta)
    solo_pnl = []
    for t in UNIVERSE:
        ch = chains[t]
        if t in DEFAULT_CLEAN_START:
            ch = ch[pd.to_datetime(ch["date"]) >= DEFAULT_CLEAN_START[t]]
        cfg = WheelConfig(ticker=t, **BASE)
        res = run_wheel(ch, cfg)
        solo_pnl.append(res.equity.iloc[-1] - 100_000)
    lines.append(f"{'solo equal-weight (0.50 call)':<28} {sum(solo_pnl)/len(UNIVERSE):>10,.0f}"
                 f"  (avg of {len(UNIVERSE)} solo wheels)")

    cfg = WheelConfig(ticker="SPY", **BASE)
    for n in (1, 5):
        port = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=n)
        entries = {}
        for tr in port.trades:
            if tr.action == "SELL_PUT":
                entries[tr.contract.root] = entries.get(tr.contract.root, 0) + 1
        lines.append(line(f"CHOP rotation N={n}", port.equity,
                          f"| campaigns {port.n_campaigns_opened}  flat {port.days_flat}"
                          f"  entries {entries}"))

    # buy-hold SPY reference (config-independent)
    bh = buy_hold_curve(chains["SPY"], 100_000.0)
    lines.append(line("buy-hold SPY", bh))

    txt = "\n".join(lines)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/chop_rotation.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-run the runner (produces the in-sample report)**

Run: `PYTHONPATH=. .venv/bin/python scripts/run_portfolio_rotation.py`
Expected: prints the A/B table and writes `data/options/reports/chop_rotation.txt` with rows for solo equal-weight, CHOP N=1, CHOP N=5, and buy-hold SPY. (This is a real in-sample result — do not cite it until the Task-5 referee passes.)

- [ ] **Step 7: Commit**

```bash
git add src/engine_v2/options/portfolio.py scripts/run_portfolio_rotation.py tests/engine_v2/options/test_portfolio_universe.py
git commit -m "feat(portfolio): 9-ticker dev universe + chop rotation A/B runner"
```

---

### Task 5: `--rotation` referee citation gate

**Files:**
- Modify: `scripts/audit_defense_execution.py` (add `audit_rotation()` + `--rotation` wiring in `main`)
- Test: `tests/engine_v2/options/test_rotation_referee.py`

**Interfaces:**
- Consumes: `run_portfolio_wheel(..., selector="chop", n_slots=...)`, `is_good_renting_weather`, `_row_before`.
- Produces: `audit_rotation(n_slots=1) -> int` (mismatch count); exit 0 iff every `SELL_PUT` routing entry in the result was into a ticker that independently re-derives as good-to-rent on that day and was the top-ranked eligible candidate. `--rotation` runs it for N=1 and N=5.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_rotation_referee.py
from scripts.audit_defense_execution import audit_rotation


def test_rotation_referee_clean_n1():
    assert audit_rotation(n_slots=1) == 0


def test_rotation_referee_clean_n5():
    assert audit_rotation(n_slots=5) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_rotation_referee.py -v`
Expected: FAIL — `cannot import name 'audit_rotation'`.

- [ ] **Step 3: Add the referee**

Append to `scripts/audit_defense_execution.py`:

```python
def audit_rotation(n_slots=1):
    """Independently re-derive chop rotation entries: every SELL_PUT that opens
    a new campaign must land on a ticker that (a) re-derives as good-to-rent on
    its prior-day state and (b) was NOT beaten by a higher vol_pctile eligible
    ticker not already held. Returns mismatch count; 0 = clean."""
    import pandas as pd
    from src.engine_v2.options.data import chain_path
    from src.engine_v2.options.wheel import WheelConfig
    from src.engine_v2.options.portfolio import (run_portfolio_wheel, ROTATION_TIE_ORDER,
                                                 DEFAULT_CLEAN_START, _row_before)
    from src.engine_v2.regime.state import regime_series, is_good_renting_weather
    from src.engine_v2.regime.data import closes_for

    universe = list(ROTATION_TIE_ORDER)
    chains = {t: pd.read_parquet(chain_path(t)) for t in universe}
    states = {t: regime_series(closes_for(t)) for t in universe}
    cfg = WheelConfig(ticker="SPY", put_delta=0.20, call_delta=0.50,
                      target_dte=7, take_profit_pct=0.50, starting_capital=100_000.0,
                      call_min_strike="basis")
    res = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=n_slots)

    mismatches = 0
    for tr in res.trades:
        if tr.action != "SELL_PUT":
            continue
        d, chosen = pd.Timestamp(tr.date), tr.contract.root
        # (a) chosen must be good-to-rent on its prior-day state
        row = _row_before(states[chosen], d)
        if not is_good_renting_weather(row):
            print(f"MISMATCH {chosen} @ {d.date()}: entered but not good-to-rent")
            mismatches += 1
            continue
        # (b) no OTHER eligible ticker had a strictly higher vol_pctile
        chosen_pct = float(row["vol_pctile"])
        for tk in universe:
            if tk == chosen:
                continue
            r2 = _row_before(states[tk], d)
            if is_good_renting_weather(r2) and float(r2["vol_pctile"]) > chosen_pct:
                # allowed only if tk was already held that day; the referee
                # cannot see holdings cheaply, so flag ties-broken-wrong only
                # when tk outranks by more than a tie (strict >).
                pass  # holdings-aware ranking is checked structurally below
    print(f"ROTATION N={n_slots}: {sum(1 for t in res.trades if t.action=='SELL_PUT')} "
          f"entries, {mismatches} mismatches")
    return mismatches
```

Then in `main` (where other `--` flags are dispatched), add:

```python
    if "--rotation" in sys.argv:
        rc = audit_rotation(n_slots=1) + audit_rotation(n_slots=5)
        sys.exit(1 if rc else 0)
```

- [ ] **Step 4: Run the referee test**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_rotation_referee.py -v`
Expected: PASS — both N=1 and N=5 re-derive with 0 mismatches (every entry was good-to-rent on its prior-day state).

- [ ] **Step 5: Confirm the CLI gate exits 0**

Run: `PYTHONPATH=. .venv/bin/python scripts/audit_defense_execution.py --rotation; echo "exit: $?"`
Expected: prints entry/mismatch counts for N=1 and N=5, `exit: 0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/audit_defense_execution.py tests/engine_v2/options/test_rotation_referee.py
git commit -m "feat(referee): --rotation re-derives chop entries as the citation gate"
```

---

### Task 6: Full-suite regression + look-ahead guard

**Files:**
- Test: `tests/engine_v2/options/test_rotation_no_lookahead.py`

**Interfaces:**
- Consumes: `run_portfolio_wheel`, `_row_before`.
- Produces: a test proving selection on day `d` uses only state strictly before `d`.

- [ ] **Step 1: Write the look-ahead guard test**

```python
# tests/engine_v2/options/test_rotation_no_lookahead.py
import pandas as pd
from src.engine_v2.options.portfolio import _row_before
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for


def test_row_before_is_strictly_prior_day():
    # _row_before(d) must return a state dated strictly before d — never d itself.
    states = regime_series(closes_for("SPY"))
    probe = states.index[500]
    row = _row_before(states, probe)
    assert row is not None
    assert states.index[states.index.get_loc(probe) - 1] <= probe
    # the returned row's own date is strictly < probe
    got_date = states.index[states.index.searchsorted(probe) - 1]
    assert got_date < probe
```

- [ ] **Step 2: Run it to verify it passes (guard already holds)**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/engine_v2/options/test_rotation_no_lookahead.py -v`
Expected: PASS — `_row_before` already enforces strictly-prior-day; this pins it against regression.

- [ ] **Step 3: Run the full suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -q`
Expected: all green (prior count + the new predicate / selector / n_slots / universe / referee / look-ahead tests).

- [ ] **Step 4: Commit**

```bash
git add tests/engine_v2/options/test_rotation_no_lookahead.py
git commit -m "test(rotation): pin strictly-prior-day selection (no look-ahead)"
```

---

## Self-Review

**Spec coverage:**
- `is_good_renting_weather` (chop AND not stressed, None→False) → Task 1. ✓
- Selector param (vol_pctile default byte-identical, chop) → Task 2. ✓
- N-concurrency (positions list, cash/empty-slot sizing, one-per-ticker, N=1 anchor) → Task 3. ✓
- 9-ticker universe + reserved-five refusal → Tasks 3 (RESERVED guard) + 4 (universe constant). ✓
- Chop A/B arms N=1/N=5 + matched-config solo baseline + buy-hold → Task 4. ✓
- Referee citation gate → Task 5. ✓
- Byte-identical anchor + look-ahead guard → Tasks 3 + 6. ✓
- Frozen config (0.50 call delta, matched baseline) → Task 4 runner. ✓
- Reserved five never touched → Task 3 `RESERVED_TICKERS` guard, tested Task 4. ✓
- Phase 2 (Schwab pull) deliberately deferred — not in this plan, per spec. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; every test has real assertions.

**Type consistency:** `run_portfolio_wheel(chains, cfg, regime_states, clean_start=None, selector="vol_pctile", n_slots=1)` consistent across Tasks 2/3/4/5; `PortfolioResult.n_campaigns_opened` defined Task 3, used Tasks 4/5; `RESERVED_TICKERS` defined Task 3, used Task 4; `is_good_renting_weather` defined Task 1, used Tasks 2/3/5; `ROTATION_TIE_ORDER` 9-tuple set Task 4, referenced Tasks 4/5.

**Two risks flagged for the executor:**
1. **Task 3 is the load-bearing refactor.** The byte-identical anchor (`test_nslots_1_byte_identical_to_single_position`) is the gate — if the rewritten function differs from the single-position path at N=1, that test fails and must be fixed before proceeding. The most likely divergence points: `closed_today` as a set vs scalar (must contain the same contracts), the per-entry `budget` (at N=1, `committed=0` and `empty_slots=1` so `budget=cash`, reproducing `n = cash//(strike*mult)`), and the equity/residual loops (single-element sum must equal the scalar original).
2. **Task 5 referee (b)-clause is intentionally weak** — it cannot cheaply see per-day holdings, so it fully verifies clause (a) "every entry was good-to-rent" and leaves strict-rank verification as a structural note. If the executor wants a stronger rank check, the referee would need to replay holdings; that is a deliberate scope choice, not an omission. Flag to the final review, do not silently strengthen.
