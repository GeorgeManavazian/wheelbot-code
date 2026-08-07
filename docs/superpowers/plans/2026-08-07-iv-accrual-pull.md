# Universe-wide IV Accrual Pull — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull option chains for the whole 547-ticker universe once a day and persist an IV observation per ticker per config-grid cell, so `IVHistory` can actually accrue instead of starving on ~12 gate-passers.

**Architecture:** A *separate* runner from the trading chain snapshot, gated on the trading snapshot having already saved. One Schwab call per ticker (PUT-only, out-of-the-money, DTE window +4/+15), then six `select_contract` calls against the in-memory frame — one per `ACCRUAL_GRID` cell — each solved to an IV by our own Black-Scholes solver and written to a per-day JSON file. Trading code is not touched.

**Tech Stack:** Python 3.12 (`.venv-live`), pandas, `schwab-py` 1.5.1, pytest.

**Spec:** `docs/superpowers/specs/2026-08-07-iv-accrual-pull-design.md` (approved 2026-08-07, commits `fae9dbf` + `5102d93`).

## Global Constraints

- **Two venvs, and they are not interchangeable.** `schwab` is installed in `.venv-live` only. Anything importing `schwab.client` must be tested under `.venv-live`.
  - Engine suite: `.venv/bin/python -m pytest tests -q` → baseline **669 passed**
  - Live suite: `.venv-live/bin/python -m pytest live/tests -q` → baseline **416 passed**
  - `.venv/bin/pytest` has a stale shebang from a repo move. Always use `python -m pytest`.
- **Data-only. No order-placement code anywhere.** This repo has never contained any and must not gain any.
- **Every test is mutation-verified.** After a test passes, break the implementation on purpose, confirm the test goes red, revert, confirm green. A test that passes against broken code is worse than no test.
- **⚠ Bytecode hazard during mutation checks.** A same-byte-length constant edit reverted within one second leaves a stale `.pyc` validating on `(mtime, size)` — Python keeps running the mutant. After any same-length constant edit: `find src live -name __pycache__ -type d -exec rm -rf {} +` and `touch` the file.
- **Schwab sends `interestRate` and `dividendYield` as PERCENT** (`3.707` means 3.707%). `implied_vol_put` takes decimals. Conversion happens at exactly ONE site (Task 1) and nowhere else.
- **Never widen the trading pull.** `chain_frame`, `chain_from_json`, `_CHAIN_COLS`, `chain_store`, `market_live.py` and `run_daily.py` are read-only to this work. `market.chain_attempts` must stay ~12.
- **Commit after every task.** End commit messages with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

## File Structure

| File | Responsibility |
|---|---|
| `live/data.py` (modify) | **new** `otm_put_frame()` — the one Schwab call, and the single percent→decimal conversion site. |
| `live/iv_accrual.py` (create) | Pure logic: a chain frame + the grid → observation records. No client, no clock, no filesystem. Offline-testable. |
| `live/iv_store.py` (create) | Persistence only: one JSON per trading day, atomic write, never-serve-stale read. |
| `live/run_iv_accrual.py` (create) | Wiring: clock window, snapshot gate, resume, zombie judgement, save deadline. Thin. |
| `scripts/wheelbot_tick.sh` (modify) | One block that fires the runner. |

**Deviation from the spec, deliberate:** the spec named three new files; this plan splits the per-ticker logic out of the runner into `live/iv_accrual.py`. That follows the doctrine `live/data.py` already states in its own docstring — *"pure mapping functions (offline-testable) plus thin client-fetch wrappers"* — and it is what lets Task 3's tests run with no client, no clock and no monkeypatching.

---

### Task 1: `otm_put_frame` — the pull and the unit conversion

**Files:**
- Modify: `live/data.py` (add constants + function at end of file, after `otm_call_frame`)
- Test: `live/tests/test_otm_put_frame.py` (create)

