# Per-Day Step Extraction (Sub-project B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `run_portfolio_wheel`'s per-day loop into a shared `step_one_day()` behind a `Market` seam, so the live bot and backtest run identical logic — with the backtest output byte-identical.

**Architecture:** A `Market` interface + `BatchMarket` wrap the backtest's preloaded data dicts. `step_one_day(state, market, day, cfg, ...)` holds the per-day loop body verbatim, reading market data through `market.` calls and mutating a `PortfolioState`. `run_portfolio_wheel` shrinks to setup → per-day loop → residual finalizer → `PortfolioResult`.

**Tech Stack:** Python 3.9 (`.venv`), pandas, pytest. This is engine code (py3.9 backtest venv).

## Global Constraints

- Run tests with `.venv/bin/python -m pytest` (the `.venv/bin/pytest` shebang is broken). Referee: `PYTHONPATH=. .venv/bin/python scripts/audit_defense_execution.py --rotation`.
- **PURE REFACTOR — byte-identical.** No behavior change. The batch output must not move.
- **The anchor:** `tests/engine_v2/options/test_portfolio.py`, `test_portfolio_selector.py`, `test_portfolio_nslots.py`, `test_portfolio_universe.py` must ALL pass UNCHANGED, and `--rotation` referee must exit 0. They pin the batch output trade-by-trade + `equity.equals`.
- Market-access mapping (batch loop → market seam): `by_date[tk].get(d)` → `market.chain(tk, d)`; `float(und[tk][d]) if d in und[tk].index else fb` → `market.spot(tk, d, fb)`; the `und[tk][und[tk].index <= expiry].iloc[-1]` late-expiry reach → `market.settle_price(tk, expiry)`; `_row_before(regime_states[tk], d)` → `market.regime_row(tk, d)`; `d < clean_start.get(tk, d)` → `not market.eligible(tk, d)`; `universe` → `market.universe`.
- `step_one_day` mutates `state` (cash, positions, campaign, days_flat, days_shares_uncovered, prev_d) and returns `StepResult(trades, equity, warnings, route_events)` (today's outputs).
- Reserved-ticker guard (`RESERVED_TICKERS`), selector validation, `n_slots` validation stay in `run_portfolio_wheel`.
- Branch: `paper-step-extraction`. Spec: `docs/superpowers/specs/2026-07-17-paper-step-extraction-design.md`.

---

### Task 1: `Market` interface + `BatchMarket`

**Files:**
- Create: `src/engine_v2/options/market.py`
- Test: `tests/engine_v2/options/test_batch_market.py`

**Interfaces:**
- Consumes: `_row_before` (currently in `portfolio.py`); import it there.
- Produces:
  - `class BatchMarket`, constructed `BatchMarket(chains: dict, regime_states: dict, clean_start: dict, universe: list)`.
  - Methods: `chain(ticker, day) -> pd.DataFrame | None`; `spot(ticker, day, fallback) -> float`; `settle_price(ticker, expiry) -> float | None`; `regime_row(ticker, day)`; `eligible(ticker, day) -> bool`; property `universe -> list`.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine_v2/options/test_batch_market.py
import pandas as pd
from src.engine_v2.options.market import BatchMarket


def _chain(dates_strikes):
    # rows: (date, expiry, strike, underlying)
    rows = [{"date": pd.Timestamp(d), "expiry": pd.Timestamp(e), "strike": s,
             "right": "P", "dte": 7, "delta": -0.2, "bid": 1.0, "ask": 1.1,
             "mid": 1.05, "underlying": u} for (d, e, s, u) in dates_strikes]
    return pd.DataFrame(rows)


def _mkt():
    ch = _chain([("2021-01-04", "2021-01-15", 30.0, 33.0),
                 ("2021-01-05", "2021-01-15", 30.0, 34.0)])
    chains = {"GDX": ch}
    # regime_states: a tiny frame indexed by date
    states = {"GDX": pd.DataFrame({"trend": ["chop"], "vol": ["normal"],
              "vol_pctile": [0.5]}, index=[pd.Timestamp("2021-01-04")])}
    return BatchMarket(chains, states, {"XOP": pd.Timestamp("2020-07-01")}, ["GDX"])


def test_chain_returns_days_rows_or_none():
    m = _mkt()
    assert len(m.chain("GDX", pd.Timestamp("2021-01-04"))) == 1
    assert m.chain("GDX", pd.Timestamp("2021-01-06")) is None   # no rows that day


def test_spot_present_and_fallback():
    m = _mkt()
    assert m.spot("GDX", pd.Timestamp("2021-01-05"), 99.0) == 34.0
    assert m.spot("GDX", pd.Timestamp("2021-01-06"), 99.0) == 99.0   # absent -> fallback


def test_settle_price_on_or_before_expiry():
    m = _mkt()
    # last underlying at/before 2021-01-15 is the 2021-01-05 row = 34.0
    assert m.settle_price("GDX", pd.Timestamp("2021-01-15")) == 34.0
    # before any data -> None
    assert m.settle_price("GDX", pd.Timestamp("2020-01-01")) is None


def test_regime_row_strictly_prior_day():
    m = _mkt()
    row = m.regime_row("GDX", pd.Timestamp("2021-01-05"))   # prior day 01-04 exists
    assert row is not None and row["trend"] == "chop"
    assert m.regime_row("GDX", pd.Timestamp("2021-01-04")) is None  # nothing strictly before


def test_eligible_respects_clean_start():
    m = _mkt()
    assert m.eligible("GDX", pd.Timestamp("2021-01-04")) is True     # no clean_start for GDX
    m2 = BatchMarket({"XOP": _chain([("2020-01-02","2020-01-15",30.0,30.0)])},
                     {"XOP": pd.DataFrame()}, {"XOP": pd.Timestamp("2020-07-01")}, ["XOP"])
    assert m2.eligible("XOP", pd.Timestamp("2020-01-02")) is False   # before clean start
    assert m2.eligible("XOP", pd.Timestamp("2020-08-01")) is True


def test_universe_property():
    assert _mkt().universe == ["GDX"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_batch_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine_v2.options.market'`.

- [ ] **Step 3: Write `market.py`**

```python
# src/engine_v2/options/market.py
"""The Market seam: step_one_day reads market data through this interface, so
the SAME per-day logic runs over full backtest history (BatchMarket) or today's
live data + carried prices (LiveMarket, sub-project B2). BatchMarket preserves
the batch backtest's exact behavior — including the on-or-before-expiry history
reach in settle_price."""
from __future__ import annotations
import pandas as pd

GATE_STALENESS_DAYS = 14   # mirrors wheel.GATE_STALENESS_DAYS / regime autopsy


def _row_before(states: pd.DataFrame, d):
    """Full state row strictly before d, staleness-bounded. None -> unknown."""
    idx = states.index
    pos = idx.searchsorted(pd.Timestamp(d)) - 1
    if pos < 0 or (pd.Timestamp(d) - idx[pos]).days > GATE_STALENESS_DAYS:
        return None
    return states.iloc[pos]


class BatchMarket:
    """Market over fully-preloaded backtest chains + regime states."""

    def __init__(self, chains: dict, regime_states: dict, clean_start: dict,
                 universe: list):
        self._und = {t: chains[t].groupby("date")["underlying"].first()
                     for t in universe}
        self._by_date = {t: {pd.Timestamp(k): g
                             for k, g in chains[t].groupby("date")}
                         for t in universe}
        self._states = regime_states
        self._clean_start = clean_start
        self._universe = list(universe)

    @property
    def universe(self) -> list:
        return self._universe

    def chain(self, ticker, day):
        return self._by_date[ticker].get(pd.Timestamp(day))

    def spot(self, ticker, day, fallback):
        u = self._und[ticker]
        d = pd.Timestamp(day)
        return float(u[d]) if d in u.index else fallback

    def settle_price(self, ticker, expiry):
        u = self._und[ticker]
        pre = u[u.index <= pd.Timestamp(expiry)]
        return float(pre.iloc[-1]) if len(pre) else None

    def regime_row(self, ticker, day):
        return _row_before(self._states[ticker], day)

    def eligible(self, ticker, day) -> bool:
        cs = self._clean_start.get(ticker)
        return cs is None or pd.Timestamp(day) >= pd.Timestamp(cs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_batch_market.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/market.py tests/engine_v2/options/test_batch_market.py
git commit -m "feat(engine): Market seam + BatchMarket (backtest data behind an interface)"
```

---

### Task 2: `step_one_day` + `PortfolioState` + refactor `run_portfolio_wheel`

**Files:**
- Modify: `src/engine_v2/options/portfolio.py` (add `PortfolioState`, `StepResult`, `step_one_day`; reduce `run_portfolio_wheel` to the shell)
- Test: `tests/engine_v2/options/test_step_one_day.py`

**Interfaces:**
- Consumes: `BatchMarket` (Task 1); existing `select_contract`, `option_mark`, `sell_proceeds`, `buy_cost`, `is_good_renting_weather`, `is_unpaid_decline`, `Trade`, `ROTATION_TIE_ORDER`.
- Produces:
  - `@dataclass PortfolioState`: `cash: float`, `positions: list`, `campaign: int = 0`, `days_flat: int = 0`, `days_shares_uncovered: int = 0`, `prev_d = None`.
  - `@dataclass StepResult`: `trades: list`, `equity: float`, `warnings: list`, `route_events: list`.
  - `step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult` — mutates `state`, returns today's outputs.
  - `run_portfolio_wheel(...)` — same signature + `PortfolioResult` as today (byte-identical output).

- [ ] **Step 1: Write the failing isolation test**

```python
# tests/engine_v2/options/test_step_one_day.py
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import PortfolioState, step_one_day


class _StubMarket:
    """Minimal Market: one held ticker, no chain today (forces the expiry path),
    a spot above the put strike (so the put expires worthless), empty universe
    (no new entries)."""
    universe = []
    def __init__(self, spot_val): self._spot = spot_val
    def chain(self, t, d): return None
    def spot(self, t, d, fb): return self._spot
    def settle_price(self, t, e): return self._spot
    def regime_row(self, t, d): return None
    def eligible(self, t, d): return True


def _cfg():
    return WheelConfig(ticker="GDX", put_delta=0.20, call_delta=0.50,
                       target_dte=7, take_profit_pct=0.50, starting_capital=100_000.0,
                       call_min_strike="basis")


def test_step_expires_put_worthless_and_drops_campaign():
    d = pd.Timestamp("2021-01-15")   # == expiry
    put = Contract("GDX", d, 30.0, "P")
    pos = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
           "premium": 99.0, "campaign": 1, "last_spot": 35.0,
           "short": {"contract": put, "contracts": 1, "credit": 1.0, "last_mid": 1.0}}
    state = PortfolioState(cash=100_099.0, positions=[pos], campaign=1)
    r = step_one_day(state, _StubMarket(spot_val=35.0), d, _cfg(),
                     selector="chop", n_slots=1)
    # spot 35 > strike 30 -> PUT_EXPIRED, no cash change on expiry
    actions = [t.action for t in r.trades]
    assert "PUT_EXPIRED" in actions
    assert state.positions == []            # campaign went flat -> dropped
    assert state.cash == 100_099.0
    assert state.days_flat == 1             # ended the day flat
    assert isinstance(r.equity, float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_step_one_day.py -v`
Expected: FAIL — `cannot import name 'PortfolioState'`.

- [ ] **Step 3: Add the dataclasses + `step_one_day`, refactor `run_portfolio_wheel`**

In `src/engine_v2/options/portfolio.py`: add `from .market import BatchMarket` to the imports. Add these dataclasses near `PortfolioResult`:

```python
@dataclass
class PortfolioState:
    cash: float
    positions: list
    campaign: int = 0
    days_flat: int = 0
    days_shares_uncovered: int = 0
    prev_d: object = None


@dataclass
class StepResult:
    trades: list
    equity: float
    warnings: list
    route_events: list
```

Add `step_one_day` (the loop body verbatim — unpack state to locals, run the body with `market.` accesses, repack):

```python
def step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult:
    """One trading day: manage held positions, drop finished campaigns, fill
    empty slots, mark equity. Mutates `state`; returns today's outputs. The
    SAME logic the batch backtest runs — reading through the Market seam."""
    d = pd.Timestamp(day)
    cash = state.cash
    positions = state.positions
    campaign = state.campaign
    days_flat = state.days_flat
    days_shares_uncovered = state.days_shares_uncovered
    prev_d = state.prev_d
    mult = cfg.contract_multiplier
    trades, warnings, route_events = [], [], []

    if prev_d is not None and cfg.cash_yield > 0:
        cash *= (1 + cfg.cash_yield / 365) ** (d - prev_d).days

    # 1) manage every held position (TP -> expiry -> covered call)
    closed_today = set()
    for pos in positions:
        tk = pos["ticker"]
        day_chain = market.chain(tk, d)
        spot = market.spot(tk, d, pos["last_spot"])
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
                    sp = market.settle_price(tk, c.expiry)
                    if sp is not None:
                        settle_spot = sp
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

    positions[:] = [p for p in positions
                    if not (p["short"] is None and p["shares"] == 0 and p["phase"] == "PUT")]

    # 2) routing entry: fill empty slots with the best good-to-rent tickers
    held_tickers = {p["ticker"] for p in positions}
    while len(positions) < n_slots:
        empty_slots = n_slots - len(positions)
        committed = sum(p["short"]["contract"].strike * mult * p["short"]["contracts"]
                        for p in positions
                        if p["short"] is not None and p["short"]["contract"].right == "P")
        budget = (cash - committed) / empty_slots
        candidates = []
        for tk in market.universe:
            if tk in held_tickers:
                continue
            day_chain = market.chain(tk, d)
            if day_chain is None or not market.eligible(tk, d):
                continue
            row = market.regime_row(tk, d)
            if selector == "chop":
                if not is_good_renting_weather(row):
                    continue
            else:
                if row is not None and is_unpaid_decline(row["trend"], row["vol"]):
                    continue
            c = select_contract(day_chain, d, "P", cfg.put_delta, cfg.target_dte, tk)
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
                          "last_spot": market.spot(tk, d, 0.0),
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
            day_chain = market.chain(pos["ticker"], d)
            mk = option_mark(day_chain, d, pos["short"]["contract"]) \
                if day_chain is not None else None
            if mk is not None:
                pos["short"]["last_mid"] = mk.mid
            liab += pos["short"]["last_mid"] * mult * pos["short"]["contracts"]
        shares_val += pos["shares"] * pos["last_spot"]
    equity_val = cash + shares_val - liab

    state.cash = cash
    state.positions = positions
    state.campaign = campaign
    state.days_flat = days_flat
    state.days_shares_uncovered = days_shares_uncovered
    state.prev_d = d
    return StepResult(trades, equity_val, warnings, route_events)
```

Then replace the body of `run_portfolio_wheel` from the `und = {...}` preload (line ~73) through the final `return PortfolioResult(...)` with the shell below (keep everything ABOVE the preload — the `selector`/`n_slots` validation, the roll/stop refusal, the universe sort + reserved/unknown-state validation, and the `clean_start = {...}` line — unchanged):

```python
    market = BatchMarket(chains, regime_states, clean_start, universe)
    dates = sorted({pd.Timestamp(d) for t in universe
                    for d in pd.to_datetime(chains[t]["date"]).unique()})
    mult = cfg.contract_multiplier

    state = PortfolioState(cash=cfg.starting_capital, positions=[])
    warnings, route_events, trades, equity = [], [], [], {}
    for d in dates:
        r = step_one_day(state, market, d, cfg, selector=selector, n_slots=n_slots)
        trades.extend(r.trades)
        warnings.extend(r.warnings)
        route_events.extend(r.route_events)
        equity[d] = r.equity

    # residual-settlement finalizer (batch-only; the live bot never runs this)
    cash = state.cash
    residual_settled = False
    final_shares = {}
    for pos in state.positions:
        if pos["short"] is not None:
            last = dates[-1]
            day_chain = market.chain(pos["ticker"], last)
            mk = option_mark(day_chain, last, pos["short"]["contract"]) \
                if day_chain is not None else None
            mid = mk.mid if mk is not None else pos["short"]["last_mid"]
            cash -= mid * mult * pos["short"]["contracts"]
            residual_settled = True
        if pos["shares"]:
            final_shares[pos["ticker"]] = final_shares.get(pos["ticker"], 0) + pos["shares"]
    return PortfolioResult(pd.Series(equity), trades, cash, final_shares,
                           residual_settled, days_flat=state.days_flat,
                           warnings=warnings,
                           days_shares_uncovered=state.days_shares_uncovered,
                           route_events=route_events,
                           n_campaigns_opened=state.campaign)
```

(If `portfolio.py` still defines its own local `_row_before`, leave it — `run_portfolio_wheel` no longer calls it, but other code may; do not remove it in this task.)

- [ ] **Step 4: Run the isolation test**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_step_one_day.py -v`
Expected: PASS (1 test) — the put expires worthless, campaign dropped, cash unchanged.

- [ ] **Step 5: Run the BYTE-IDENTICAL ANCHOR (the load-bearing gate)**

Run: `.venv/bin/python -m pytest tests/engine_v2/options/test_portfolio.py tests/engine_v2/options/test_portfolio_selector.py tests/engine_v2/options/test_portfolio_nslots.py tests/engine_v2/options/test_portfolio_universe.py -v`
Expected: ALL PASS, unchanged. If any fails, the refactor changed behavior — diff your `step_one_day` body against the original loop (`git show HEAD:src/engine_v2/options/portfolio.py`) line by line; the body must be verbatim except the `market.` swaps. Do NOT weaken the tests.

Then the referee:
Run: `PYTHONPATH=. .venv/bin/python scripts/audit_defense_execution.py --rotation; echo "exit: $?"`
Expected: `exit: 0`.

- [ ] **Step 6: Commit**

```bash
git add src/engine_v2/options/portfolio.py tests/engine_v2/options/test_step_one_day.py
git commit -m "refactor(engine): extract step_one_day + PortfolioState; batch calls it in a loop"
```

---

### Task 3: Full-suite regression

**Files:** none (verification only)

- [ ] **Step 1: Run the full 3.9 suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (prior count + the new market/step tests). Confirms nothing else in the engine regressed on the refactor.

- [ ] **Step 2: Re-confirm both referees**

Run: `PYTHONPATH=. .venv/bin/python scripts/audit_defense_execution.py --rotation; echo "rotation exit: $?"`
Expected: `rotation exit: 0`.

- [ ] **Step 3: Commit any residual (usually nothing)**

```bash
git add -A && git commit -m "chore: step extraction — suite green" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- `Market` interface + `BatchMarket` (chain/spot/settle_price/regime_row/eligible/universe; history reach) → Task 1. ✓
- `PortfolioState` + `StepResult` + `step_one_day` (loop body verbatim, market-ified, mutate+return) → Task 2. ✓
- `run_portfolio_wheel` reduced to shell (validate → preload → BatchMarket → loop → finalizer → PortfolioResult unchanged) → Task 2. ✓
- Byte-identical anchor (existing suite + --rotation) → Task 2 Step 5 + Task 3. ✓
- step isolation unit + BatchMarket unit → Tasks 2, 1. ✓
- Reserved guard / selector / n_slots validation stay in run_portfolio_wheel → Task 2 (kept above the preload). ✓

**Placeholder scan:** No TBD/TODO; complete code for market.py, step_one_day, and the shell; tests assert real values.

**Type consistency:** `BatchMarket(chains, regime_states, clean_start, universe)` consistent Task 1↔2; `step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult` consistent; `PortfolioState` fields match what step reads/writes and what the shell passes; `PortfolioResult` fields unchanged (incl. `n_campaigns_opened=state.campaign`).

**Two risks flagged for the executor:**
1. **`positions[:] =` vs `positions =`** in `step_one_day`'s drop-finished line: it MUST be `positions[:] = [...]` (in-place) OR reassign then `state.positions = positions` at the end — the plan repacks `state.positions = positions` at the end, and uses `positions[:]` to keep the same list object through the routing loop. Keep as written.
2. **The anchor is the whole point** — Task 2 Step 5 is not optional. If it fails, the body diverged from the original; fix the body, never the test.
