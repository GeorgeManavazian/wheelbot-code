# Live Data Adapter (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn live Schwab JSON into the engine's native shapes — a daily-closes Series and an option-chain DataFrame — so the paper bot can run the existing strategy on live data.

**Architecture:** New `live/` package (py3.12, `.venv-live`). Pure mapping functions (`closes_from_json`, `chain_from_json`) do the Schwab→engine translation and are unit-tested offline against saved fixtures; thin fetch wrappers (`daily_closes`, `chain_frame`) add the client call. A price-diverse universe list and a throttle helper round it out.

**Tech Stack:** Python 3.12, `schwab-py 1.5.1`, pandas, pytest. Runs on `.venv-live` only.

## Global Constraints

- **Run `live/` tests with `.venv-live` (py3.12):** `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`. The 3.9 backtest suite has `testpaths = tests` in `pytest.ini`, so a bare `pytest` never collects `live/` — keep all live tests under `live/`, never under `tests/`.
- Engine chain columns (exact, ordered): `["date","expiry","strike","right","dte","delta","bid","ask","mid","underlying"]`.
- Mapping rules: `mid` = Schwab `mark`; `right` = `"P"`; `expiry` = the `putExpDateMap` key's date part (`"2026-07-17:0"` → `2026-07-17`); `dte` = `daysToExpiration`; `delta` kept signed (puts negative); `underlying` = top-level `underlyingPrice`. **Skip a contract if** its `delta` is missing/`"NaN"`/NaN, or `bid <= 0`, or `ask <= 0` (illiquid placeholders).
- Fixtures (real GDX): `live/fixtures/price_history_gdx.json` (5070 candles, first 2006-05-23 close 37.96, last 2026-07-17 close 71.32), `live/fixtures/option_chain_gdx_puts.json` (underlying 71.32, first expiry key `"2026-07-17:0"`, first strike `"66.0"` has bid 0.0 → skipped).
- Universe must be price-diverse (low $5–50 / mid $50–150 / high $150–700+), sector-varied, for $5k–$500k account testing.
- schwab-py calls: `client.get_price_history_every_day(ticker)`; `client.get_option_chain(ticker, contract_type=Client.Options.ContractType.PUT, strike_count=, from_date=, to_date=)`. Both return an httpx `Response` (`.status_code`, `.json()`).
- Branch: `live-data-adapter`. Spec: `docs/superpowers/specs/2026-07-17-live-data-adapter-design.md`.

---

### Task 1: `live/` package + price-diverse universe

**Files:**
- Create: `live/__init__.py`, `live/universe.py`
- Test: `live/tests/__init__.py`, `live/tests/test_universe.py`

**Interfaces:**
- Produces: `live.universe.UNIVERSE: list[str]` — ~150–200 unique upper-case tickers spanning price tiers and sectors.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_universe.py
from live.universe import UNIVERSE


def test_universe_is_clean_and_sized():
    assert 120 <= len(UNIVERSE) <= 250
    assert all(isinstance(t, str) and t.isupper() and t.strip() == t for t in UNIVERSE)
    assert len(UNIVERSE) == len(set(UNIVERSE)), "no duplicate tickers"


def test_universe_spans_price_tiers_and_sectors():
    # cheap names a ~$5k account can trade (one put < ~$5k collateral)
    for cheap in ("GDX", "SLV", "F", "SOFI"):
        assert cheap in UNIVERSE, f"{cheap} (low-price inventory) missing"
    # expensive names only larger accounts reach
    for pricey in ("SPY", "QQQ"):
        assert pricey in UNIVERSE, f"{pricey} (high-price) missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.universe'`.

- [ ] **Step 3: Create the package + universe**

Create `live/__init__.py` (empty) and `live/tests/__init__.py` (empty). Create `live/universe.py`:

```python
"""Live trading universe: price-diverse, liquid, optionable. Spans price tiers so
the bot is testable across $5k-$500k accounts (one put ties up strike*100
collateral, so small accounts can only trade cheap underlyings). Owner-editable.
Filtering to what an account can afford is a decision-layer concern (sub-project B)."""

# Low ($5-50): the inventory a ~$5k account can actually trade.
_LOW = [
    "GDX", "SLV", "XOP", "EWZ", "GDXJ", "XLF", "KRE", "EEM", "FXI", "USO",
    "SOFI", "F", "PLTR", "NIO", "RIG", "SNAP", "T", "BAC", "WFC", "PFE",
    "INTC", "CSCO", "KVUE", "VALE", "GOLD", "CCL", "MARA", "RIOT", "CHPT",
    "LYFT", "HOOD", "AAL", "UAL", "PBR", "KGC", "AGNC", "NOK", "SIRI",
]
# Mid ($50-150).
_MID = [
    "GLD", "XBI", "XLE", "XLK", "SMH", "IWM", "DIA", "EFA", "TLT", "HYG",
    "AMD", "BABA", "PYPL", "UBER", "DIS", "KO", "PEP", "CVX", "XOM", "WMT",
    "SBUX", "NKE", "MU", "C", "GM", "COIN", "SNOW", "SHOP", "MRNA", "ROKU",
]
# High ($150-700+): only larger accounts reach these.
_HIGH = [
    "SPY", "QQQ", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA",
    "NFLX", "AVGO", "CRM", "ADBE", "COST", "HD", "UNH", "LLY", "V", "MA",
    "JPM", "GS", "CAT", "BA", "AMAT", "NOW", "PANW", "LRCX", "ISRG", "MELI",
]
UNIVERSE = _LOW + _MID + _HIGH
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_universe.py -v`
Expected: PASS (2 tests). (If the size assert fails, adjust the lists to land in 120–250.)

- [ ] **Step 5: Verify the 3.9 suite still ignores `live/`**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: the existing suite count, unchanged — `pytest.ini`'s `testpaths = tests` means `live/` is never collected by the 3.9 run.

- [ ] **Step 6: Commit**

```bash
git add live/__init__.py live/universe.py live/tests/__init__.py live/tests/test_universe.py
git commit -m "feat(live): price-diverse trading universe + live package skeleton"
```

---

### Task 2: `daily_closes` — Schwab price history → closes Series

**Files:**
- Create: `live/data.py`
- Test: `live/tests/test_data_closes.py`

**Interfaces:**
- Produces:
  - `closes_from_json(payload: dict) -> pd.Series` — pure mapping; datetime-ms → normalized-date index, `close` values, sorted, de-duplicated (keep last). Empty `candles` → empty float Series named `"close"`.
  - `daily_closes(client, ticker: str) -> pd.Series` — fetch wrapper: `client.get_price_history_every_day(ticker)`, raise on non-200, else `closes_from_json(resp.json())`.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_data_closes.py
import json
import pandas as pd
from live.data import closes_from_json

FIX = "live/fixtures/price_history_gdx.json"


def test_closes_mapping_shape_and_values():
    s = closes_from_json(json.load(open(FIX)))
    assert isinstance(s, pd.Series)
    assert isinstance(s.index, pd.DatetimeIndex)
    assert s.is_monotonic_increasing            # sorted by date
    assert not s.index.has_duplicates           # de-duplicated
    # spot-check the fixture's known first + last (dates normalized to midnight)
    assert s.loc[pd.Timestamp("2006-05-23")] == 37.96
    assert s.loc[pd.Timestamp("2026-07-17")] == 71.32
    assert s.name == "close"


def test_closes_empty_candles():
    s = closes_from_json({"candles": []})
    assert isinstance(s, pd.Series) and len(s) == 0 and s.name == "close"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_data_closes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.data'`.

- [ ] **Step 3: Write the implementation**

Create `live/data.py`:

```python
"""Schwab JSON -> engine-native shapes. Pure mapping functions (offline-testable)
plus thin client-fetch wrappers. Data-only; no order code. py3.12 (.venv-live)."""
from __future__ import annotations
import datetime as dt
import pandas as pd

_CHAIN_COLS = ["date", "expiry", "strike", "right", "dte",
               "delta", "bid", "ask", "mid", "underlying"]


def closes_from_json(payload: dict) -> pd.Series:
    """price_history JSON -> daily close Series (normalized-date index, sorted,
    de-duplicated keep-last). Feeds regime_series()."""
    candles = payload.get("candles", []) or []
    if not candles:
        return pd.Series([], dtype=float, name="close")
    df = pd.DataFrame(candles)
    idx = pd.to_datetime(df["datetime"], unit="ms").dt.normalize()
    s = pd.Series(df["close"].astype(float).to_numpy(), index=idx, name="close")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def daily_closes(client, ticker: str) -> pd.Series:
    r = client.get_price_history_every_day(ticker)
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} price_history -> HTTP {r.status_code}")
    return closes_from_json(r.json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_data_closes.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add live/data.py live/tests/test_data_closes.py
git commit -m "feat(live): daily_closes — Schwab price history -> engine close Series"
```

---

### Task 3: `chain_frame` — Schwab option chain → engine DataFrame

**Files:**
- Modify: `live/data.py` (add `chain_from_json` + `chain_frame`)
- Test: `live/tests/test_data_chain.py`

**Interfaces:**
- Consumes: `_CHAIN_COLS` (Task 2).
- Produces:
  - `chain_from_json(payload: dict, obs_date) -> pd.DataFrame` — pure mapping to `_CHAIN_COLS`. Skips contracts with missing/NaN delta or `bid<=0` or `ask<=0`. Empty `putExpDateMap` → empty DataFrame with `_CHAIN_COLS`.
  - `chain_frame(client, ticker, target_dte: int, strike_count: int = 12, obs_date=None) -> pd.DataFrame` — fetch wrapper: bounded PUT chain over `today .. today + target_dte + 20` days, raise on non-200, else `chain_from_json(resp.json(), obs_date or today)`.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_data_chain.py
import json
import pandas as pd
from live.data import chain_from_json, _CHAIN_COLS

FIX = "live/fixtures/option_chain_gdx_puts.json"
OBS = pd.Timestamp("2026-07-17")


def _payload():
    return json.load(open(FIX))


def test_chain_exact_columns_and_types():
    df = chain_from_json(_payload(), OBS)
    assert list(df.columns) == _CHAIN_COLS
    assert (df["right"] == "P").all()
    assert (df["date"] == OBS).all()
    assert (df["underlying"] == 71.32).all()
    assert (df["delta"] < 0).all()              # puts are negative
    assert (df["bid"] <= df["ask"]).all()
    assert (df["bid"] > 0).all() and (df["ask"] > 0).all()   # placeholders skipped


def test_chain_skips_zero_bid_placeholder():
    # fixture's first contract (strike 66.0) has bid 0.0 -> must be absent
    df = chain_from_json(_payload(), OBS)
    zero_bid_66 = df[(df["strike"] == 66.0) & (df["dte"] == 0)]
    assert len(zero_bid_66) == 0


def test_chain_spot_check_a_live_contract():
    # find the first contract in the fixture with bid>0 and verify its row maps 1:1
    payload = _payload()
    exp_key, strikes = next(iter(payload["putExpDateMap"].items()))
    picked = None
    for strike_key, contracts in strikes.items():
        ct = contracts[0]
        if ct["bid"] > 0 and ct["ask"] > 0 and ct["delta"] == ct["delta"]:
            picked = (exp_key, ct); break
    assert picked is not None, "fixture should contain a positive-bid put"
    exp_key, ct = picked
    df = chain_from_json(payload, OBS)
    row = df[(df["expiry"] == pd.Timestamp(exp_key.split(":")[0]))
             & (df["strike"] == float(ct["strikePrice"]))].iloc[0]
    assert row["bid"] == ct["bid"]
    assert row["ask"] == ct["ask"]
    assert row["mid"] == ct["mark"]
    assert row["delta"] == ct["delta"]
    assert row["dte"] == ct["daysToExpiration"]


