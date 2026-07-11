# Wheel Sub-project 1 — SPY Options Data + Primitives — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. NOTE: the owner wants a full audit + explicit approval after EVERY task — do NOT run tasks continuously.

**Goal:** Pull real SPY option chains (with greeks) from the local ThetaData terminal, normalize them, commit a small fixture, and expose pure strike-selection/marking primitives — the foundation the Wheel engine (sub-project 2) composes.

**Architecture:** A thin REST client for the running terminal (`127.0.0.1:25503`, CSV, v3 paths) → a normalizer to a tidy `OptionsChain` → a CLI puller that writes a gitignored cache + a committed fixture → 4 pure primitives. New package `src/engine_v2/options/`, isolated from the equity engine and the gate.

**Tech Stack:** Python 3.9, pandas, urllib (stdlib), pytest. `.venv/bin/python`, `.venv/bin/pytest`.

## Global Constraints

- Terminal REST base `http://127.0.0.1:25503`; v3 paths (`/v3/option/...`); responses are CSV; dates ISO `YYYY-MM-DD`. Reference: `docs/thetadata-v3-access.md`.
- Normalized `OptionsChain` columns EXACTLY: `date` (tz-naive Timestamp), `expiry` (Timestamp), `dte` (int), `strike` (float), `right` ("P"|"C"), `bid`, `ask`, `mid`, `close`, `delta`, `iv`, `underlying`.
- `right` normalization: raw "CALL"→"C", "PUT"→"P".
- Do NOT import the equity `_simulate`, the gate, `compute_verdict`, or `orchestrator.run_backtest` anywhere in `src/engine_v2/options/`.
- The gitignored cache `data/options/` is never committed; only `fixtures/spy_options_small.parquet` is.
- The terminal must be running for the client's live smoke test and the Task 3 pull. Liveness gate: `ThetaClient.is_up()`. If down, start it: `PATH=/opt/homebrew/opt/openjdk/bin:$PATH nohup java -jar ~/ThetaTerminal/ThetaTerminalv3u.jar >~/ThetaTerminal/terminal.log 2>&1 &` (key is in `~/ThetaTerminal/.env`).
- Every task ends green: `.venv/bin/pytest tests/ -q` passes before commit.

## Real greeks/eod CSV shape (captured live 2026-07-11)

Header (subset used): `symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,bid_size,bid_exchange,bid,bid_condition,ask_size,ask_exchange,ask,ask_condition,delta,...,implied_vol,iv_error,underlying_timestamp,underlying_price`
Example rows:
```
"SPY","2024-01-19",477.000,"CALL",2024-01-16T16:14:46.635,1.74,2.10,0.86,1.26,43870,4393,306,60,1.26,50,1,1,1.27,50,0.3611,...,0.1196,0.0005,2024-01-16T17:15:18.701,474.9300
"SPY","2024-01-19",477.000,"PUT",2024-01-16T16:09:57.619,3.06,4.44,2.20,2.90,13740,1544,306,1,2.96,50,112,69,3.00,50,-0.6497,...,0.1107,-0.0002,2024-01-16T17:15:18.701,474.9300
```
The as-of `date` = date part of `underlying_timestamp` (the ~17:15 ET EOD snapshot). `strike` is already dollars (477.000). `delta` is signed (calls +, puts −).

---

### Task 1: `ThetaClient` — REST client for the terminal

**Files:**
- Create: `src/engine_v2/options/__init__.py` (empty), `src/engine_v2/options/theta_client.py`
- Test: `tests/engine_v2/options/test_theta_client.py`, plus `tests/engine_v2/options/__init__.py` (empty)

