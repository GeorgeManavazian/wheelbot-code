# Live Paper Runner + State Store (Sub-project B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shared `step_one_day` brain run live and stateful — persist the portfolio to JSON, wrap live Schwab data in a `LiveMarket`, and run one paper day end-to-end.

**Architecture:** `live/state.py` serializes `PortfolioState` (incl. nested `Contract`) to JSON atomically. `LiveMarket` implements the `Market` interface over injected `closes_fn`/`chain_fn` (real adapter in prod, fixtures in tests), pulling chains only for held ∪ good-to-rent tickers. `live/run_daily.py` wires load → `LiveMarket` → `step_one_day` → append trades → save.

**Tech Stack:** Python 3.12 (`.venv-live`), pandas, pytest. `live/` is py3.12-only.

## Global Constraints

- Run `live/` tests with `.venv-live`: `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`. The 3.9 suite ignores `live/` (pytest.ini `testpaths = tests`).
- `PortfolioState` (from `src.engine_v2.options.portfolio`): fields `cash, positions, campaign, days_flat, days_shares_uncovered, prev_d`. `Contract` (from `src.engine_v2.options.chain`): frozen dataclass `root, expiry, strike, right`.
- Position dict shape: `{ticker, shares, phase, basis, premium, campaign, last_spot, short}` where `short` is `None` or `{contract: Contract, contracts, credit, last_mid}`.
- `step_one_day(state, market, day, cfg, *, selector, n_slots) -> StepResult` (StepResult: `trades, equity, warnings, route_events`).
- Market interface: `chain(tk,day)`, `spot(tk,day,fallback)`, `settle_price(tk,expiry)`, `regime_row(tk,day)`, `eligible(tk,day)`, `universe`.
- Frozen strategy cfg: `WheelConfig(put_delta=0.30, call_delta=0.50, target_dte=11, take_profit_pct=0.60, call_min_strike="basis", starting_capital=<account>)`.
- Data-only. No order code. Store under `data/live/` (gitignored).
- Fixtures for tests: `live/fixtures/price_history_gdx.json`, `live/fixtures/option_chain_gdx_puts.json` (underlying 71.32).
- Branch: `live-paper-runner`. Spec: `docs/superpowers/specs/2026-07-17-live-paper-runner-design.md`.

---

### Task 1: `live/state.py` — JSON persistence

**Files:**
- Create: `live/state.py`
- Test: `live/tests/test_state.py`
- Modify: `.gitignore` (add `data/live/`)

**Interfaces:**
- Produces: `state_to_dict(state) -> dict`; `state_from_dict(d) -> PortfolioState`; `save_state(state, path)` (atomic); `load_state(path) -> PortfolioState | None`.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_state.py
import os
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState
from live.state import state_to_dict, state_from_dict, save_state, load_state


def _state():
    put = Contract("GDX", pd.Timestamp("2026-01-15"), 30.0, "P")
    open_put = {"ticker": "GDX", "shares": 0, "phase": "PUT", "basis": None,
                "premium": 99.0, "campaign": 1, "last_spot": 33.0,
                "short": {"contract": put, "contracts": 1, "credit": 1.0, "last_mid": 0.9}}
    assigned = {"ticker": "SLV", "shares": 100, "phase": "CALL", "basis": 25.0,
                "premium": 40.0, "campaign": 2, "last_spot": 24.0, "short": None}
    return PortfolioState(cash=95_000.0, positions=[open_put, assigned], campaign=2,
                          days_flat=3, days_shares_uncovered=1,
                          prev_d=pd.Timestamp("2026-07-16"))


def test_round_trip_preserves_everything():
    s = _state()
    s2 = state_from_dict(state_to_dict(s))
    assert s2.cash == s.cash and s2.campaign == s.campaign
    assert s2.days_flat == 3 and s2.days_shares_uncovered == 1
    assert s2.prev_d == pd.Timestamp("2026-07-16")
    # open-put campaign: nested Contract survives
    c = s2.positions[0]["short"]["contract"]
    assert isinstance(c, Contract)
    assert (c.root, c.strike, c.right) == ("GDX", 30.0, "P")
    assert c.expiry == pd.Timestamp("2026-01-15")
    assert s2.positions[0]["short"]["credit"] == 1.0
    # assigned-shares campaign: short is None, shares/basis survive
    assert s2.positions[1]["short"] is None
    assert s2.positions[1]["shares"] == 100 and s2.positions[1]["basis"] == 25.0