def test_chain_empty_map():
    df = chain_from_json({"underlyingPrice": 10.0, "putExpDateMap": {}}, OBS)
    assert list(df.columns) == _CHAIN_COLS and len(df) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_data_chain.py -v`
Expected: FAIL — `ImportError: cannot import name 'chain_from_json'`.

- [ ] **Step 3: Add the implementation to `live/data.py`**

Append to `live/data.py`:

```python
def _num(v):
    """float or None. Handles Schwab's 'NaN' string and actual NaN."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def chain_from_json(payload: dict, obs_date) -> pd.DataFrame:
    """Bounded PUT option-chain JSON -> engine chain rows. One row per contract;
    skips illiquid placeholders (missing/NaN delta, or bid<=0, or ask<=0)."""
    obs = pd.Timestamp(obs_date).normalize()
    und = _num(payload.get("underlyingPrice"))
    rows = []
    for exp_key, strikes in (payload.get("putExpDateMap") or {}).items():
        expiry = pd.Timestamp(exp_key.split(":")[0]).normalize()
        for _strike_key, contracts in strikes.items():
            ct = contracts[0]
            delta = _num(ct.get("delta"))
            bid, ask = _num(ct.get("bid")), _num(ct.get("ask"))
            if delta is None or bid is None or ask is None or bid <= 0 or ask <= 0:
                continue
            rows.append({
                "date": obs, "expiry": expiry,
                "strike": float(ct["strikePrice"]), "right": "P",
                "dte": int(ct["daysToExpiration"]), "delta": delta,
                "bid": bid, "ask": ask, "mid": _num(ct.get("mark")),
                "underlying": und,
            })
    return pd.DataFrame(rows, columns=_CHAIN_COLS)


def chain_frame(client, ticker: str, target_dte: int, strike_count: int = 12,
                obs_date=None) -> pd.DataFrame:
    from schwab.client import Client
    today = dt.date.today()
    r = client.get_option_chain(
        ticker,
        contract_type=Client.Options.ContractType.PUT,
        strike_count=strike_count,
        from_date=today,
        to_date=today + dt.timedelta(days=target_dte + 20),
    )
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} option_chain -> HTTP {r.status_code}")
    return chain_from_json(r.json(), obs_date or today)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_data_chain.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add live/data.py live/tests/test_data_chain.py
git commit -m "feat(live): chain_frame — Schwab option chain -> engine chain DataFrame"
```

---

### Task 4: Throttle helper + live smoke script

**Files:**
- Modify: `live/data.py` (add `throttle`)
- Create: `live/smoke_pull.py`
- Test: `live/tests/test_throttle.py`

**Interfaces:**
- Produces:
  - `throttle(fn, *args, retries=2, **kwargs)` — calls `fn`; if it returns a Response with `status_code in (429, 502)`, sleeps briefly and retries up to `retries` times; returns the last Response. (Rate-limit pacing is the caller's loop concern; this is the per-call transient-retry guard.)
  - `live/smoke_pull.py` — manual (non-pytest) end-to-end eyeball on live Schwab.

- [ ] **Step 1: Write the failing test**

```python
# live/tests/test_throttle.py
from live.data import throttle


class _Resp:
    def __init__(self, code): self.status_code = code


def test_throttle_retries_on_502_then_succeeds():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        return _Resp(502 if calls["n"] < 2 else 200)
    r = throttle(flaky, retries=3)
    assert r.status_code == 200
    assert calls["n"] == 2                        # retried once


def test_throttle_gives_up_after_retries():
    calls = {"n": 0}
    def always_502():
        calls["n"] += 1
        return _Resp(502)
    r = throttle(always_502, retries=2)
    assert r.status_code == 502
    assert calls["n"] == 3                         # initial + 2 retries


def test_throttle_passes_through_200_immediately():
    calls = {"n": 0}
    def ok():
        calls["n"] += 1
        return _Resp(200)
    assert throttle(ok).status_code == 200 and calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_throttle.py -v`
Expected: FAIL — `ImportError: cannot import name 'throttle'`.

- [ ] **Step 3: Add `throttle` to `live/data.py`**

Append to `live/data.py`:

```python
import time

def throttle(fn, *args, retries: int = 2, backoff: float = 1.0, **kwargs):
    """Call fn(*args, **kwargs); on a transient 429/502 Response, sleep and retry
    up to `retries` times. Returns the final Response (caller checks status)."""
    r = fn(*args, **kwargs)
    attempts = 0
    while getattr(r, "status_code", None) in (429, 502) and attempts < retries:
        time.sleep(backoff)
        r = fn(*args, **kwargs)
        attempts += 1
    return r
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_throttle.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the live smoke script**

Create `live/smoke_pull.py`:

```python
"""Manual end-to-end eyeball (NOT a pytest): pulls live GDX from Schwab, maps it,
prints the head of the close series + the mapped chain. Run:
  PYTHONPATH=. .venv-live/bin/python live/smoke_pull.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