**Interfaces:**
- Produces:
  - `class ThetaClient(base_url="http://127.0.0.1:25503", timeout=30)`.
  - `.get_csv(path, **params) -> pd.DataFrame` — GET `base_url+path?params`, raise `ThetaError` on non-200 with status + first body line; parse CSV via `pd.read_csv`.
  - `.is_up() -> bool` — True if a cheap request succeeds.
  - `.list_expirations(symbol) -> list[pd.Timestamp]`.
  - `.chain_greeks_eod(symbol, expiration, start, end, strike_range) -> pd.DataFrame` (raw CSV frame). `expiration/start/end` accept ISO strings or Timestamps (formatted to ISO).
  - `class ThetaError(RuntimeError)`.

- [ ] **Step 1: Write the failing test** (unit: monkeypatch the HTTP layer; no network)

```python
# tests/engine_v2/options/test_theta_client.py
import pandas as pd
import pytest
from src.engine_v2.options.theta_client import ThetaClient, ThetaError

class _Resp:
    def __init__(self, code, body): self.status = code; self._b = body.encode()
    def read(self): return self._b
    def getcode(self): return self.status
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_get_csv_parses(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open", lambda url: _Resp(200, "a,b\n1,2\n3,4\n"))
    df = c.get_csv("/v3/x", symbol="SPY")
    assert list(df.columns) == ["a", "b"]
    assert df.iloc[1]["a"] == 3

def test_get_csv_raises_on_error(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open", lambda url: _Resp(404, "Not Found\n..."))
    with pytest.raises(ThetaError):
        c.get_csv("/v3/x")

def test_list_expirations_parses(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open",
        lambda url: _Resp(200, 'symbol,expiration\n"SPY","2024-01-19"\n"SPY","2024-02-16"\n'))
    exps = c.list_expirations("SPY")
    assert exps == [pd.Timestamp("2024-01-19"), pd.Timestamp("2024-02-16")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_theta_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write the client**

```python
# src/engine_v2/options/theta_client.py
"""Thin REST client for the local ThetaData v3 terminal (CSV responses).
See docs/thetadata-v3-access.md. Isolated from the equity engine and the gate."""
from __future__ import annotations
import io
import urllib.parse, urllib.request
import pandas as pd

class ThetaError(RuntimeError):
    pass

def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")

class ThetaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:25503", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _open(self, url: str):  # seam for tests
        return urllib.request.urlopen(url, timeout=self.timeout)

    def get_csv(self, path: str, **params) -> pd.DataFrame:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}" + (f"?{qs}" if qs else "")
        with self._open(url) as r:
            code = getattr(r, "status", None) or r.getcode()
            body = r.read().decode("utf-8", "replace")
        if code != 200:
            first = body.splitlines()[0] if body else ""
            raise ThetaError(f"HTTP {code} for {path}: {first}")
        return pd.read_csv(io.StringIO(body))

    def is_up(self) -> bool:
        try:
            self.get_csv("/v3/option/list/expirations", symbol="SPY")
            return True
        except Exception:
            return False

    def list_expirations(self, symbol: str) -> list[pd.Timestamp]:
        df = self.get_csv("/v3/option/list/expirations", symbol=symbol)
        return [pd.Timestamp(x) for x in df["expiration"]]

    def chain_greeks_eod(self, symbol, expiration, start, end, strike_range) -> pd.DataFrame:
        return self.get_csv("/v3/option/history/greeks/eod", symbol=symbol,
                            expiration=_iso(expiration), start_date=_iso(start),
                            end_date=_iso(end), strike_range=strike_range)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_theta_client.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Live smoke (manual, not committed as a gating test)**

If the terminal is up, confirm end-to-end:
Run: `.venv/bin/python -c "from src.engine_v2.options.theta_client import ThetaClient as C; c=C(); print('up', c.is_up()); print(len(c.list_expirations('SPY')), 'expirations')"`
Expected: `up True` and a few hundred expirations. (If down, start the terminal per Global Constraints.)

- [ ] **Step 6: Commit**

```bash
git add src/engine_v2/options/__init__.py src/engine_v2/options/theta_client.py tests/engine_v2/options/
git commit -m "feat: ThetaData v3 REST client (options, CSV)"
```