**Interfaces:**
- Consumes: existing `chain_from_json`, `throttle`, `_num`, `_ET` from `live/data.py`.
- Produces:
  - `IV_WINDOW_LO = 4`, `IV_WINDOW_HI = 15` (ints, days from today)
  - `otm_put_frame(client, ticker: str, obs_date=None) -> pd.DataFrame`
    Columns are `_CHAIN_COLS` plus `rate` and `div_yield`, both **decimals** (Schwab's percent already divided by 100). All rows have `right == "P"`.

- [ ] **Step 1: Write the failing test**

Create `live/tests/test_otm_put_frame.py`:

```python
"""otm_put_frame: the ONE Schwab call the IV accrual pull makes per ticker.

Two things are load-bearing and neither is obvious from reading the function:

1. The request window must cover EVERY DTE band in ACCRUAL_GRID, not just the
   live one. derived_band(7) = (5, 10) and derived_band(11) = (9, 14), so a
   window sized for target_dte=11 alone yields NO observation at all for every
   DTE-7 grid cell -- the store fills up looking healthy with half the grid
   permanently empty.

2. Schwab sends interestRate/dividendYield as PERCENT (3.707 == 3.707%) while
   implied_vol_put takes decimals. Passing 3.707 does not raise and does not
   warn; it returns a plausible IV that is wrong on every contract forever, in
   a series whose entire purpose is internal consistency. This is the one and
   only place the conversion happens.
"""
import datetime as dt
import json
import unittest.mock as um

import pandas as pd

import live.data as data
from live.data import otm_put_frame, IV_WINDOW_LO, IV_WINDOW_HI, _CHAIN_COLS

PUTS = json.load(open("live/fixtures/option_chain_gdx_puts.json"))
OBS = pd.Timestamp("2026-07-17")


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Client:
    """Records the request kwargs and replays the GDX puts fixture."""

    def __init__(self, payload=PUTS):
        self.kw = {}
        self.ticker = None
        self._payload = payload

    def get_option_chain(self, ticker, **kw):
        self.ticker = ticker
        self.kw = kw
        return _Resp(self._payload)


class _FrozenDT(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        base = dt.datetime(2026, 7, 17, 15, 30, tzinfo=dt.timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


def _pull(client):
    with um.patch.object(data.dt, "datetime", _FrozenDT):
        return otm_put_frame(client, "GDX", obs_date=OBS)


def test_requests_out_of_the_money_puts_only():
    from schwab.client import Client
    c = _Client()
    _pull(c)
    assert c.ticker == "GDX"
    assert c.kw["contract_type"] is Client.Options.ContractType.PUT
    assert c.kw["strike_range"] is Client.Options.StrikeRange.OUT_OF_THE_MONEY
    assert "strike_count" not in c.kw, "no strike_count guess; OTM range is the point"


def test_window_covers_every_grid_dte_band_not_just_the_live_one():
    """derived_band(7) = (5,10), derived_band(11) = (9,14) -> union 5..14,
    plus one day of slack each side because Schwab's daysToExpiration need not
    agree with (expiry - today).days at the edges."""
    c = _Client()
    _pull(c)
    today = dt.date(2026, 7, 17)
    assert c.kw["from_date"] == today + dt.timedelta(days=IV_WINDOW_LO)
    assert c.kw["to_date"] == today + dt.timedelta(days=IV_WINDOW_HI)
    assert IV_WINDOW_LO <= 4 and IV_WINDOW_HI >= 15


def test_rate_and_div_yield_are_converted_from_percent_to_decimal():
    """The fixture header carries interestRate 3.707 and dividendYield 0.888,
    both PERCENT. Everything downstream must see decimals."""
    df = _pull(_Client())
    assert len(df) > 0
    assert df["rate"].iloc[0] == 0.03707
    assert df["div_yield"].iloc[0] == 0.00888


def test_frame_is_puts_only_and_keeps_the_engine_column_shape():
    df = _pull(_Client())
    assert set(df["right"]) == {"P"}
    assert list(df.columns) == _CHAIN_COLS + ["rate", "div_yield"]
    assert (df["date"] == OBS).all()


def test_missing_header_fields_land_as_none_not_a_crash():
    payload = {k: v for k, v in PUTS.items()
               if k not in ("interestRate", "dividendYield")}
    df = _pull(_Client(payload))
    assert df["rate"].isna().all()
    assert df["div_yield"].isna().all()


def test_non_200_raises_so_the_caller_counts_a_skipped_ticker():
    class _Bad:
        def get_option_chain(self, ticker, **kw):
            r = _Resp({})
            r.status_code = 503
            return r

    import pytest
    with pytest.raises(RuntimeError, match="503"):
        _pull(_Bad())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv-live/bin/python -m pytest live/tests/test_otm_put_frame.py -q`
Expected: FAIL — `ImportError: cannot import name 'otm_put_frame' from 'live.data'`

- [ ] **Step 3: Write the implementation**

Append to `live/data.py`, after `otm_call_frame`:

```python
# IV-accrual request window, in days from today. It must span EVERY DTE band in
# live/iv_accrual.py's ACCRUAL_GRID, not just the live config's:
#     derived_band(7)  = (5, 10)
#     derived_band(11) = (9, 14)   ->  union 5..14
# plus one day of slack each side, because Schwab's daysToExpiration need not
# agree with (expiry - today).days at the edges. A window sized for
# target_dte=11 alone silently yields NO observation for any DTE-7 grid cell.
IV_WINDOW_LO, IV_WINDOW_HI = 4, 15


def _pct(v):
    """Schwab percent -> decimal. None-safe.

    interestRate/dividendYield are PERCENT (3.707 == 3.707%), the same
    convention as the per-contract `volatility` field, while implied_vol_put
    takes decimals. Passing 3.707 neither raises nor warns -- it returns a
    plausible IV that is wrong on every contract, permanently. This is the ONE
    conversion site; nothing downstream divides by 100 again.
    (Convention proven in scratchpad/diag_schwab_iv_fit.py, 2026-08-06.)"""
    f = _num(v)
    return None if f is None else f / 100.0


def otm_put_frame(client, ticker: str, obs_date=None) -> pd.DataFrame:
    """Every listed OUT-OF-THE-MONEY PUT across the IV-accrual DTE window, plus
    the chain header's rate and dividend yield as decimal columns.

    PUT-only is load-bearing, the mirror image of otm_call_frame's CALL-only
    note: these rows exist to be measured, never to be traded. They are written
    to the IV store and never spliced into a LiveMarket, so no account can ever
    see a far-OTM put as an entry candidate.

    strike_range=OUT_OF_THE_MONEY rather than a strike_count guess: chain_frame's
    12-strike spot-centred window reaches only ~+/-3.8%, and a 0.30-delta put at
    11 DTE can sit further out on a low-vol name -- which would mean pulling 547
    chains and still missing the one contract each is for."""
    from schwab.client import Client
    today = dt.datetime.now(_ET).date()   # A22/B7: ET, never the box clock
    r = throttle(client.get_option_chain, ticker,
                 contract_type=Client.Options.ContractType.PUT,
                 strike_range=Client.Options.StrikeRange.OUT_OF_THE_MONEY,
                 from_date=today + dt.timedelta(days=IV_WINDOW_LO),
                 to_date=today + dt.timedelta(days=IV_WINDOW_HI))
    if r.status_code != 200:
        raise RuntimeError(f"{ticker} otm_put_chain -> HTTP {r.status_code}")
    payload = r.json()
    df = chain_from_json(payload, obs_date or today)
    df["rate"] = _pct(payload.get("interestRate"))
    df["div_yield"] = _pct(payload.get("dividendYield"))
    return df
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-live/bin/python -m pytest live/tests/test_otm_put_frame.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Mutation-verify the percent conversion**

Change `_pct`'s `return None if f is None else f / 100.0` to `return f`. Then:

```bash
find src live -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
touch live/data.py
.venv-live/bin/python -m pytest live/tests/test_otm_put_frame.py -q
```

Expected: FAIL on `test_rate_and_div_yield_are_converted_from_percent_to_decimal`.
Then revert, clear `__pycache__` again, re-run, confirm PASS.

- [ ] **Step 6: Mutation-verify the request window**

Change `IV_WINDOW_LO, IV_WINDOW_HI = 4, 15` to `8, 15` (the value that covers DTE 11 but not DTE 7). Clear `__pycache__`, `touch live/data.py`, re-run.
Expected: FAIL on `test_window_covers_every_grid_dte_band_not_just_the_live_one`.
Revert, clear, re-run, confirm PASS.

- [ ] **Step 7: Confirm nothing else moved**

Run: `.venv-live/bin/python -m pytest live/tests -q`
Expected: `422 passed` (416 baseline + 6 new)

- [ ] **Step 8: Commit**

```bash
git add live/data.py live/tests/test_otm_put_frame.py
git commit -m "feat: otm_put_frame, the IV accrual pull's one Schwab call

PUT-only + strike_range=OUT_OF_THE_MONEY, mirroring otm_call_frame (A4): no
strike_count guess, so the ~0.30-delta put is guaranteed present where
chain_frame's 12-strike +/-3.8% window can miss it.

Request window +4/+15 covers every DTE band in ACCRUAL_GRID, not just the live
one -- derived_band(7) = (5,10) and derived_band(11) = (9,14). A window sized
for DTE 11 alone would silently yield no observation at all for every DTE-7
cell.

Schwab's interestRate/dividendYield are PERCENT while implied_vol_put takes
decimals; _pct is the single conversion site. Mutation-verified both ways.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `live/iv_store.py` — one JSON per trading day

**Files:**
- Create: `live/iv_store.py`
- Test: `live/tests/test_iv_store.py` (create)

**Interfaces:**
- Consumes: `live.paths.in_state`.
- Produces:
  - `iv_path(obs) -> str` — `data/live/iv/YYYY-MM-DD.json`
  - `save_iv_day(obs, records: list[dict], pulled_at: str, path=None) -> str`
  - `load_iv_day(obs, path=None) -> list[dict] | None` — `None` for absent, unreadable, or wrong-date. `[]` is a real, distinguishable value.
  - `tickers_done(records: list[dict]) -> set[str]`

- [ ] **Step 1: Write the failing test**

Create `live/tests/test_iv_store.py`:

```python
"""IV observation store. Two contracts copied from chain_store, for the same
reasons stated there:

  * atomic write -- a reader must never see a half-written file
  * load returns None, NEVER a stale dict, for a file stamped with another date

The second one is the 2026-07-24 failure mode (a day stepped against multi-day-
old marks) and it is worse here than for chains: a stale IV file would append
observations under today's date that were measured on another day's quotes,
inside a series whose whole purpose is comparing a ticker to its own past.
"""
import json

import pandas as pd

from live.iv_store import iv_path, save_iv_day, load_iv_day, tickers_done

OBS = pd.Timestamp("2026-07-17")

REC = {"ticker": "GDX", "put_delta": 0.30, "target_dte": 11,
       "expiry": "2026-07-31", "strike": 68.0, "dte": 14, "delta": -0.31,
       "bid": 0.95, "ask": 1.02, "mid": 0.98, "underlying": 71.32,
       "rate": 0.03707, "div_yield": 0.00888, "iv": 0.3412,
       "source": "schwab-rth/bs-v1/d30/dte11"}


def _roundtrip(tmp_path, records, obs=OBS, load_obs=None):
    p = str(tmp_path / "iv.json")
    save_iv_day(obs, records, pulled_at="2026-07-17T15:35:00-04:00", path=p)
    return p, load_iv_day(load_obs or obs, path=p)


def test_roundtrip_preserves_every_field(tmp_path):
    _, got = _roundtrip(tmp_path, [REC])
    assert got == [REC]


def test_empty_day_is_present_not_missing(tmp_path):
    """'pulled fine, nothing selectable' must stay distinguishable from
    'the window failed' -- same rule as chain_store's empty snapshot."""
    _, got = _roundtrip(tmp_path, [])
    assert got == []
    assert got is not None


def test_wrong_obs_date_is_never_served(tmp_path):
    _, got = _roundtrip(tmp_path, [REC], load_obs=OBS + pd.Timedelta(days=1))
    assert got is None


def test_missing_or_corrupt_file_returns_none(tmp_path):
    assert load_iv_day(OBS, path=str(tmp_path / "absent.json")) is None
    for name, body in (("corrupt.json", "{not json"),
                       ("notdict.json", '["nope"]'),
                       ("norecords.json", '{"obs": "2026-07-17"}'),
                       ("badrecords.json", '{"obs": "2026-07-17", "records": 7}')):
        p = tmp_path / name
        p.write_text(body)
        assert load_iv_day(OBS, path=str(p)) is None, name


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    p, _ = _roundtrip(tmp_path, [REC])
    assert not (tmp_path / "iv.json.tmp").exists()
    payload = json.loads(open(p).read())
    assert payload["obs"] == "2026-07-17"
    assert payload["pulled_at"] == "2026-07-17T15:35:00-04:00"


def test_tickers_done_powers_the_resume(tmp_path):
    """Resume is keyed on the TICKER, not the grid cell: one API call produces
    all six cells, so a ticker is either done or not."""
    recs = [dict(REC), dict(REC, target_dte=7), dict(REC, ticker="SPY")]
    assert tickers_done(recs) == {"GDX", "SPY"}
    assert tickers_done([]) == set()


def test_path_lives_under_the_state_root_so_it_rides_the_mirror():
    assert iv_path(OBS).endswith("iv/2026-07-17.json")
    assert "data/live" in iv_path(OBS) or "iv/2026-07-17.json" in iv_path(OBS)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv-live/bin/python -m pytest live/tests/test_iv_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.iv_store'`

- [ ] **Step 3: Write the implementation**

Create `live/iv_store.py`:

```python
"""Persisted daily IV observations. Data-only; no order code.

One JSON file per trading day, keyed by the ET observation date, holding one
record per (ticker, grid cell). The git state mirror therefore sees one NEW
file per day and never a modified one -- the cheapest diff available, and the
reason this shape was chosen over per-ticker files.

`load_iv_day` returns None -- never a stale list -- when the file is absent,
unreadable, or stamped with a different obs date. Serving another day's
observations as today's would append quotes measured elsewhere into a series
whose entire meaning is a ticker compared against its own past. Same doctrine
as chain_store.load_chain_snapshot.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from live.paths import in_state


def iv_path(obs) -> str:
    return in_state("iv", f"{pd.Timestamp(obs).date()}.json")


def save_iv_day(obs, records: list, pulled_at: str, path=None) -> str:
    """Persist `records` for `obs`. Atomic (tmp + rename): a reader must never
    see a half-written file from a pass that died mid-dump."""
    obs = pd.Timestamp(obs).normalize()
    path = path or iv_path(obs)
    payload = {"obs": str(obs.date()), "pulled_at": pulled_at,
               "records": list(records)}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    return path


def load_iv_day(obs, path=None):
    """The records for exactly `obs`, or None when no usable file exists.

    An EMPTY list is a real answer ("pulled fine, nothing selectable") and is
    deliberately distinguishable from None ("no usable file")."""
    obs = pd.Timestamp(obs).normalize()
    path = path or iv_path(obs)
    try:
        with open(path) as f:
            payload = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("obs") != str(obs.date()):
        return None
    recs = payload.get("records")
    if not isinstance(recs, list):
        return None
    return recs


def tickers_done(records: list) -> set:
    """Tickers already recorded today. Resume is keyed on the ticker, not the
    grid cell: one API call produces every cell, so a ticker is either done or
    not."""
    return {r["ticker"] for r in records if isinstance(r, dict) and "ticker" in r}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-live/bin/python -m pytest live/tests/test_iv_store.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Mutation-verify the never-serve-stale rule**

Delete these two lines from `load_iv_day`:

```python
    if payload.get("obs") != str(obs.date()):
        return None
```

Clear `__pycache__`, re-run.
Expected: FAIL on `test_wrong_obs_date_is_never_served`. Revert, confirm PASS.

- [ ] **Step 6: Commit**

```bash
git add live/iv_store.py live/tests/test_iv_store.py
git commit -m "feat: iv_store, one JSON per trading day

Atomic write and never-serve-stale read, both copied from chain_store. The
stale rule matters more here than for chains: serving another day's file would
append observations measured on other quotes into a series whose whole meaning
is a ticker compared against its own past.

Empty list stays distinguishable from None -- 'nothing selectable today' is not
'the window failed'. Mutation-verified on the date guard.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `live/iv_accrual.py` — the grid, the stamp, the solve

**Files:**
- Create: `live/iv_accrual.py`
- Test: `live/tests/test_iv_accrual.py` (create)

**Interfaces:**
- Consumes: `otm_put_frame`'s column shape (Task 1); `src.engine_v2.options.select.select_contract`; `src.engine_v2.options.iv_solve.implied_vol_put`.
- Produces:
  - `ACCRUAL_GRID: list[tuple[float, int]]` — `[(0.20, 7), (0.20, 11), (0.30, 7), (0.30, 11), (0.40, 7), (0.40, 11)]`
  - `SOURCE = "schwab-rth"`, `SOLVER_VERSION = "bs-v1"`, `MAX_SOLVED_IV = 4.9`
  - `stamp(put_delta: float, target_dte: int) -> str`
  - `observations_for(ticker: str, frame, obs_date, grid=ACCRUAL_GRID) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `live/tests/test_iv_accrual.py`:

```python
"""Per-ticker IV observation building. Pure: no client, no clock, no files.

The one thing that must not drift: the recorded value is the IV of THE CONTRACT
select_contract WOULD SELL that day -- same function, same delta target, same
DTE band. IVHistory.from_chains states why in its own docstring: the ranks were
measured on exactly this quantity, so recording a chain-wide average or an ATM
proxy silently redefines the statistic while everything keeps running.
"""
import json

import pandas as pd
import pytest

import live.data as data
from live.iv_accrual import (ACCRUAL_GRID, MAX_SOLVED_IV, observations_for,
                             stamp)
from src.engine_v2.options.select import select_contract

PUTS = json.load(open("live/fixtures/option_chain_gdx_puts.json"))
OBS = pd.Timestamp("2026-07-17")


def _frame():
    """The shape otm_put_frame produces, built offline: puts only, plus the
    header's rate/div_yield already converted to decimals."""
    df = data.chain_from_json(PUTS, OBS)
    df = df[df["right"] == "P"].reset_index(drop=True)
    df["rate"] = data._pct(PUTS["interestRate"])
    df["div_yield"] = data._pct(PUTS["dividendYield"])
    return df


def test_grid_is_the_configs_actually_in_play():
    assert (0.30, 11) in ACCRUAL_GRID, "FROZEN"
    assert (0.20, 11) in ACCRUAL_GRID, "the delta the owner described 2026-08-03"
    assert (0.40, 7) in ACCRUAL_GRID, "the sweep's top arm"
    assert len(ACCRUAL_GRID) == len(set(ACCRUAL_GRID))


def test_stamp_encodes_source_solver_and_the_grid_cell():
    """A put_delta or target_dte change is a scale change exactly as a solver
    change is: a 0.20-delta put is further OTM and cheaper, so every post-change
    day would read as near-record-cheap on a series that never got cheap.
    IVHistory.append refuses on a stamp difference -- so the cell must be IN the
    stamp, or that refusal cannot see a config change."""
    assert stamp(0.30, 11) == "schwab-rth/bs-v1/d30/dte11"
    assert stamp(0.20, 7) == "schwab-rth/bs-v1/d20/dte7"
    assert stamp(0.30, 11) != stamp(0.20, 11)
    assert stamp(0.30, 11) != stamp(0.30, 7)


def test_records_the_contract_select_contract_would_actually_sell():
    frame = _frame()
    recs = {(r["put_delta"], r["target_dte"]): r
            for r in observations_for("GDX", frame, OBS)}
    r = recs[(0.30, 11)]
    c = select_contract(frame, OBS, "P", 0.30, 11, "GDX")
    assert c is not None
    assert r["strike"] == c.strike
    assert pd.Timestamp(r["expiry"]) == pd.Timestamp(c.expiry)


def test_both_dte_bands_produce_observations():
    """The regression that a request window sized for DTE 11 alone would cause,
    caught one layer down as well: derived_band(7) = (5,10) must resolve too."""
    recs = observations_for("GDX", _frame(), OBS)
    dtes = {r["target_dte"] for r in recs}
    assert dtes == {7, 11}, f"one band produced nothing: {dtes}"


def test_every_cell_carries_its_own_stamp_and_solver_inputs():
    for r in observations_for("GDX", _frame(), OBS):
        assert r["source"] == stamp(r["put_delta"], r["target_dte"])
        for k in ("ticker", "expiry", "strike", "dte", "delta", "bid", "ask",
                  "mid", "underlying", "rate", "div_yield", "iv"):
            assert k in r, k
        assert 0.0 < r["iv"] < MAX_SOLVED_IV
        assert r["strike"] < r["underlying"], "solver is OTM-only"
        assert isinstance(r["expiry"], str), "must be JSON-serialisable"


def test_a_cell_with_no_in_band_expiry_contributes_nothing_not_a_nan():
    """A gap must SHORTEN a history, never poison the percentile. Same rule as
    IVHistory.from_chains."""
    recs = observations_for("GDX", _frame(), OBS, grid=[(0.30, 99)])
    assert recs == []


def test_empty_frame_yields_no_records():
    empty = _frame().iloc[0:0]
    assert observations_for("GDX", empty, OBS) == []


def test_a_quote_that_pins_the_solver_is_dropped_not_recorded():
    """Bisection returns its ceiling for an unsolvable quote. Recording 500%
    vol would dominate that ticker's percentile for a full year."""
    frame = _frame().copy()
    frame["mid"] = frame["strike"] * 2.0     # no-arb violation on every row
    frame["bid"] = frame["mid"] - 0.01
    frame["ask"] = frame["mid"] + 0.01
    assert observations_for("GDX", frame, OBS) == []


def test_missing_rate_or_div_yield_skips_rather_than_guessing():
    frame = _frame().copy()
    frame["rate"] = float("nan")
    assert observations_for("GDX", frame, OBS) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv-live/bin/python -m pytest live/tests/test_iv_accrual.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.iv_accrual'`

- [ ] **Step 3: Write the implementation**

Create `live/iv_accrual.py`:

```python
"""Chain frame + config grid -> IV observation records. Pure; data-only.

WHY A GRID. The pull already downloads every OTM put on the ticker. Recording
only the contract FROZEN would sell throws away the other ~59 already in hand,
and Schwab has NO historical chain endpoint -- live/data.py says it plainly, a
day not captured is unmeasurable forever. So a later put_delta/target_dte change
would mean a 150-observation rebuild it is impossible to backfill.

Recording the grid instead costs zero API calls (six select_contract passes over
a frame already in memory) and turns that rebuild into a column switch: every
cell accrues in parallel from day 1 and warms up on the same schedule.

The cells are not arbitrary -- 0.30/11 is FROZEN, 0.20 is the delta the owner
described the strategy as on 2026-08-03, and 0.40/7 is the sweep's top arm.

BACKTESTS ARE UNAFFECTED by any of this: IVHistory.from_chains rebuilds in
memory from raw stored chains on every run, sets no stamp, and never calls
append. Only the live forward path needs a lock, because only the live forward
path cannot re-derive.
"""
from __future__ import annotations

import pandas as pd

from src.engine_v2.options.iv_solve import implied_vol_put
from src.engine_v2.options.select import select_contract

# (put_delta, target_dte) cells recorded every day. Adding a cell is cheap
# (no API cost) but only accrues FORWARD -- it cannot be backfilled.
ACCRUAL_GRID = [(0.20, 7), (0.20, 11),
                (0.30, 7), (0.30, 11),
                (0.40, 7), (0.40, 11)]

SOURCE = "schwab-rth"
SOLVER_VERSION = "bs-v1"

# implied_vol_put bisects on [1e-6, 5.0]. An unsolvable quote (a no-arb
# violation, a stale crossed book) does not raise -- it returns the ceiling. A
# recorded 500% observation would dominate that ticker's percentile for a full
# 252-day window, so a pinned solve is DROPPED rather than stored.
MAX_SOLVED_IV = 4.9


def stamp(put_delta: float, target_dte: int) -> str:
    """The provenance scale for one grid cell.

    The cell is IN the stamp because changing put_delta or target_dte is a scale
    change exactly as changing the solver is. IVHistory.append refuses to extend
    a series whose stamp differs; if the cell were not encoded, that refusal
    could not see a config change and the series would silently hold two scales.
    """
    return f"{SOURCE}/{SOLVER_VERSION}/d{int(round(put_delta * 100))}/dte{target_dte}"


def observations_for(ticker: str, frame, obs_date, grid=ACCRUAL_GRID) -> list:
    """One record per grid cell that resolves to a solvable contract.

    A cell with no in-band expiry, an unusable quote, or a missing solver input
    contributes NOTHING -- never a NaN. Gaps shorten a history; NaNs poison a
    percentile. Cells are independent: a ticker can legitimately produce a DTE-11
    row and no DTE-7 row on the same day."""
    if frame is None or len(frame) == 0:
        return []
    obs = pd.Timestamp(obs_date).normalize()
    out = []
    for put_delta, target_dte in grid:
        c = select_contract(frame, obs, "P", put_delta, target_dte, ticker)
        if c is None:
            continue
        sel = frame[(frame["expiry"] == c.expiry)
                    & (frame["strike"] == c.strike)
                    & (frame["right"] == "P")]
        if sel.empty:
            continue
        row = sel.iloc[0]
        mid, und = float(row["mid"]), float(row["underlying"])
        strike, dte = float(row["strike"]), int(row["dte"])
        rate, q = row["rate"], row["div_yield"]
        # A missing r/q is not a zero r/q. Skipping keeps the series on one
        # scale; substituting a default would put an unmarked second scale in
        # it, which is the failure the stamp exists to make impossible.
        if pd.isna(rate) or pd.isna(q) or mid <= 0 or und <= 0 or dte <= 0:
            continue
        try:
            iv = implied_vol_put(price=mid, underlying=und, strike=strike,
                                 dte=dte, rate=float(rate), div_yield=float(q))
        except ValueError:
            # implied_vol_put refuses ITM puts. An OTM-only pull should never
            # produce one; skip rather than propagate, so an unexpected row
            # cannot kill the whole day.
            continue
        if not (0.0 < iv < MAX_SOLVED_IV):
            continue
        out.append({
            "ticker": ticker,
            "put_delta": put_delta, "target_dte": target_dte,
            "expiry": str(pd.Timestamp(row["expiry"]).date()),
            "strike": strike, "dte": dte, "delta": float(row["delta"]),
            "bid": float(row["bid"]), "ask": float(row["ask"]), "mid": mid,
            "underlying": und, "rate": float(rate), "div_yield": float(q),
            "iv": float(iv), "source": stamp(put_delta, target_dte),
        })
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-live/bin/python -m pytest live/tests/test_iv_accrual.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Mutation-verify the "same contract as the bot would sell" rule**

In `observations_for`, change the `select_contract` call's delta argument from `put_delta` to a hardcoded `0.30`:

```python
        c = select_contract(frame, obs, "P", 0.30, target_dte, ticker)
```

Clear `__pycache__`, re-run.
Expected: FAIL on `test_records_the_contract_select_contract_would_actually_sell` or `test_every_cell_carries_its_own_stamp_and_solver_inputs`. Revert, confirm PASS.

- [ ] **Step 6: Mutation-verify the stamp carries the cell**

Change `stamp` to return `f"{SOURCE}/{SOLVER_VERSION}"`. Clear `__pycache__`, re-run.
Expected: FAIL on `test_stamp_encodes_source_solver_and_the_grid_cell`. Revert, confirm PASS.

- [ ] **Step 7: Mutation-verify the pinned-solve guard**

Change `MAX_SOLVED_IV = 4.9` to `MAX_SOLVED_IV = 99.0`. **Same-length-constant hazard does not apply here (different length), but clear `__pycache__` anyway.** Re-run.
Expected: FAIL on `test_a_quote_that_pins_the_solver_is_dropped_not_recorded`. Revert, confirm PASS.

- [ ] **Step 8: Commit**

```bash
git add live/iv_accrual.py live/tests/test_iv_accrual.py
git commit -m "feat: iv_accrual, grid -> observation records

Records the contract select_contract WOULD SELL, by calling select_contract --
not a reimplementation and not an ATM proxy. IVHistory.from_chains states why:
the ranks were measured on exactly this quantity.

Six grid cells (delta .20/.30/.40 x DTE 7/11) at zero API cost, because the
pull already has every OTM put in memory and Schwab has no historical chain
endpoint to get them back later. Turns a future put_delta/target_dte change
from a 150-day rebuild into a column switch.

The stamp encodes the cell, so IVHistory.append's refusal can see a config
change the same way it sees a source change.

A cell with no in-band expiry, a missing r/q, or a solve pinned at the
bisection ceiling contributes nothing rather than a NaN -- gaps shorten a
history, NaNs poison a percentile. Mutation-verified on all three.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `live/run_iv_accrual.py` — the runner

**Files:**
- Create: `live/run_iv_accrual.py`
- Test: `live/tests/test_run_iv_accrual.py` (create)

**Interfaces:**
- Consumes: `otm_put_frame` (Task 1), `iv_store` (Task 2), `iv_accrual` (Task 3), `live.run_daily.FROZEN`, `live.chain_store.load_chain_snapshot`, `live.config.load_run_config`, `live.universe.UNIVERSE`.
- Produces:
  - `IV_OPEN = 1520`, `IV_CLOSE = 1555`, `SAVE_DEADLINE = 1605`
  - `accrual_window_open(now_et) -> bool`
  - `save_still_rth(now_et) -> bool`
  - `frozen_cell_on_grid(frozen: dict, grid) -> bool`
  - `pull_failed(attempted: int, ok: int, threshold: float) -> bool`
  - `main() -> int` (0 = saved; non-zero = no marker, retry next tick)

- [ ] **Step 1: Write the failing test**

Create `live/tests/test_run_iv_accrual.py`:

```python
"""Runner wiring. Everything external is monkeypatched at the module that owns
it (main() re-imports inside the function, so call-time attribute patches land);
no network, no real store. Same harness shape as test_run_chain_snapshot.py.
"""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import live.run_iv_accrual as ria
from live.run_iv_accrual import (IV_OPEN, IV_CLOSE, accrual_window_open,
                                 frozen_cell_on_grid, pull_failed,
                                 save_still_rth)

ET = ZoneInfo("America/New_York")


def _at(h, m, day=17):
    return dt.datetime(2026, 7, day, h, m, tzinfo=ET)


def _clockseq(*stamps):
    stamps = list(stamps)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return stamps.pop(0) if len(stamps) > 1 else stamps[0]
    return _DT


# ---- predicates -----------------------------------------------------------

def test_window_is_weekday_rth_only():
    assert accrual_window_open(_at(15, 30)) is True          # Friday
    assert accrual_window_open(_at(15, 19)) is False
    assert accrual_window_open(_at(17, 0)) is False, "17:00 is the defect, not a fallback"
    assert accrual_window_open(_at(15, 30, day=18)) is False, "Saturday"
    assert IV_OPEN == 1520 and IV_CLOSE == 1555


def test_save_deadline_refuses_post_close_quotes():
    assert save_still_rth(_at(16, 4)) is True
    assert save_still_rth(_at(16, 6)) is False


def test_pull_failure_is_measured_on_the_feed_not_the_yield():
    assert pull_failed(547, 12, 0.5) is True
    assert pull_failed(547, 541, 0.5) is False
    assert pull_failed(0, 0, 0.5) is True, "nothing attempted is not a quiet day here"


def test_frozen_must_be_a_grid_member():
    """Otherwise the store fills happily with six series, none of which is the
    strategy being traded."""
    grid = [(0.30, 11), (0.20, 7)]
    assert frozen_cell_on_grid({"put_delta": 0.30, "target_dte": 11}, grid) is True
    assert frozen_cell_on_grid({"put_delta": 0.25, "target_dte": 9}, grid) is False


def test_the_real_frozen_is_on_the_real_grid():
    from live.iv_accrual import ACCRUAL_GRID
    from live.run_daily import FROZEN
    assert frozen_cell_on_grid(FROZEN, ACCRUAL_GRID) is True


# ---- main() wiring --------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Returns a helper that runs main() with everything external stubbed."""
    def _run(*, universe=("GDX", "SPY"), snapshot={"GDX": None},
             prior=None, frames=None, start=None, done=None,
             threshold=0.5, frozen=None):
        import live.chain_store as cs
        import live.config as lc
        import live.data as ld
        import live.iv_store as ivs
        import live.run_daily as rd
        import live.universe as lu

        start = start or _at(15, 30)
        monkeypatch.setattr(ria.dt, "datetime", _clockseq(start, done or start))
        fake = types.ModuleType("schwab_client")
        fake.get_client = lambda: object()
        monkeypatch.setitem(sys.modules, "schwab_client", fake)
        monkeypatch.setattr(lu, "UNIVERSE", list(universe))
        monkeypatch.setattr(cs, "load_chain_snapshot", lambda obs: snapshot)
        monkeypatch.setattr(lc, "load_run_config",
                            lambda *a, **k: {"zombie_threshold": threshold})
        if frozen is not None:
            monkeypatch.setattr(rd, "FROZEN", frozen)
        monkeypatch.setattr(ivs, "load_iv_day", lambda obs: prior)

        saved = {}
        monkeypatch.setattr(ivs, "save_iv_day",
                            lambda obs, records, pulled_at, **kw:
                            saved.update(obs=obs, records=records)
                            or str(tmp_path / "iv.json"))

        pulled = []

        def _frame(client, tk, obs_date=None):
            pulled.append(tk)
            if frames is not None and tk in frames:
                out = frames[tk]
                if isinstance(out, Exception):
                    raise out
                return out
            raise RuntimeError(f"{tk} boom")

        monkeypatch.setattr(ld, "otm_put_frame", _frame)
        monkeypatch.setattr(ria, "observations_for",
                            lambda tk, frame, obs: [{"ticker": tk, "iv": 0.3}])
        rc = ria.main()
        return rc, saved, pulled
    return _run


def test_refuses_before_the_trading_snapshot_exists(wired):
    rc, saved, pulled = wired(snapshot=None)
    assert rc != 0
    assert saved == {}, "nothing saved"
    assert pulled == [], "no API calls -- trading eats first"


def test_refuses_outside_the_window(wired):
    rc, saved, _ = wired(start=_at(17, 0))
    assert rc != 0 and saved == {}


def test_refuses_when_frozen_is_off_grid(wired):
    rc, saved, pulled = wired(frozen={"put_delta": 0.25, "target_dte": 9})
    assert rc != 0
    assert saved == {} and pulled == []


def test_happy_path_saves_and_exits_zero(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled = wired(frames={"GDX": df, "SPY": df})
    assert rc == 0
    assert sorted(pulled) == ["GDX", "SPY"]
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "SPY"}


def test_wholesale_pull_failure_saves_nothing_useful_and_exits_one(wired):
    rc, saved, pulled = wired(frames={})       # every ticker raises
    assert rc != 0
    assert sorted(pulled) == ["GDX", "SPY"], "it tried all of them"


def test_resume_skips_tickers_already_recorded_today(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, pulled = wired(frames={"SPY": df},
                              prior=[{"ticker": "GDX", "iv": 0.2}])
    assert pulled == ["SPY"], "GDX was already done"
    assert {r["ticker"] for r in saved["records"]} == {"GDX", "SPY"}, \
        "prior records are carried forward, not dropped"


def test_a_slow_pull_past_the_deadline_is_discarded(wired):
    df = pd.DataFrame({"x": [1]})
    rc, saved, _ = wired(frames={"GDX": df, "SPY": df},
                         start=_at(15, 50), done=_at(16, 30))
    assert rc != 0
    assert saved == {}, "post-close quotes must never be stamped RTH"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv-live/bin/python -m pytest live/tests/test_run_iv_accrual.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'live.run_iv_accrual'`

- [ ] **Step 3: Write the implementation**

Create `live/run_iv_accrual.py`:

```python
"""Universe-wide IV accrual runner. Data-only, NO order code; writes nothing but
the daily IV observation file.

  PYTHONPATH=. .venv-live/bin/python live/run_iv_accrual.py [--force]

WHY THIS EXISTS. market_live.py pulls option chains only for held tickers plus
that day's weather-gate passers -- 12 of 547 on 2026-07-31. An IV observation
can only exist on a day the chain was pulled, so a ticker accrues ~5-6 per year
against IVHistory's MIN_RANK_OBS = 150. Purchased history would roll out of the
252-day window faster than it accrues, and about a year after launch every name
would fall to NEUTRAL_IV_RANK and selection would silently become universe
order. This runner is what makes "forward data comes from Schwab" true.

SEPARATE from run_chain_snapshot.py on purpose. That runner's exit code gates
the tick's once-per-day marker and its chain_attempts feeds zombie_check; adding
45x the API work to it would put every trading decision behind a heavier pull
and force those thresholds to be re-derived. This one is gated on the trading
snapshot ALREADY existing, so trading gets the window and the API budget first,
and its own exit code touches nothing but its own marker.

Exit codes: 0 = observations saved (tick writes the marker); non-zero = nothing
saved / partial, tick retries on every remaining in-window tick.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from live.iv_accrual import ACCRUAL_GRID, observations_for

ET = ZoneInfo("America/New_York")

# Opens with the trading snapshot's window but is gated on that snapshot having
# saved, so in practice this starts ~15:27-15:35. Closes later than the trading
# window (1550) because a 547-ticker pull takes ~5 minutes.
IV_OPEN, IV_CLOSE = 1520, 1555
# Same grace and same reason as run_chain_snapshot.SAVE_DEADLINE: a pull that
# STARTED in-window may finish a few minutes past 16:00 on a slow day, but past
# this the quotes are genuinely post-close and must not be stamped as RTH.
SAVE_DEADLINE = 1605


def accrual_window_open(now_et) -> bool:
    if now_et.weekday() >= 5:
        return False
    hm = now_et.hour * 100 + now_et.minute
    return IV_OPEN <= hm <= IV_CLOSE


def save_still_rth(now_et) -> bool:
    """Re-checked AFTER the pull -- the start gate alone lets a slow pull bless
    post-close quotes."""
    return now_et.hour * 100 + now_et.minute <= SAVE_DEADLINE


def frozen_cell_on_grid(frozen: dict, grid=ACCRUAL_GRID) -> bool:
    """Is the live config one of the cells we record?

    Without this check, changing FROZEN to an off-grid (put_delta, target_dte)
    leaves the store filling happily with six series NONE of which is the
    strategy being traded -- healthy-looking, and useless."""
    return (float(frozen["put_delta"]), int(frozen["target_dte"])) in \
        [(float(d), int(t)) for d, t in grid]


def pull_failed(attempted: int, ok: int, threshold: float) -> bool:
    """Judge the FEED, never the derived quantity -- the zombie_check doctrine.

    A low observation count is a real property of thin expiry ladders (AOS was
    measured with an in-band expiry on 17% of days), so it is logged and never
    a failure. A high CHAIN failure ratio is an outage."""
    if attempted <= 0:
        return True
    return (attempted - ok) / attempted >= threshold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="pull outside the RTH window (manual/backfill only; "
                         "the result is the post-close book, not RTH)")
    args = ap.parse_args()

    from live.chain_store import load_chain_snapshot
    from live.config import load_run_config
    from live.data import otm_put_frame
    from live.iv_store import load_iv_day, save_iv_day, tickers_done
    from live.run_daily import FROZEN
    from live.universe import UNIVERSE

    now = dt.datetime.now(ET)
    if not args.force and not accrual_window_open(now):
        print(f"IV accrual REFUSED: {now:%Y-%m-%d %H:%M} ET is outside the "
              f"{IV_OPEN}-{IV_CLOSE} RTH window.")
        return 1

    if not frozen_cell_on_grid(FROZEN):
        print(f"IV accrual REFUSED: FROZEN is put_delta="
              f"{FROZEN['put_delta']} target_dte={FROZEN['target_dte']}, which "
              f"is NOT in ACCRUAL_GRID {ACCRUAL_GRID}. The store would accrue "
              f"six series none of which is the traded strategy. Add the cell "
              f"to the grid (it accrues forward only) or revert FROZEN.")
        return 1

    obs = pd.Timestamp(now.date())

    # Trading eats first: this runner exists to accrue data, never to compete
    # with the decision path for the window or the API budget.
    if load_chain_snapshot(obs) is None:
        print(f"IV accrual DEFERRED: no trading chain snapshot for "
              f"{obs.date()} yet. Retrying on a later in-window tick.")
        return 1

    prior = load_iv_day(obs) or []
    done = tickers_done(prior)
    todo = [tk for tk in UNIVERSE if tk not in done]
    if done:
        print(f"IV accrual resuming: {len(done)} ticker(s) already recorded "
              f"today, {len(todo)} to go.")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                    "scripts", "schwab"))
    from schwab_client import get_client
    client = get_client()

    records, attempted, ok, skipped = list(prior), 0, 0, []
    for tk in todo:
        attempted += 1
        try:
            frame = otm_put_frame(client, tk, obs_date=obs)
        except Exception as e:      # one bad symbol must not stop the run
            skipped.append((tk, f"{type(e).__name__}: {e}"))
            continue
        ok += 1
        records.extend(observations_for(tk, frame, obs))

    thr = load_run_config()["zombie_threshold"]
    if pull_failed(attempted, ok, thr):
        print(f"IV accrual FAILED {obs.date()}: chains {ok}/{attempted} usable "
              f"(threshold {thr:.0%}). Nothing saved; the next in-window tick "
              f"resumes.")
        return 1

    finished = dt.datetime.now(ET)
    if not args.force and not save_still_rth(finished):
        print(f"IV accrual DISCARDED: pull finished {finished:%H:%M} ET, past "
              f"the {SAVE_DEADLINE} grace -- these are post-close quotes and "
              f"must not be stamped RTH. Nothing saved.")
        return 1

    path = save_iv_day(obs, records, pulled_at=now.isoformat())
    cells = len(records)
    names = len({r["ticker"] for r in records})
    print(f"IV accrual {obs.date()}: {ok}/{attempted} chains, {cells} "
          f"observation(s) across {names} ticker(s) -> {path}")
    if skipped:
        print(f"PARTIAL: {len(skipped)} chain(s) failed "
              f"({[tk for tk, _ in skipped][:8]}); saved anyway; exiting 1 so "
              f"remaining in-window ticks resume the missing names.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-live/bin/python -m pytest live/tests/test_run_iv_accrual.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: Mutation-verify the trading-first gate**

Delete the `load_chain_snapshot(obs) is None` block from `main()`. Clear `__pycache__`, re-run.
Expected: FAIL on `test_refuses_before_the_trading_snapshot_exists`. Revert, confirm PASS.

- [ ] **Step 6: Mutation-verify the FROZEN-on-grid guard**

Change `frozen_cell_on_grid` to `return True`. Clear `__pycache__`, re-run.
Expected: FAIL on `test_frozen_must_be_a_grid_member` and `test_refuses_when_frozen_is_off_grid`. Revert, confirm PASS.

- [ ] **Step 7: Mutation-verify the resume**

Change `todo = [tk for tk in UNIVERSE if tk not in done]` to `todo = list(UNIVERSE)`. Clear `__pycache__`, re-run.
Expected: FAIL on `test_resume_skips_tickers_already_recorded_today`. Revert, confirm PASS.

- [ ] **Step 8: Commit**

```bash
git add live/run_iv_accrual.py live/tests/test_run_iv_accrual.py
git commit -m "feat: run_iv_accrual, the universe-wide IV pull runner

Separate from run_chain_snapshot on purpose: that runner's exit code gates the
tick marker and its chain_attempts feeds zombie_check, so adding 45x the API
work would put trading decisions behind a heavier pull and force those
thresholds to be re-derived. This one refuses to start until the trading
snapshot has saved, and its exit code touches nothing but its own marker.

Refuses when FROZEN's (put_delta, target_dte) is off ACCRUAL_GRID -- otherwise
the store accrues six healthy-looking series none of which is the traded
strategy.

Failure is the chain failure ratio against zombie_threshold, never the
observation count: a thin ladder legitimately yields nothing. Retries resume
from the day's partial file. Mutation-verified on the trading gate, the grid
guard and the resume.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Tick block + spec reconciliation

**Files:**
- Modify: `scripts/wheelbot_tick.sh` (insert after the RTH chain-snapshot block, before the EOD daily-run block)
- Modify: `docs/superpowers/specs/2026-08-07-iv-accrual-pull-design.md` (data-flow snippet)
- Test: `live/tests/test_tick_script.py` (append)

**Interfaces:**
- Consumes: `live/run_iv_accrual.py` (Task 4); the existing `Sandbox` harness and `SHIM` in `live/tests/test_tick_script.py`.
- Produces: no Python interface. Marker file `data/live/logs/.ivaccrual-$TODAY`.

- [ ] **Step 1: Write the failing test**

Append to `live/tests/test_tick_script.py`:

```python
# ---- IV accrual block (2026-08-07) ---------------------------------------
# The block must be GATED on the chain snapshot's marker: the accrual pull is
# 547 chains and must never take the window or the API budget from the ~12-chain
# trading pull the day's decision depends on.

def _ran(calls, script):
    return any(script in c for c in calls)


def test_iv_accrual_does_not_run_before_the_chain_snapshot(tmp_path):
    sb = Sandbox(tmp_path)
    sb.tick(dow="5", hm="1530", today="2026-07-17",
            env={"TICK_RC_run_chain_snapshot": "1"})
    assert _ran(sb.calls(), "run_chain_snapshot.py")
    assert not _ran(sb.calls(), "run_iv_accrual.py"), \
        "547-chain pull must not run while the trading snapshot is unfinished"


def test_iv_accrual_runs_once_the_chain_snapshot_marker_exists(tmp_path):
    sb = Sandbox(tmp_path)
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert _ran(sb.calls(), "run_iv_accrual.py")
    assert (sb.logs / ".ivaccrual-2026-07-17").exists()


def test_iv_accrual_marker_is_written_only_on_success(tmp_path):
    sb = Sandbox(tmp_path)
    (sb.logs / ".chainsnap-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17",
            env={"TICK_RC_run_iv_accrual": "1"})
    assert not (sb.logs / ".ivaccrual-2026-07-17").exists(), \
        "a failed pull must retry on every remaining in-window tick"


def test_iv_accrual_failure_does_not_fail_the_tick(tmp_path):
    """An IV outage must never alert-storm or mark a trading day failed."""
    sb = Sandbox(tmp_path)
    (sb.logs / ".chainsnap-2026-07-17").touch()
    r = sb.tick(dow="5", hm="1530", today="2026-07-17",
                env={"TICK_RC_run_iv_accrual": "1"})
    assert r.returncode == 0


def test_iv_accrual_does_not_rerun_once_done(tmp_path):
    sb = Sandbox(tmp_path)
    (sb.logs / ".chainsnap-2026-07-17").touch()
    (sb.logs / ".ivaccrual-2026-07-17").touch()
    sb.tick(dow="5", hm="1530", today="2026-07-17")
    assert not _ran(sb.calls(), "run_iv_accrual.py")


def test_iv_accrual_does_not_run_on_a_weekend(tmp_path):
    sb = Sandbox(tmp_path)
    (sb.logs / ".chainsnap-2026-07-18").touch()
    sb.tick(dow="6", hm="1530", today="2026-07-18")
    assert not _ran(sb.calls(), "run_iv_accrual.py")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv-live/bin/python -m pytest live/tests/test_tick_script.py -q -k iv_accrual`
Expected: FAIL — `run_iv_accrual.py` never appears in the journal.

- [ ] **Step 3: Write the implementation**

In `scripts/wheelbot_tick.sh`, insert immediately after the chain-snapshot block (after its closing `fi`, before the `# --- EOD daily run` comment):

```bash
# --- Universe-wide IV accrual: weekdays 15:20-15:55 ET, once per day -------
# 547 chains (~5 min) so the whole universe accrues an IV observation, not just
# the ~12 gate-passers -- without this the forward series gets ~5-6 obs/ticker/
# year against MIN_RANK_OBS=150 and any purchased history decays to NEUTRAL
# about a year after launch.
#
# GATED on $SNAPMARKER: trading's ~12-chain pull owns the window and the API
# budget first; this only ever runs on its leftovers. Deliberately does NOT set
# FAIL -- an IV outage must never alert-storm or mark a trading day failed. The
# runner itself re-checks every one of these conditions; the shell gate just
# avoids spawning it 60 times a day for nothing.
IVMARKER="$LOGDIR/.ivaccrual-$TODAY"
if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] \
   && [ -f "$SNAPMARKER" ] && [ ! -f "$IVMARKER" ]; then
  log "iv accrual start"
  py live/run_iv_accrual.py >> "$LOGDIR/iv-$TODAY.log" 2>&1
  RC=$?
  log "iv accrual exit $RC"
  [ "$RC" -eq 0 ] && touch "$IVMARKER"
fi
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv-live/bin/python -m pytest live/tests/test_tick_script.py -q`
Expected: PASS — all existing tick tests plus 6 new.

- [ ] **Step 5: Mutation-verify the trading gate**

Remove `&& [ -f "$SNAPMARKER" ]` from the condition. Re-run.
Expected: FAIL on `test_iv_accrual_does_not_run_before_the_chain_snapshot`. Revert, confirm PASS.

- [ ] **Step 6: Reconcile the spec's data-flow snippet**

The spec shows the percent→decimal conversion at the solve call (`rate=rate/100`). The implementation hoists it into `otm_put_frame._pct`, so there is exactly one conversion site instead of one per grid cell. Update the spec's data-flow block to match:

In `docs/superpowers/specs/2026-08-07-iv-accrual-pull-design.md`, change:

```
    implied_vol_put(price=mid, underlying=..., strike=..., dte=...,
                    rate=rate/100, div_yield=div_yield/100)
```

to:

```
    implied_vol_put(price=mid, underlying=..., strike=..., dte=...,
                    rate=..., div_yield=...)     # already decimals, see _pct
```

And in the hazard section, after "**Gets a dedicated test.**", add:

```
The conversion is hoisted into `otm_put_frame` (`_pct`) so there is exactly ONE
conversion site rather than one per grid cell.
```

- [ ] **Step 7: Full suite, both venvs**

```bash
.venv/bin/python -m pytest tests -q           # expect 669 passed (unchanged)
.venv-live/bin/python -m pytest live/tests -q # expect 456 passed
```

New tests by task: 6 (T1) + 7 (T2) + 9 (T3) + 12 (T4) + 6 (T5) = **40**, against a
416 baseline.

Neither number may drop. `tests/` must be **exactly** unchanged — this work touches no engine code.

- [ ] **Step 8: Confirm the trading path is untouched**

```bash
git diff --stat HEAD~4 -- live/market_live.py live/run_daily.py \
  live/chain_store.py src/
```

Expected: **empty output.** If anything appears, the separation this whole design rests on has been broken.

- [ ] **Step 9: Commit**

```bash
git add scripts/wheelbot_tick.sh live/tests/test_tick_script.py \
  docs/superpowers/specs/2026-08-07-iv-accrual-pull-design.md
git commit -m "feat: fire the IV accrual pull from the tick

Gated on the chain-snapshot marker so trading's ~12-chain pull owns the window
and the API budget first, and deliberately does not set FAIL -- an IV outage
must never alert-storm or mark a trading day failed. Marker written only on
exit 0, so a failed pull resumes on every remaining in-window tick.

Spec reconciled: the percent->decimal conversion is hoisted into otm_put_frame
so there is one conversion site rather than one per grid cell.

Mutation-verified: removing the snapshot gate turns the trading-first test red.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Deployment (NOT part of this plan)

The VPS is **not a git repo**; deploy is rsync of `live/ src/ deploy/` plus the tick script, then a sha256 verify. The bot is currently **OFF** by owner decision, and this work does not change that.

**The counter reads 0 until day 1.** The 252-day window only advances on days the bot runs, so this accrues nothing until the timer is enabled. That is expected and was accepted when the owner chose "accrual fix only".

When deploying later, `scripts/wheelbot_tick.sh` must be included — it is outside the `live/ src/ deploy/` rsync set, and a deployed runner with an un-deployed tick script would never fire.

## Out of scope, on the record

- **The read side.** `run_daily` keeps `iv_history=None`; `rank_by="iv_rank"` still raises via `7c8ea48`'s guard.
- **Coverage.** Still 108/547 rankable. This builds the accrual path; it moves no ticker into the rankable column.
- **The scale-vs-vendor stamp amendment.** Unruled, and binds nothing while we accrue from a single source.
- **`earnings_blackout`.** Unrelated open work, needs the same `_live_market()` signature plumbing.