from schwab_client import get_client  # noqa: E402
from live.data import daily_closes, chain_frame  # noqa: E402


def main():
    c = get_client()
    s = daily_closes(c, "GDX")
    print(f"daily_closes GDX: {len(s)} rows; last: {s.index[-1].date()} = {s.iloc[-1]}")
    df = chain_frame(c, "GDX", target_dte=11, strike_count=12)
    print(f"chain_frame GDX: {len(df)} rows; cols {list(df.columns)}")
    if len(df):
        print(df.sort_values("strike").tail(5).to_string(index=False))
    print("\nLive adapter OK." if len(s) and len(df) else "\nNo data — check the token.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the full `live/` suite + commit**

Run: `PYTHONPATH=. .venv-live/bin/python -m pytest live/ -v`
Expected: PASS (all Task 1–4 tests). Then:

```bash
git add live/data.py live/smoke_pull.py live/tests/test_throttle.py
git commit -m "feat(live): throttle retry helper + live smoke-pull script"
```

---

## Self-Review

**Spec coverage:**
- `live/` package, py3.12-only, 3.9 suite excluded (testpaths=tests) → Task 1 + Step 5 verify. ✓
- Price-diverse tiered universe → Task 1. ✓
- `daily_closes` / `closes_from_json` mapping + empty handling → Task 2. ✓
- `chain_frame` / `chain_from_json` exact columns, mid=mark, right=P, expiry-from-key, skip NaN-delta/nonpositive-bid-ask, empty handling → Task 3. ✓
- Throttle 120/min + 429/502 retry → Task 4 (per-call transient retry; the 120/min pacing is the caller/runner's loop, sub-project C, noted in the interface). ✓
- Offline fixture tests (no live API in the suite) + live smoke script → Tasks 2–4. ✓

**Placeholder scan:** No TBD/TODO; every code step complete; every test asserts real values (fixture-derived).

**Type consistency:** `_CHAIN_COLS` defined Task 2, used Tasks 3; `closes_from_json`/`daily_closes` signatures consistent; `chain_from_json(payload, obs_date)` / `chain_frame(client, ticker, target_dte, strike_count, obs_date)` consistent between definition and tests; `throttle(fn, *args, retries, ...)` consistent.

**One risk flagged for the executor:** the universe size assert (120–250) and the required-names assert must both hold — if the drafted lists fall outside 120 or a required name (GDX/SLV/F/SOFI/SPY/QQQ) is missing, adjust the lists (they are the editable deliverable, not load-bearing logic). The mapping tests are the load-bearing ones.