---

### Task 2: Normalizer + `Contract`/`Mark`

**Files:**
- Create: `src/engine_v2/options/chain.py`
- Test: `tests/engine_v2/options/test_chain.py`

**Interfaces:**
- Consumes: raw greeks/eod frame (Task 1 shape).
- Produces:
  - `@dataclass(frozen=True) Contract(root: str, expiry, strike: float, right: str)`.
  - `@dataclass(frozen=True) Mark(bid: float, ask: float, mid: float)`.
  - `normalize_greeks_eod(raw: pd.DataFrame) -> pd.DataFrame` → tidy `OptionsChain` with the exact Global-Constraints columns, sorted by (date, expiry, strike, right), bad rows (bid/ask ≤ 0 or NaN delta) dropped.

- [ ] **Step 1: Write the failing test** (recorded CSV, no network)

```python
# tests/engine_v2/options/test_chain.py
import io
import pandas as pd
from src.engine_v2.options.chain import normalize_greeks_eod, Contract, Mark

RAW = (
 'symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,'
 'bid_size,bid_exchange,bid,bid_condition,ask_size,ask_exchange,ask,ask_condition,'
 'delta,implied_vol,underlying_timestamp,underlying_price\n'
 '"SPY","2024-01-19",477.000,"CALL",2024-01-16T16:14:46,1.74,2.10,0.86,1.26,43870,4393,'
 '306,60,1.26,50,1,1,1.27,50,0.3611,0.1196,2024-01-16T17:15:18,474.93\n'
 '"SPY","2024-01-19",477.000,"PUT",2024-01-16T16:09:57,3.06,4.44,2.20,2.90,13740,1544,'
 '306,1,2.96,50,112,69,3.00,50,-0.6497,0.1107,2024-01-16T17:15:18,474.93\n'
 '"SPY","2024-01-19",999.000,"PUT",2024-01-16T16:00:00,0,0,0,0,0,0,'
 '0,0,0,50,0,0,0,50,-0.99,0.5,2024-01-16T17:15:18,474.93\n'   # bad: bid/ask 0 -> dropped
)

def _norm():
    return normalize_greeks_eod(pd.read_csv(io.StringIO(RAW)))

def test_columns_and_types():
    df = _norm()
    assert list(df.columns) == ["date","expiry","dte","strike","right","bid","ask","mid",
                                "close","delta","iv","underlying"]
    assert set(df["right"]) <= {"P","C"}
    assert df["date"].dt.tz is None

def test_values_and_derived():
    df = _norm().set_index("right")
    assert df.loc["P","mid"] == (2.96 + 3.00) / 2
    assert df.loc["P","underlying"] == 474.93
    # date from underlying_timestamp (2024-01-16); dte to 2024-01-19 = 3
    assert df.loc["P","date"] == pd.Timestamp("2024-01-16")
    assert df.loc["P","dte"] == 3
    assert df.loc["P","iv"] == 0.1107

def test_bad_rows_dropped():
    df = _norm()
    assert (df["strike"] == 999.0).sum() == 0  # zero-bid/ask row removed
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_chain.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `chain.py`**

```python
# src/engine_v2/options/chain.py
"""Normalize ThetaData greeks/eod CSV into the tidy OptionsChain frame, plus the
Contract / Mark value types."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

_RIGHT = {"CALL": "C", "PUT": "P", "C": "C", "P": "P"}
COLUMNS = ["date","expiry","dte","strike","right","bid","ask","mid",
           "close","delta","iv","underlying"]

@dataclass(frozen=True)
class Contract:
    root: str
    expiry: pd.Timestamp
    strike: float
    right: str

@dataclass(frozen=True)
class Mark:
    bid: float
    ask: float
    mid: float

def normalize_greeks_eod(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    date = pd.to_datetime(df["underlying_timestamp"]).dt.normalize()
    expiry = pd.to_datetime(df["expiration"]).dt.normalize()
    out = pd.DataFrame({
        "date": date,
        "expiry": expiry,
        "dte": (expiry - date).dt.days,
        "strike": df["strike"].astype(float),
        "right": df["right"].map(_RIGHT),
        "bid": df["bid"].astype(float),
        "ask": df["ask"].astype(float),
        "mid": (df["bid"].astype(float) + df["ask"].astype(float)) / 2.0,
        "close": df["close"].astype(float),
        "delta": df["delta"].astype(float),
        "iv": df["implied_vol"].astype(float),
        "underlying": df["underlying_price"].astype(float),
    })
    out = out[(out["bid"] > 0) & (out["ask"] > 0) & out["delta"].notna()]
    return out[COLUMNS].sort_values(["date","expiry","strike","right"]).reset_index(drop=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_chain.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/chain.py tests/engine_v2/options/test_chain.py
git commit -m "feat: normalize greeks/eod -> OptionsChain + Contract/Mark"
```

---

### Task 3: `pull_spy_options.py` — puller + committed fixture

**Files:**
- Create: `scripts/pull_spy_options.py`
- Modify: `.gitignore`
- Create (committed artifact): `fixtures/spy_options_small.parquet`
- Test: `tests/engine_v2/options/test_pull_fixture.py`

**Interfaces:**
- Consumes: `ThetaClient`, `normalize_greeks_eod`.
- Produces: `build(client, symbol, start, end, strike_range, dte_max) -> pd.DataFrame` (normalized, concatenated across expirations overlapping [start,end]); `main()` writes the gitignored cache + the committed fixture.

- [ ] **Step 1: Write the failing test** (uses the committed fixture)

```python
# tests/engine_v2/options/test_pull_fixture.py
import os
import pandas as pd
import pytest

FIX = "fixtures/spy_options_small.parquet"

@pytest.mark.skipif(not os.path.exists(FIX), reason="fixture not built yet")
def test_fixture_shape():
    df = pd.read_parquet(FIX)
    assert list(df.columns) == ["date","expiry","dte","strike","right","bid","ask","mid",
                                "close","delta","iv","underlying"]
    assert set(df["right"]) <= {"P","C"}
    assert (df["bid"] > 0).all() and (df["ask"] > 0).all()
    # both puts and calls, a spread of deltas incl. something near 0.30 magnitude
    assert (df["right"] == "P").any() and (df["right"] == "C").any()
    assert ((df["delta"].abs() - 0.30).abs() < 0.15).any()
    assert df["date"].nunique() >= 2  # multiple trading days
```

- [ ] **Step 2: Run to verify it fails/ skips**

Run: `.venv/bin/pytest tests/engine_v2/options/test_pull_fixture.py -v`
Expected: SKIP (fixture absent) — that is the pre-build state.

- [ ] **Step 3: Write the puller**

```python
# scripts/pull_spy_options.py
"""Pull SPY option greeks/eod chains from the local ThetaData terminal, normalize,
write a gitignored cache + a small committed fixture. Terminal must be running
(see docs/thetadata-v3-access.md)."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from src.engine_v2.options.theta_client import ThetaClient
from src.engine_v2.options.chain import normalize_greeks_eod