def test_save_load_atomic_round_trip(tmp_path):
    p = str(tmp_path / "sub" / "state.json")   # nested dir must be created
    save_state(_state(), p)
    assert os.path.exists(p)
    loaded = load_state(p)
    assert loaded.cash == 95_000.0 and len(loaded.positions) == 2


def test_load_absent_is_none(tmp_path):
    assert load_state(str(tmp_path / "nope.json")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.state'`.

- [ ] **Step 3: Write `live/state.py`**

```python
"""PortfolioState <-> JSON. Positions carry nested Contract dataclasses, so
(de)serialization is explicit. save_state is atomic (temp + os.replace) so a
crash mid-write can't corrupt a real portfolio."""
from __future__ import annotations
import json
import os
import tempfile
import pandas as pd
from src.engine_v2.options.chain import Contract
from src.engine_v2.options.portfolio import PortfolioState

_POS_SCALARS = ("ticker", "shares", "phase", "basis", "premium", "campaign", "last_spot")


def _contract_to_dict(c):
    return {"root": c.root, "expiry": pd.Timestamp(c.expiry).isoformat(),
            "strike": c.strike, "right": c.right}


def _contract_from_dict(d):
    return Contract(d["root"], pd.Timestamp(d["expiry"]), d["strike"], d["right"])


def _pos_to_dict(p):
    d = {k: p[k] for k in _POS_SCALARS}
    sh = p["short"]
    d["short"] = None if sh is None else {
        "contract": _contract_to_dict(sh["contract"]),
        "contracts": sh["contracts"], "credit": sh["credit"], "last_mid": sh["last_mid"]}
    return d


def _pos_from_dict(d):
    p = {k: d[k] for k in _POS_SCALARS}
    sh = d["short"]
    p["short"] = None if sh is None else {
        "contract": _contract_from_dict(sh["contract"]),
        "contracts": sh["contracts"], "credit": sh["credit"], "last_mid": sh["last_mid"]}
    return p


def state_to_dict(state) -> dict:
    return {
        "cash": state.cash, "campaign": state.campaign,
        "days_flat": state.days_flat,
        "days_shares_uncovered": state.days_shares_uncovered,
        "prev_d": None if state.prev_d is None else pd.Timestamp(state.prev_d).isoformat(),
        "positions": [_pos_to_dict(p) for p in state.positions],
    }


def state_from_dict(d) -> PortfolioState:
    return PortfolioState(
        cash=d["cash"], positions=[_pos_from_dict(p) for p in d["positions"]],
        campaign=d["campaign"], days_flat=d["days_flat"],
        days_shares_uncovered=d["days_shares_uncovered"],
        prev_d=None if d["prev_d"] is None else pd.Timestamp(d["prev_d"]))


def save_state(state, path):
    d = state_to_dict(state)
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirn, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, path)


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return state_from_dict(json.load(f))
```

- [ ] **Step 4: Run test + add gitignore**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_state.py -v`
Expected: PASS (3 tests).
Then append to `.gitignore` (if not present): `data/live/`.

- [ ] **Step 5: Commit**

```bash
git add live/state.py live/tests/test_state.py .gitignore
git commit -m "feat(live): PortfolioState JSON persistence (atomic, nested Contract)"
```

---

### Task 2: `live/market_live.py` — `LiveMarket`

**Files:**
- Create: `live/market_live.py`
- Test: `live/tests/test_market_live.py`, `live/tests/test_step_on_livemarket.py`

**Interfaces:**
- Consumes: `regime_series` (`src.engine_v2.regime.state`), `is_good_renting_weather` (same), `_row_before` (`src.engine_v2.options.market`).
- Produces: `LiveMarket(universe, held_tickers, obs_date, *, closes_fn, chain_fn) ` implementing the Market interface. `closes_fn(ticker) -> pd.Series`, `chain_fn(ticker) -> pd.DataFrame` are injected (prod: wrap `daily_closes`/`chain_frame` + client; tests: fixture readers). A ticker whose `closes_fn` raises is skipped (logged).

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_market_live.py
import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def _closes_fn(tk):
    # every ticker gets the GDX close history (fine for a mapping test)
    return closes_from_json(PH)


def _chain_fn(tk):
    return chain_from_json(OC, OBS)


def _mkt(held=()):
    return LiveMarket(["GDX", "AAA"], set(held), OBS,
                      closes_fn=_closes_fn, chain_fn=_chain_fn)


def test_spot_is_today_close_or_fallback():
    m = _mkt()
    assert m.spot("GDX", OBS, 0.0) == 71.32          # last fixture close
    assert m.spot("GDX", pd.Timestamp("2099-01-01"), 5.0) == 5.0


def test_regime_row_is_prior_day():
    m = _mkt()
    row = m.regime_row("GDX", OBS)
    assert row is not None and "trend" in row


def test_settle_price_none_and_eligible_true():
    m = _mkt()
    assert m.settle_price("GDX", OBS) is None
    assert m.eligible("GDX", OBS) is True


def test_universe_property():
    assert _mkt().universe == ["GDX", "AAA"]


def test_held_ticker_chain_present():
    # a held ticker always gets its chain (needed for management), even if not
    # good-to-rent
    m = _mkt(held=["GDX"])
    assert m.chain("GDX", OBS) is not None
    assert list(m.chain("GDX", OBS).columns)[:3] == ["date", "expiry", "strike"]


def test_pull_failure_skips_ticker():
    def flaky(tk):
        if tk == "BAD":
            raise RuntimeError("BAD price_history -> HTTP 404")
        return closes_from_json(PH)
    m = LiveMarket(["GDX", "BAD"], set(), OBS, closes_fn=flaky, chain_fn=_chain_fn)
    assert "BAD" not in m._closes          # skipped, not fatal
    assert m.spot("GDX", OBS, 0.0) == 71.32
```

```python
# live/tests/test_step_on_livemarket.py
import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def test_step_runs_on_livemarket_end_to_end():
    m = LiveMarket(["GDX"], set(), OBS,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: chain_from_json(OC, OBS))
    cfg = WheelConfig(ticker="GDX", put_delta=0.30, call_delta=0.50,
                      target_dte=11, take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    r = step_one_day(state, m, OBS, cfg, selector="chop", n_slots=1)
    # it ran without error and marked equity; state advanced its date
    assert isinstance(r.trades, list)
    assert isinstance(r.equity, float)
    assert state.prev_d == OBS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_market_live.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.market_live'`.

- [ ] **Step 3: Write `live/market_live.py`**

```python
"""LiveMarket: the Market interface backed by TODAY's Schwab data + carried
prices, so the same step_one_day that ran the backtest runs live. Efficient by
construction — pulls chains only for held ∪ good-to-rent tickers (step's routing
loop asks chain() for every universe ticker; a non-candidate returns None and is
skipped, same outcome as the regime check). closes_fn/chain_fn are injected so
tests use fixtures, prod uses the Schwab client + adapter."""
from __future__ import annotations
import pandas as pd
from src.engine_v2.regime.state import regime_series, is_good_renting_weather
from src.engine_v2.options.market import _row_before


class LiveMarket:
    def __init__(self, universe, held_tickers, obs_date, *, closes_fn, chain_fn):
        self._universe = list(universe)
        self._obs = pd.Timestamp(obs_date).normalize()
        self._closes = {}   # ticker -> close Series
        self._rows = {}     # ticker -> prior-day regime row (or None)
        self._chains = {}   # ticker -> chain df (only pulled ones)
        self.skipped = []   # tickers whose pull failed

        good = set()
        for tk in self._universe:
            try:
                s = closes_fn(tk)
            except Exception as e:   # one bad symbol must not stop the bot
                self.skipped.append((tk, str(e)))
                continue
            self._closes[tk] = s
            row = _row_before(regime_series(s), self._obs)
            self._rows[tk] = row
            if is_good_renting_weather(row):
                good.add(tk)

        for tk in (set(held_tickers) | good) & set(self._closes):
            try:
                self._chains[tk] = chain_fn(tk)
            except Exception as e:
                self.skipped.append((tk, str(e)))

    @property
    def universe(self):
        return self._universe

    def chain(self, ticker, day):
        return self._chains.get(ticker)

    def spot(self, ticker, day, fallback):
        s = self._closes.get(ticker)
        if s is None:
            return fallback
        d = pd.Timestamp(day).normalize()
        return float(s[d]) if d in s.index else fallback

    def settle_price(self, ticker, expiry):
        return None   # live runs daily; step settles at today's spot

    def regime_row(self, ticker, day):
        return self._rows.get(ticker)

    def eligible(self, ticker, day):
        return True
```

- [ ] **Step 4: Run both tests**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_market_live.py live/tests/test_step_on_livemarket.py -v`
Expected: PASS (6 + 1 tests) — `LiveMarket` returns correct values, held chains present, pull failures skipped, and `step_one_day` runs on it end-to-end.

- [ ] **Step 5: Commit**

```bash
git add live/market_live.py live/tests/test_market_live.py live/tests/test_step_on_livemarket.py
git commit -m "feat(live): LiveMarket — Market seam over live data (chains for held+good-to-rent)"
```

---

### Task 3: `live/run_daily.py` — the daily paper-step

**Files:**
- Create: `live/run_daily.py`
- Test: `live/tests/test_run_daily.py`

**Interfaces:**
- Consumes: `LiveMarket` (Task 2), `save_state`/`load_state` (Task 1), `step_one_day`, `WheelConfig`.
- Produces: `paper_step(state, market, cfg, n_slots, trades_path, state_path) -> StepResult` — runs step, appends `result.trades` to `trades_path` (JSONL), saves state to `state_path`. Plus a `main()` CLI (`--n`, `--capital`, `--smoke`) that builds the real `LiveMarket` from the Schwab client + adapter. `--smoke` runs a 3-ticker universe.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_run_daily.py
import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from live.run_daily import paper_step
from live.state import load_state
from src.engine_v2.options.portfolio import PortfolioState
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def test_paper_step_persists_state_and_trades(tmp_path):
    m = LiveMarket(["GDX"], set(), OBS,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: chain_from_json(OC, OBS))
    cfg = WheelConfig(ticker="GDX", put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    sp = str(tmp_path / "state.json"); tp = str(tmp_path / "trades.jsonl")
    r = paper_step(state, m, cfg, n_slots=1, trades_path=tp, state_path=sp)
    # state.json written and reloadable
    reloaded = load_state(sp)
    assert reloaded is not None and reloaded.prev_d == OBS
    # each trade is one JSON line in trades.jsonl
    with open(tp) as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == len(r.trades)
    if lines:
        assert {"date", "action", "ticker"} <= set(lines[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_run_daily.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.run_daily'`.

- [ ] **Step 3: Write `live/run_daily.py`**

```python
"""Daily paper-step runner. Data-only, simulated fills, NO order code.

  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --n 5 --capital 100000
  PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke   # 3-ticker live test
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import pandas as pd

from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig
from live.state import load_state, save_state
from live.market_live import LiveMarket
from live.universe import UNIVERSE

STATE_PATH = "data/live/state.json"
TRADES_PATH = "data/live/trades.jsonl"

FROZEN = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
              take_profit_pct=0.60, call_min_strike="basis")


def _trade_row(t):
    c = t.contract
    return {"date": pd.Timestamp(t.date).isoformat(), "action": t.action,
            "ticker": c.root, "strike": c.strike, "right": c.right,
            "expiry": pd.Timestamp(c.expiry).isoformat(), "contracts": t.contracts,
            "price": t.price_per_contract, "cash_after": t.cash_after,
            "campaign": t.campaign_id}


def paper_step(state, market, cfg, n_slots, trades_path, state_path):
    day = market._obs
    result = step_one_day(state, market, day, cfg, selector="chop", n_slots=n_slots)
    os.makedirs(os.path.dirname(trades_path) or ".", exist_ok=True)
    with open(trades_path, "a") as f:
        for t in result.trades:
            f.write(json.dumps(_trade_row(t)) + "\n")
    save_state(state, state_path)
    return result


def _live_market(universe, held, obs, client, target_dte):
    from live.data import daily_closes, chain_frame
    return LiveMarket(universe, held, obs,
                      closes_fn=lambda tk: daily_closes(client, tk),
                      chain_fn=lambda tk: chain_frame(client, tk, target_dte))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    universe = ["GDX", "SLV", "XOP"] if args.smoke else UNIVERSE
    obs = pd.Timestamp.today().normalize()
    state = load_state(STATE_PATH) or PortfolioState(cash=args.capital, positions=[])
    held = {p["ticker"] for p in state.positions}
    cfg = WheelConfig(ticker="SPY", starting_capital=args.capital, **FROZEN)

    market = _live_market(universe, held, obs, client, cfg.target_dte)
    if market.skipped:
        print(f"skipped {len(market.skipped)} tickers (pull failures): "
              f"{[s[0] for s in market.skipped][:8]}")
    r = paper_step(state, market, cfg, args.n, TRADES_PATH, STATE_PATH)
    print(f"\n=== paper day {obs.date()}  (N={args.n}, ${args.capital:,.0f}) ===")
    print(f"trades today: {len(r.trades)}  |  open campaigns: {len(state.positions)}  |  "
          f"cash ${state.cash:,.0f}  |  equity ${r.equity:,.0f}")
    for t in r.trades:
        print(f"  {t.action:<12} {t.contract.root} {t.contract.strike}{t.contract.right} "
              f"x{t.contracts} @ {t.price_per_contract}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_run_daily.py -v`
Expected: PASS (1 test) — `paper_step` writes reloadable state + a JSONL trade per trade.

- [ ] **Step 5: Commit**

```bash
git add live/run_daily.py live/tests/test_run_daily.py
git commit -m "feat(live): daily paper-step runner (load -> step -> append trades -> save state)"
```

---

### Task 4: Full `live/` suite regression + live smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full `live/` suite**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`
Expected: all green (B1-adapter tests + the new state/market/runner tests).

- [ ] **Step 2: Live smoke (manual, requires a valid Schwab token)**

Run: `PYTHONPATH=. .venv-live/bin/python live/run_daily.py --smoke`
Expected: prints a paper day for the 3-ticker universe, writes `data/live/state.json` + `data/live/trades.jsonl`. (If the token has lapsed, this errors on the Schwab call — that's a token issue, not a code failure; note it and move on.)

- [ ] **Step 3: Confirm the 3.9 suite still ignores `live/`**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -2`
Expected: unchanged 3.9 count (live/ not collected).

- [ ] **Step 4: Commit any residual**

```bash
git add -A && git commit -m "chore: live paper runner — suite green" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- `state.py` (to/from dict incl nested Contract, atomic save, load-absent-None) → Task 1. ✓
- `LiveMarket` (closes→regime→good-to-rent→chains for held∪good; chain/spot/settle_price/regime_row/eligible/universe; pull-failure skip) → Task 2. ✓
- `run_daily` (load-or-fresh → LiveMarket → step → append trades JSONL → save → summary; CLI --n/--capital/--smoke) → Task 3. ✓
- Injected closes_fn/chain_fn for fixture testing → Task 2. ✓
- Data-only, gitignore data/live/ → Tasks 1,3. ✓
- Error handling (skip on pull failure, atomic save, load-absent) → Tasks 1,2. ✓

**Placeholder scan:** No TBD/TODO; complete code; tests assert real values.

**Type consistency:** `PortfolioState`/`Contract` fields consistent; `LiveMarket(universe, held_tickers, obs_date, *, closes_fn, chain_fn)` consistent Tasks 2/3; `paper_step(state, market, cfg, n_slots, trades_path, state_path)` consistent; `step_one_day(..., *, selector, n_slots)` matches B1.

**Two risks flagged for the executor:**
1. `LiveMarket._obs` is read by `paper_step` (`market._obs`) — it must stay the normalized obs Timestamp set in `__init__`. Keep the attribute name.
2. The `test_step_on_livemarket` / `test_run_daily` tests use the GDX fixture whose weather may or may not be good-to-rent — if GDX is NOT good-to-rent on the fixture date, `n_slots=1` opens no position and `r.trades` is empty; the tests assert *shape* (list/float, persisted state), not that a trade fired, so they pass either way. Do not strengthen them to require a trade.