CACHE_PATH = "data/options/spy_greeks_eod.parquet"
FIXTURE_PATH = "fixtures/spy_options_small.parquet"

def build(client: ThetaClient, symbol: str, start, end, strike_range: int,
          dte_max: int) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frames = []
    for exp in client.list_expirations(symbol):
        win_start = max(start, exp - pd.Timedelta(days=dte_max))
        if exp < start or win_start > end or exp < win_start:
            continue
        raw = client.chain_greeks_eod(symbol, exp, win_start, min(exp, end), strike_range)
        if len(raw):
            frames.append(normalize_greeks_eod(raw))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["date","expiry","strike","right"]).reset_index(drop=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--strike-range", type=int, default=30)
    ap.add_argument("--dte-max", type=int, default=50)
    ap.add_argument("--fixture", action="store_true", help="also (re)write the committed fixture")
    args = ap.parse_args()

    client = ThetaClient()
    if not client.is_up():
        raise SystemExit("ThetaData terminal not running — see docs/thetadata-v3-access.md")
    df = build(client, args.symbol, args.start, args.end, args.strike_range, args.dte_max)
    Path("data/options").mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_PATH)
    print(f"cache {CACHE_PATH}: {df.shape}")
    if args.fixture:
        keep = df["expiry"].drop_duplicates().sort_values().head(2)
        fx = df[df["expiry"].isin(keep)]
        fx.to_parquet(FIXTURE_PATH)
        print(f"fixture {FIXTURE_PATH}: {fx.shape}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: gitignore + build the fixture live**

Append to `.gitignore`:
```
data/options/
```
Confirm the terminal is up (start it per Global Constraints if not), then build a small window and the fixture:
Run: `.venv/bin/python -m scripts.pull_spy_options --start 2024-01-02 --end 2024-02-29 --strike-range 30 --dte-max 50 --fixture`
Expected: prints a cache shape and a fixture shape (a few thousand rows, 2 expiries). If the delta band looks too narrow (no ~0.30 strikes), rerun with a larger `--strike-range`.
Verify: `.venv/bin/python -c "import pandas as pd; d=pd.read_parquet('fixtures/spy_options_small.parquet'); print(d.shape, d['date'].nunique(),'days'); print(d['delta'].describe())"`

- [ ] **Step 5: Confirm the cache is gitignored, run tests**

Run: `git status --porcelain` — `data/options/spy_greeks_eod.parquet` must NOT appear.
Run: `.venv/bin/pytest tests/engine_v2/options/test_pull_fixture.py -v` — now PASSES.
Run: `.venv/bin/pytest tests/ -q` — full suite green.

- [ ] **Step 6: Commit** (fixture yes, cache no)

```bash
git add scripts/pull_spy_options.py .gitignore fixtures/spy_options_small.parquet tests/engine_v2/options/test_pull_fixture.py
git commit -m "feat: SPY options puller + committed greeks/eod fixture"
```

---

### Task 4: Selection / marking primitives

**Files:**
- Create: `src/engine_v2/options/select.py`
- Test: `tests/engine_v2/options/test_select.py`

**Interfaces:**
- Consumes: the committed fixture (Task 3), `Contract`, `Mark`.
- Produces:
  - `select_strike_by_delta(chain, date, right, target_delta, dte_min, dte_max) -> Contract | None`
  - `option_mark(chain, date, contract) -> Mark | None`
  - `intrinsic_value(right, strike, underlying) -> float`
  - `expiry_underlying(chain, contract) -> float | None`

- [ ] **Step 1: Write the failing test** (on the committed fixture)

```python
# tests/engine_v2/options/test_select.py
import os
import pandas as pd
import pytest
from src.engine_v2.options.select import (
    select_strike_by_delta, option_mark, intrinsic_value, expiry_underlying)
from src.engine_v2.options.chain import Contract

FIX = "fixtures/spy_options_small.parquet"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="fixture not built")

def _chain():
    return pd.read_parquet(FIX)

def test_intrinsic_value():
    assert intrinsic_value("P", 100, 90) == 10
    assert intrinsic_value("P", 100, 110) == 0
    assert intrinsic_value("C", 100, 110) == 10
    assert intrinsic_value("C", 100, 90) == 0

def test_select_30_delta_put():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    assert c is not None and c.right == "P"
    # the selected contract's |delta| is the closest to 0.30 among that day's puts in range
    day = ch[(ch["date"] == d) & (ch["right"] == "P")]
    best = (day["delta"].abs() - 0.30).abs().min()
    got = abs(day.set_index("strike").loc[c.strike, "delta"])
    assert abs(abs(got) - 0.30) == pytest.approx(best, abs=1e-9)

def test_select_returns_none_when_empty():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    assert select_strike_by_delta(ch, d, "P", 0.30, dte_min=9998, dte_max=9999) is None

def test_option_mark_and_absent():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    m = option_mark(ch, d, c)
    assert m is not None and m.mid == pytest.approx((m.bid + m.ask) / 2)
    absent = Contract("SPY", c.expiry, 1.0, "P")
    assert option_mark(ch, d, absent) is None

def test_expiry_underlying():
    ch = _chain()
    d = sorted(ch["date"].unique())[0]
    c = select_strike_by_delta(ch, d, "P", 0.30, dte_min=1, dte_max=60)
    u = expiry_underlying(ch, c)
    # underlying on the expiry date if present in the fixture, else None — both valid
    assert (u is None) or (u > 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/engine_v2/options/test_select.py -v`
Expected: FAIL — module missing (or SKIP if fixture somehow absent; it exists from Task 3).

- [ ] **Step 3: Write `select.py`**

```python
# src/engine_v2/options/select.py
"""Pure strike-selection and marking primitives over an OptionsChain frame.
No I/O, no engine state — consumed by the Wheel engine (sub-project 2)."""
from __future__ import annotations
import pandas as pd
from .chain import Contract, Mark

def select_strike_by_delta(chain, date, right, target_delta, dte_min, dte_max):
    m = ((chain["date"] == date) & (chain["right"] == right)
         & (chain["dte"] >= dte_min) & (chain["dte"] <= dte_max))
    cand = chain[m]
    if cand.empty:
        return None
    err = (cand["delta"].abs() - abs(target_delta)).abs()
    # nearest target delta; tie -> further OTM = smaller |delta|
    order = cand.assign(_e=err).sort_values(["_e", "delta"], key=lambda s: s.abs()
                        if s.name == "delta" else s)
    row = order.iloc[0]
    return Contract("SPY", row["expiry"], float(row["strike"]), right)

def option_mark(chain, date, contract):
    m = ((chain["date"] == date) & (chain["expiry"] == contract.expiry)
         & (chain["strike"] == contract.strike) & (chain["right"] == contract.right))
    r = chain[m]
    if r.empty:
        return None
    row = r.iloc[0]
    return Mark(float(row["bid"]), float(row["ask"]), float(row["mid"]))

def intrinsic_value(right, strike, underlying):
    return max(strike - underlying, 0.0) if right == "P" else max(underlying - strike, 0.0)

def expiry_underlying(chain, contract):
    r = chain[(chain["date"] == contract.expiry) & (chain["expiry"] == contract.expiry)]
    if r.empty:
        return None
    return float(r.iloc[0]["underlying"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/engine_v2/options/test_select.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine_v2/options/select.py tests/engine_v2/options/test_select.py
git commit -m "feat: option strike-selection + marking primitives"
```

---

## Self-Review

**Spec coverage:** ThetaClient (Task 1); normalize + Contract/Mark (Task 2); puller + gitignored cache + committed fixture (Task 3); the 4 primitives (Task 4); live-smoke skip-when-down (Task 1 Step 5 manual + puller `is_up` guard). No Wheel cycle / P&L / intraday / dashboard — all correctly absent (later sub-projects).

**Placeholder scan:** none. The one runtime-tuned value (`--strike-range`) has a default (30) and a Task 3 note to widen if the delta band misses ~0.30.

**Type consistency:** `OptionsChain` columns identical across normalize (Task 2), fixture test (Task 3), and primitives (Task 4). `Contract`/`Mark` fields match their construction in `select.py` and the tests. `ThetaClient` method names match the puller's calls. `_open` seam lets Task 1 tests avoid network.

**Risk note:** Task 3 depends on the live terminal to build the committed fixture. Once committed, Tasks 3–4 tests are deterministic and terminal-independent. If the terminal is down at Task 3, start it (Global Constraints) before building.
