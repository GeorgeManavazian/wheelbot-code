# ETF Research Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a transparent pandas backtesting engine where strategies are plugins, with a frozen playground/exam data split and a batch runner that tests many strategy×parameter combinations honestly.

**Architecture:** Daily event loop over wide close-price DataFrames. Strategy sees a truncated window ending at decision day T and returns target weights (or `None` = no rebalance); engine fills at T+1 open with slippage, whole shares, no leverage. Data is frozen into checksummed playground (2010–2020) and exam (2021+) parquet files; exam loads are gated and logged. Batch runner sweeps parameter grids and prints a leaderboard with multiple-testing honesty stats.

**Tech Stack:** Python 3.11+, pandas, numpy, scipy, yfinance, pyarrow, pytest.

**Spec:** `docs/superpowers/specs/2026-07-05-research-engine-design.md` (same repo — read it first).

## Global Constraints

- Playground = 2010-01-01 → 2020-12-31; exam = 2021-01-01 → present. Exam data loads ONLY via `load_exam(confirm=True)`, and every exam load appends to `data/manifests/exam_runs.log`.
- Fills at next-bar OPEN only. Same-bar fills must be impossible.
- Slippage default 5 bps each way; commission $0; whole shares; weights sum ≤ 1.0; no shorting, no leverage.
- Strategy contract: `target_weights(self, window: pd.DataFrame) -> dict[str, float] | None` where `window` = wide adjusted closes (index=dates, columns=tickers) up to and including decision day. `None` = hold, don't trade today.
- Universe (20 ETFs): SPY, QQQ, IWM, EFA, EEM, TLT, IEF, SHY, LQD, HYG, GLD, SLV, DBC, VNQ, XLE, XLF, XLK, XLV, XLU, XLI.
- NaN prices inside an engine window → error, never silently interpolated. QC hard-fails, no silent patching.
- Owner is a near-beginner coder: code stays simple and flat, no clever abstractions. YAGNI hard.
- Data parquet files are gitignored; manifests (checksums), QC reports, and exam_runs.log are committed.

---

### Task 1: Project scaffold + universe module

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `src/__init__.py`, `src/engine/__init__.py`, `src/strategies/__init__.py`, `src/batch/__init__.py`, `src/engine/universe.py`
- Modify: `.gitignore`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `src.engine.universe.TICKERS: list[str]` — the 20-ETF universe every later task imports.

- [ ] **Step 1: Write requirements and configs**

`requirements.txt`:
```
pandas>=2.0
numpy>=1.24
scipy>=1.10
yfinance>=0.2.40
pyarrow>=14.0
pytest>=8.0
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = .
```

Append to `.gitignore`:
```
.venv/
__pycache__/
data/raw/
data/playground/*.parquet
data/exam/*.parquet
results/
```

- [ ] **Step 2: Create venv and install**

Run: `cd ~/Documents/Trading\ code/etf-bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
Expected: installs succeed. All later `pytest`/`python` commands use `.venv/bin/`.

- [ ] **Step 3: Write the failing test**

`tests/test_universe.py`:
```python
from src.engine.universe import TICKERS

def test_universe_has_20_unique_tickers():
    assert len(TICKERS) == 20
    assert len(set(TICKERS)) == 20
    assert "SPY" in TICKERS and "TLT" in TICKERS
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 5: Write minimal implementation**

Create empty `src/__init__.py`, `src/engine/__init__.py`, `src/strategies/__init__.py`, `src/batch/__init__.py`.

`src/engine/universe.py`:
```python
"""The v1 ETF universe. Fixed list = no survivorship-bias machinery needed."""

TICKERS = [
    "SPY", "QQQ", "IWM", "EFA", "EEM",          # equity broad
    "TLT", "IEF", "SHY", "LQD", "HYG",          # bonds/credit
    "GLD", "SLV", "DBC", "VNQ",                 # commodities/REIT
    "XLE", "XLF", "XLK", "XLV", "XLU", "XLI",   # sectors
]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_universe.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini .gitignore src/ tests/
git commit -m "feat: project scaffold and 20-ETF universe"
```

---

### Task 2: Data pull script (yfinance bootstrap)

**Files:**
- Create: `scripts/pull_data.py`

**Interfaces:**
- Consumes: `src.engine.universe.TICKERS`
- Produces: `data/raw/{TICKER}.parquet` — one file per ticker, columns `date, open, high, low, close, volume` (adjusted, lowercase), plus `data/raw/SPY_unadjusted.parquet` for the QC adjustment check.

Network script — no unit test; verified by the QC task. Keep it thin.

- [ ] **Step 1: Write the script**

`scripts/pull_data.py`:
```python
"""Pull daily OHLCV for the ETF universe from yfinance into data/raw/.

auto_adjust=True gives split+dividend adjusted prices (total-return series).
We also save unadjusted SPY so QC can prove adjustment actually happened.
"""
from pathlib import Path

import yfinance as yf

from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
START = "2010-01-01"


def clean(df):
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index.name = "date"
    return df.dropna(how="all")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for ticker in TICKERS:
        df = yf.download(ticker, start=START, auto_adjust=True, progress=False)
        if df.empty:
            raise SystemExit(f"FATAL: no data for {ticker}")
        if isinstance(df.columns[0], tuple):  # yfinance MultiIndex quirk
            df.columns = df.columns.get_level_values(0)
        clean(df).to_parquet(RAW_DIR / f"{ticker}.parquet")
        print(f"{ticker}: {len(df)} rows, {df.index[0].date()} -> {df.index[-1].date()}")

    spy_raw = yf.download("SPY", start=START, auto_adjust=False, progress=False)
    if isinstance(spy_raw.columns[0], tuple):
        spy_raw.columns = spy_raw.columns.get_level_values(0)
    clean(spy_raw).to_parquet(RAW_DIR / "SPY_unadjusted.parquet")
    print("SPY_unadjusted saved (QC adjustment check)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python scripts/pull_data.py`
Expected: 20 lines like `SPY: ~4000 rows, 2010-01-04 -> <today>`, then the unadjusted line. SPY/TLT/GLD start 2010; DBC/HYG also pre-2010; all 20 exist by 2010 (verify in output — any ticker starting after 2010-01-04 gets flagged by QC next task).

- [ ] **Step 3: Commit**

```bash
git add scripts/pull_data.py
git commit -m "feat: yfinance data pull script"
```

---

### Task 3: QC module + report script

**Files:**
- Create: `src/engine/qc.py`, `scripts/qc_report.py`
- Test: `tests/test_qc.py`

**Interfaces:**
- Consumes: `data/raw/*.parquet` from Task 2.
- Produces: `qc.check_prices(df) -> list[str]`, `qc.check_calendar(df, ref) -> list[str]`, `qc.inception_report(frames: dict) -> pd.DataFrame`, `qc.check_spy_adjustment(adj, unadj) -> list[str]`. Each check returns a list of violation strings (empty = pass). Committed report at `data/manifests/qc_report.md`.

- [ ] **Step 1: Write the failing tests**

`tests/test_qc.py`:
```python
import pandas as pd
import pytest

from src.engine import qc


def make_df(closes, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes,
         "close": closes, "volume": [1000] * len(closes)},
        index=idx,
    )


def test_check_prices_flags_zero_and_negative():
    df = make_df([100.0, 0.0, -5.0, 100.0])
    violations = qc.check_prices(df)
    assert len(violations) == 2


def test_check_prices_flags_nan():
    df = make_df([100.0, float("nan"), 100.0])
    assert len(qc.check_prices(df)) == 1


def test_check_prices_passes_clean_data():
    assert qc.check_prices(make_df([100.0, 101.0, 102.0])) == []


def test_check_calendar_flags_missing_dates():
    ref = make_df([100.0] * 5)          # 5 business days
    df = make_df([100.0] * 5).drop(ref.index[2])  # missing day 3
    violations = qc.check_calendar(df, ref)
    assert len(violations) == 1
    assert str(ref.index[2].date()) in violations[0]


def test_check_calendar_ignores_pre_inception():
    ref = make_df([100.0] * 10)
    late = make_df([100.0] * 5, start=str(ref.index[5].date()))
    assert qc.check_calendar(late, ref) == []


def test_inception_report():
    frames = {"A": make_df([1.0] * 3), "B": make_df([1.0] * 3, start="2021-06-01")}
    rep = qc.inception_report(frames)
    assert rep.loc["B", "first_date"] > rep.loc["A", "first_date"]


def test_spy_adjustment_detects_identical_series():
    adj = make_df([100.0, 101.0, 102.0])
    violations = qc.check_spy_adjustment(adj, adj.copy())
    assert len(violations) == 1  # identical = adjustment never applied
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_qc.py -v`
Expected: FAIL with `ImportError` / `AttributeError`.

- [ ] **Step 3: Write the implementation**

`src/engine/qc.py`:
```python
"""Data QC checks. Every check returns a list of violation strings; empty = pass.

House rule: hard-fail on violations, never silently patch data.
"""
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]


def check_prices(df: pd.DataFrame) -> list[str]:
    """Zero, negative, or NaN prices."""
    out = []
    for date, row in df[PRICE_COLS].iterrows():
        if row.isna().any():
            out.append(f"{date.date()}: NaN price {row.to_dict()}")
        elif (row <= 0).any():
            out.append(f"{date.date()}: non-positive price {row.to_dict()}")
    return out


def check_calendar(df: pd.DataFrame, ref: pd.DataFrame) -> list[str]:
    """Missing bars vs a reference calendar (SPY trades every NYSE session).

    Only checks dates after the instrument's own first bar — an ETF that
    launched later is not 'missing' earlier bars (inception_report covers that).
    """
    expected = ref.index[ref.index >= df.index[0]]
    missing = expected.difference(df.index)
    return [f"missing bar {d.date()}" for d in missing]


def inception_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """First/last date per ticker so nothing silently enters mid-backtest."""
    rows = {
        t: {"first_date": df.index[0], "last_date": df.index[-1], "rows": len(df)}
        for t, df in frames.items()
    }
    return pd.DataFrame.from_dict(rows, orient="index").sort_values("first_date")


def check_spy_adjustment(adj: pd.DataFrame, unadj: pd.DataFrame) -> list[str]:
    """Adjusted and unadjusted SPY closes must diverge going back in time
    (dividends compound). If they're identical, auto_adjust silently failed."""
    joined = adj[["close"]].join(unadj[["close"]], rsuffix="_unadj").dropna()
    early = joined.iloc[: len(joined) // 2]
    if (early["close"] - early["close_unadj"]).abs().max() < 1e-6:
        return ["adjusted == unadjusted SPY closes: dividend adjustment missing"]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_qc.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Write the report script**

`scripts/qc_report.py`:
```python
"""Run all QC checks on data/raw/ and write data/manifests/qc_report.md.

Exits non-zero on any violation — freezing (next step) is blocked until clean.
"""
import sys
from pathlib import Path

import pandas as pd

from src.engine import qc
from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
OUT = Path("data/manifests/qc_report.md")


def main():
    frames = {t: pd.read_parquet(RAW_DIR / f"{t}.parquet") for t in TICKERS}
    spy_unadj = pd.read_parquet(RAW_DIR / "SPY_unadjusted.parquet")

    violations = []
    for t, df in frames.items():
        violations += [f"{t}: {v}" for v in qc.check_prices(df)]
        violations += [f"{t}: {v}" for v in qc.check_calendar(df, frames["SPY"])]
    violations += [f"SPY: {v}" for v in qc.check_spy_adjustment(frames["SPY"], spy_unadj)]

    report = qc.inception_report(frames)
    late = report[report["first_date"] > pd.Timestamp("2010-01-05")]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# QC Report", "", "## Inception dates", "", report.to_markdown(), ""]
    if len(late):
        lines += ["## WARNING: tickers entering after 2010-01-05", "",
                  late.to_markdown(), ""]
    lines += ["## Violations", ""]
    lines += [f"- {v}" for v in violations] if violations else ["None."]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}; {len(violations)} violations, {len(late)} late-inception tickers")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run it on real data**

Run: `.venv/bin/python scripts/qc_report.py`
Expected: `wrote data/manifests/qc_report.md; 0 violations, N late-inception tickers`, exit 0. If violations: STOP, show the owner, decide (fix source or drop ticker) — never patch silently. If a ticker's inception is after 2010-01-05, flag to owner: keep (strategies must handle NaN-before-inception by exclusion) or swap for an older equivalent.

- [ ] **Step 7: Commit**

```bash
git add src/engine/qc.py scripts/qc_report.py tests/test_qc.py data/manifests/qc_report.md
git commit -m "feat: data QC checks and report"
```

---

### Task 4: Freeze split + gated loaders

**Files:**
- Create: `scripts/freeze_split.py`, `src/engine/data.py`
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: `data/raw/*.parquet`.
- Produces:
  - `data/playground/playground.parquet`, `data/exam/exam.parquet` — long format, columns `date, ticker, open, high, low, close, volume`.
  - `data/manifests/manifest.json` — `{"playground": {"sha256": ..., "rows": ..., "start": ..., "end": ...}, "exam": {...}}` (committed).
  - `data.load_playground(data_dir="data") -> pd.DataFrame` (long format, checksum-verified).
  - `data.load_exam(confirm=False, data_dir="data") -> pd.DataFrame` — raises `RuntimeError` unless `confirm=True`; appends ISO-timestamp line to `data/manifests/exam_runs.log`.
  - `data.to_wide_closes(long_df) -> pd.DataFrame` — wide adjusted closes (index=date, columns=ticker); this is the strategy `window` format.
  - `data.to_wide(long_df, field) -> pd.DataFrame` — same pivot for any field (engine uses `"open"`).

- [ ] **Step 1: Write the failing tests**

`tests/test_data.py`:
```python
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.engine import data


def make_long(dates, tickers):
    rows = []
    for d in dates:
        for t in tickers:
            rows.append({"date": d, "ticker": t, "open": 10.0, "high": 10.0,
                         "low": 10.0, "close": 10.0, "volume": 100})
    return pd.DataFrame(rows)


@pytest.fixture
def frozen(tmp_path):
    dates = pd.bdate_range("2020-01-01", periods=4)
    df = make_long(dates, ["A", "B"])
    for split in ("playground", "exam"):
        (tmp_path / split).mkdir()
        df.to_parquet(tmp_path / split / f"{split}.parquet")
    manifest = {}
    for split in ("playground", "exam"):
        p = tmp_path / split / f"{split}.parquet"
        manifest[split] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                           "rows": len(df)}
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_load_playground_verifies_checksum(frozen):
    df = data.load_playground(data_dir=frozen)
    assert len(df) == 8


def test_load_playground_rejects_tampered_file(frozen):
    p = frozen / "playground" / "playground.parquet"
    p.write_bytes(p.read_bytes() + b"tamper")
    with pytest.raises(RuntimeError, match="checksum"):
        data.load_playground(data_dir=frozen)


def test_load_exam_refuses_without_confirm(frozen):
    with pytest.raises(RuntimeError, match="exam"):
        data.load_exam(data_dir=frozen)


def test_load_exam_logs_when_confirmed(frozen):
    data.load_exam(confirm=True, data_dir=frozen)
    log = (frozen / "manifests" / "exam_runs.log").read_text()
    assert len(log.strip().splitlines()) == 1


def test_to_wide_closes(frozen):
    wide = data.to_wide_closes(data.load_playground(data_dir=frozen))
    assert list(wide.columns) == ["A", "B"]
    assert len(wide) == 4
    assert wide.index.is_monotonic_increasing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: FAIL with `ImportError` / `AttributeError`.

- [ ] **Step 3: Write the implementation**

`src/engine/data.py`:
```python
"""Frozen-data loaders. Playground loads freely; exam is gated and logged.

The exam gate is the multiple-testing defense: mass screening physically
cannot touch 2021+ data by accident.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def _load_verified(split: str, data_dir) -> pd.DataFrame:
    data_dir = Path(data_dir)
    path = data_dir / split / f"{split}.parquet"
    manifest = json.loads((data_dir / "manifests" / "manifest.json").read_text())
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != manifest[split]["sha256"]:
        raise RuntimeError(
            f"{split} checksum mismatch — file changed since freeze. "
            f"Expected {manifest[split]['sha256'][:12]}…, got {actual[:12]}…"
        )
    return pd.read_parquet(path)


def load_playground(data_dir="data") -> pd.DataFrame:
    return _load_verified("playground", data_dir)


def load_exam(confirm: bool = False, data_dir="data") -> pd.DataFrame:
    if not confirm:
        raise RuntimeError(
            "exam data is SEALED. Pass confirm=True only for a pre-registered "
            "exam run (pass bars written to a vault decision note first). "
            "This load will be permanently logged."
        )
    log = Path(data_dir) / "manifests" / "exam_runs.log"
    with open(log, "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} exam data loaded\n")
    return _load_verified("exam", data_dir)


def to_wide(long_df: pd.DataFrame, field: str) -> pd.DataFrame:
    wide = long_df.pivot(index="date", columns="ticker", values=field).sort_index()
    wide.columns.name = None
    return wide


def to_wide_closes(long_df: pd.DataFrame) -> pd.DataFrame:
    return to_wide(long_df, "close")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Write the freeze script**

`scripts/freeze_split.py`:
```python
"""Freeze data/raw/ into playground (2010–2020) and exam (2021+) parquet
files with SHA256 manifest. Run ONCE after QC passes; re-running overwrites,
which invalidates prior results — only do that on an owner decision.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.engine.universe import TICKERS

RAW_DIR = Path("data/raw")
SPLIT_END = "2020-12-31"  # playground <= this < exam


def main():
    frames = []
    for t in TICKERS:
        df = pd.read_parquet(RAW_DIR / f"{t}.parquet").reset_index()
        df["ticker"] = t
        frames.append(df)
    long_df = pd.concat(frames, ignore_index=True)
    long_df["date"] = pd.to_datetime(long_df["date"])

    cut = pd.Timestamp(SPLIT_END)
    splits = {"playground": long_df[long_df["date"] <= cut],
              "exam": long_df[long_df["date"] > cut]}

    manifest = {}
    for name, df in splits.items():
        out = Path(f"data/{name}/{name}.parquet")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        manifest[name] = {
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "rows": len(df),
            "start": str(df["date"].min().date()),
            "end": str(df["date"].max().date()),
            "tickers": sorted(df["ticker"].unique().tolist()),
        }
        print(f"{name}: {len(df)} rows {manifest[name]['start']} -> {manifest[name]['end']}")

    Path("data/manifests").mkdir(parents=True, exist_ok=True)
    Path("data/manifests/manifest.json").write_text(json.dumps(manifest, indent=2))
    print("manifest written")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run freeze on real data + verify loaders**

Run: `.venv/bin/python scripts/freeze_split.py`
Expected: playground ~2010-01-04 → 2020-12-31, exam 2021-01-04 → present, manifest written.

Run: `.venv/bin/python -c "from src.engine.data import load_playground, load_exam; print(load_playground().shape); load_exam()"`
Expected: playground shape prints; then `RuntimeError: exam data is SEALED…`.

- [ ] **Step 7: Commit**

```bash
git add src/engine/data.py scripts/freeze_split.py tests/test_data.py data/manifests/manifest.json
git commit -m "feat: freeze playground/exam split with checksums and gated exam loader"
```

---

### Task 5: Rebalance/fill accounting

**Files:**
- Create: `src/engine/fills.py`
- Test: `tests/test_fills.py`

**Interfaces:**
- Produces:
  ```python
  execute_rebalance(
      positions: dict[str, int],      # ticker -> shares held (mutated copy returned)
      cash: float,
      targets: dict[str, float],      # ticker -> target weight
      open_prices: pd.Series,         # ticker -> today's open
      slippage_bps: float,
  ) -> tuple[dict[str, int], float, list[dict]]   # new positions, new cash, trades
  ```
  Trade dict: `{"ticker", "side" ("buy"/"sell"), "shares", "price"}`. Task 6 consumes this.

- [ ] **Step 1: Write the failing tests**

`tests/test_fills.py`:
```python
import pandas as pd
import pytest

from src.engine.fills import execute_rebalance


def test_buy_from_cash_no_slippage():
    pos, cash, trades = execute_rebalance(
        {}, 10_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=0)
    assert pos == {"A": 50}
    assert cash == pytest.approx(5_000.0)
    assert trades == [{"ticker": "A", "side": "buy", "shares": 50, "price": 100.0}]


def test_buy_pays_slippage():
    # 100 bps: buy price 101 -> floor(5000/101) = 49 shares
    pos, cash, trades = execute_rebalance(
        {}, 10_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=100)
    assert pos == {"A": 49}
    assert cash == pytest.approx(10_000.0 - 49 * 101.0)


def test_sell_receives_slippage_penalty():
    # sell all of A at 100 with 100 bps -> proceeds 99/share
    pos, cash, trades = execute_rebalance(
        {"A": 10}, 0.0, {"A": 0.0}, pd.Series({"A": 100.0}), slippage_bps=100)
    assert pos == {}
    assert cash == pytest.approx(990.0)
    assert trades[0]["side"] == "sell"


def test_sells_execute_before_buys():
    # all cash in A; switch to B. Must sell A first to afford B.
    pos, cash, trades = execute_rebalance(
        {"A": 100}, 0.0, {"A": 0.0, "B": 1.0},
        pd.Series({"A": 100.0, "B": 100.0}), slippage_bps=0)
    assert pos == {"B": 100}
    assert [t["side"] for t in trades] == ["sell", "buy"]


def test_insufficient_cash_reduces_buy(capsys):
    # full switch A -> B with 100 bps slippage: sell 100 A @ 99 -> cash 9900,
    # target B = floor(10000/101) = 99 shares costing 9999 -> capped to 98
    pos, cash, trades = execute_rebalance(
        {"A": 100}, 0.0, {"B": 1.0},
        pd.Series({"A": 100.0, "B": 100.0}), slippage_bps=100)
    assert pos == {"B": 98}
    assert cash == pytest.approx(9_900.0 - 98 * 101.0)
    assert "WARNING" in capsys.readouterr().out


def test_weights_over_one_rejected():
    with pytest.raises(ValueError, match="sum"):
        execute_rebalance({}, 1000.0, {"A": 0.6, "B": 0.6},
                          pd.Series({"A": 10.0, "B": 10.0}), slippage_bps=0)


def test_negative_weight_rejected():
    with pytest.raises(ValueError, match="short"):
        execute_rebalance({}, 1000.0, {"A": -0.1}, pd.Series({"A": 10.0}),
                          slippage_bps=0)


def test_unknown_ticker_rejected():
    with pytest.raises(ValueError, match="price"):
        execute_rebalance({}, 1000.0, {"ZZZ": 0.5}, pd.Series({"A": 10.0}),
                          slippage_bps=0)


def test_no_trade_when_already_at_target():
    pos, cash, trades = execute_rebalance(
        {"A": 50}, 5_000.0, {"A": 0.5}, pd.Series({"A": 100.0}), slippage_bps=0)
    assert trades == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fills.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/engine/fills.py`:
```python
"""Rebalance accounting: targets -> whole-share trades at today's open.

Sells first (frees cash), then buys in sorted-ticker order (deterministic).
Buys are capped at available cash — never borrow (no leverage, house rule).
"""
import math

import pandas as pd


def execute_rebalance(positions, cash, targets, open_prices, slippage_bps):
    if sum(targets.values()) > 1.0 + 1e-9:
        raise ValueError(f"target weights sum to {sum(targets.values()):.4f} > 1.0")
    if any(w < 0 for w in targets.values()):
        raise ValueError("negative weight = shorting, not allowed in v1")
    for t in set(targets) | set(positions):
        if t not in open_prices.index or pd.isna(open_prices[t]):
            raise ValueError(f"no open price for {t}")

    positions = dict(positions)
    slip = slippage_bps / 10_000.0
    value = cash + sum(sh * open_prices[t] for t, sh in positions.items())

    target_shares = {
        t: math.floor(w * value / open_prices[t]) for t, w in targets.items()
    }
    # tickers held but absent from targets -> implicit target 0
    for t in list(positions):
        target_shares.setdefault(t, 0)

    trades = []
    # sells first
    for t in sorted(target_shares):
        diff = target_shares[t] - positions.get(t, 0)
        if diff < 0:
            price = open_prices[t] * (1 - slip)
            cash += -diff * price
            trades.append({"ticker": t, "side": "sell", "shares": -diff, "price": price})
            positions[t] = target_shares[t]
            if positions[t] == 0:
                del positions[t]
    # then buys, capped at cash
    for t in sorted(target_shares):
        diff = target_shares[t] - positions.get(t, 0)
        if diff > 0:
            price = open_prices[t] * (1 + slip)
            affordable = math.floor(cash / price)
            if affordable < diff:
                print(f"WARNING: {t} buy reduced {diff} -> {affordable} (cash)")
                diff = affordable
            if diff <= 0:
                continue
            cash -= diff * price
            trades.append({"ticker": t, "side": "buy", "shares": diff, "price": price})
            positions[t] = positions.get(t, 0) + diff
    return positions, cash, trades
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fills.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine/fills.py tests/test_fills.py
git commit -m "feat: whole-share rebalance accounting with slippage, sells-first, no leverage"
```

---

### Task 6: Backtest loop (next-open fills, look-ahead-proof)

**Files:**
- Create: `src/engine/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `fills.execute_rebalance`, wide frames from `data.to_wide`.
- Produces:
  ```python
  @dataclass
  class BacktestConfig:
      initial_cash: float = 100_000.0
      slippage_bps: float = 5.0

  @dataclass
  class BacktestResult:
      equity: pd.Series          # daily, marked at close
      holdings_value: pd.Series  # daily position value at close (exposure calc)
      trades: pd.DataFrame       # columns: date, ticker, side, shares, price

  run_backtest(long_df: pd.DataFrame, strategy, config=None) -> BacktestResult
  ```
  Strategy duck-type: `.name`, `.params`, `.target_weights(window) -> dict | None`. Task 8 implements the base class; this task only needs the duck type.

- [ ] **Step 1: Write the failing tests**

`tests/test_backtest.py`:
```python
import pandas as pd
import pytest

from src.engine.backtest import BacktestConfig, run_backtest


def make_long(opens, closes, ticker="A", start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({
        "date": idx, "ticker": ticker, "open": opens, "high": closes,
        "low": opens, "close": closes, "volume": [1000] * len(opens)})


class BuyDayOneThenHold:
    name = "buyhold"
    params = {}
    def __init__(self):
        self.calls = []
    def target_weights(self, window):
        self.calls.append(window.index[-1])
        return {"A": 1.0} if len(window) == 1 else None


def test_fills_at_next_open_not_same_bar():
    # decision at day1 close; fill at day2 OPEN (110), never day1 close (100)
    df = make_long(opens=[100, 110, 110], closes=[100, 110, 120])
    res = run_backtest(df, BuyDayOneThenHold(),
                       BacktestConfig(initial_cash=11_000, slippage_bps=0))
    trade = res.trades.iloc[0]
    assert trade["price"] == 110.0          # day2 open
    assert trade["date"] == df["date"][1]   # day2
    assert trade["shares"] == 100           # floor(11000/110)


def test_equity_curve_hand_computed():
    df = make_long(opens=[100, 100, 100], closes=[100, 100, 110])
    res = run_backtest(df, BuyDayOneThenHold(),
                       BacktestConfig(initial_cash=10_000, slippage_bps=0))
    # day1: all cash 10000. day2: buy 100 @100 open, close 100 -> 10000.
    # day3: close 110 -> 11000.
    assert list(res.equity.values) == pytest.approx([10_000, 10_000, 11_000])
    assert res.holdings_value.iloc[-1] == pytest.approx(11_000)


def test_strategy_never_sees_future():
    df = make_long(opens=[100] * 5, closes=[100] * 5)
    strat = BuyDayOneThenHold()
    run_backtest(df, strat, BacktestConfig())
    dates = list(df["date"])
    # called once per bar except the last (no next open to fill at),
    # and window always ends at the decision date
    assert strat.calls == dates[:-1]


def test_window_mutation_cannot_corrupt_engine():
    class Mutator:
        name, params = "mut", {}
        def target_weights(self, window):
            window.iloc[:] = -1.0  # vandalize the window
            return None
    df = make_long(opens=[100] * 3, closes=[100] * 3)
    res = run_backtest(df, Mutator(), BacktestConfig(initial_cash=1_000))
    assert list(res.equity.values) == pytest.approx([1_000, 1_000, 1_000])


def test_nan_price_in_window_raises():
    df = make_long(opens=[100, 100, 100], closes=[100, float("nan"), 100])
    with pytest.raises(ValueError, match="NaN"):
        run_backtest(df, BuyDayOneThenHold(), BacktestConfig())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/engine/backtest.py`:
```python
"""Daily backtest loop.

Timeline per bar T (except the last):
  1. strategy sees closes up to and including T  (truncated copy — no future)
  2. non-None targets -> fill at T+1 OPEN with slippage
Equity is marked at each day's close. Same-bar fills are impossible:
the fill price row (T+1) is never inside the strategy's window (<= T).

NaN closes for a live-universe ticker are a data bug -> hard error.
Tickers with NaN (pre-inception) are dropped from the strategy window
per-day, so strategies only ever see tradeable instruments.
"""
from dataclasses import dataclass, field

import pandas as pd

from src.engine.data import to_wide
from src.engine.fills import execute_rebalance


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 5.0


@dataclass
class BacktestResult:
    equity: pd.Series
    holdings_value: pd.Series
    trades: pd.DataFrame


def run_backtest(long_df: pd.DataFrame, strategy, config: BacktestConfig = None) -> BacktestResult:
    config = config or BacktestConfig()
    closes = to_wide(long_df, "close")
    opens = to_wide(long_df, "open")

    positions: dict[str, int] = {}
    cash = config.initial_cash
    equity, holdings, all_trades = [], [], []
    pending_targets = None

    for i, date in enumerate(closes.index):
        # 1. fill yesterday's decision at today's open
        if pending_targets is not None:
            positions, cash, trades = execute_rebalance(
                positions, cash, pending_targets, opens.loc[date],
                config.slippage_bps)
            for tr in trades:
                all_trades.append({"date": date, **tr})
            pending_targets = None

        # 2. mark at close
        row = closes.loc[date]
        for t in positions:
            if pd.isna(row[t]):
                raise ValueError(f"NaN close for held ticker {t} on {date.date()}")
        pos_value = sum(sh * row[t] for t, sh in positions.items())
        equity.append(cash + pos_value)
        holdings.append(pos_value)

        # 3. decide (skip last bar — no next open to fill at)
        if i < len(closes.index) - 1:
            window = closes.iloc[: i + 1]
            window = window.dropna(axis=1, how="any").copy()  # pre-inception cols out; copy = mutation-proof
            targets = strategy.target_weights(window)
            if targets is not None:
                for t in targets:
                    if t not in closes.columns:
                        raise ValueError(f"strategy returned unknown ticker {t}")
                pending_targets = targets

    return BacktestResult(
        equity=pd.Series(equity, index=closes.index, name="equity"),
        holdings_value=pd.Series(holdings, index=closes.index, name="holdings"),
        trades=pd.DataFrame(all_trades,
                            columns=["date", "ticker", "side", "shares", "price"]),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_backtest.py -v`
Expected: 5 PASS. (`test_nan_price_in_window_raises` exercises the held-ticker NaN check: strategy buys A on day 1, fill day 2, day 2 close is NaN → `ValueError`.)

- [ ] **Step 5: Run whole suite**

Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/engine/backtest.py tests/test_backtest.py
git commit -m "feat: backtest loop with next-open fills and look-ahead-proof windows"
```

---

### Task 7: Metrics

**Files:**
- Create: `src/engine/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `BacktestResult` fields.
- Produces:
  ```python
  cagr(equity: pd.Series) -> float
  max_drawdown(equity: pd.Series) -> float          # negative number
  sharpe(equity: pd.Series) -> float
  yearly_returns(equity: pd.Series) -> pd.Series    # index = year int
  annual_turnover(trades: pd.DataFrame, equity: pd.Series) -> float
  avg_exposure(holdings_value: pd.Series, equity: pd.Series) -> float
  summarize(result) -> dict   # all of the above + n_trades, positive_years,
                              # sample_flag ("OK" / "LOW <100" / "INSUFFICIENT <30")
  ```
  `summarize` dict keys: `cagr, max_dd, sharpe, n_trades, turnover, exposure, positive_years, total_years, sample_flag`. Task 10's leaderboard consumes exactly these keys.

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics.py`:
```python
import numpy as np
import pandas as pd
import pytest

from src.engine import metrics


def make_equity(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)),
                     dtype=float)


def test_cagr_doubling_in_252_days():
    eq = make_equity(np.linspace(100, 200, 253))
    # 252 daily periods -> exactly one 252-day year -> CAGR 100%
    assert metrics.cagr(eq) == pytest.approx(1.0, abs=0.01)


def test_max_drawdown():
    eq = make_equity([100, 120, 90, 110])
    assert metrics.max_drawdown(eq) == pytest.approx(90 / 120 - 1)  # -25%


def test_sharpe_zero_vol_is_nan():
    eq = make_equity([100.0] * 10)
    assert np.isnan(metrics.sharpe(eq))


def test_yearly_returns():
    idx = pd.to_datetime(["2020-06-01", "2020-12-31", "2021-06-01", "2021-12-31"])
    eq = pd.Series([100.0, 110.0, 115.5, 121.0], index=idx)
    yr = metrics.yearly_returns(eq)
    assert yr.loc[2020] == pytest.approx(0.10)
    assert yr.loc[2021] == pytest.approx(0.10)  # 110 -> 121


def test_annual_turnover():
    eq = make_equity([10_000.0] * 253)  # one 252-day year, avg equity 10k
    trades = pd.DataFrame([
        {"date": eq.index[10], "ticker": "A", "side": "buy", "shares": 50, "price": 100.0},
        {"date": eq.index[100], "ticker": "A", "side": "sell", "shares": 50, "price": 100.0},
    ])
    # traded value 10k over 1 year on 10k equity -> turnover 1.0x
    assert metrics.annual_turnover(trades, eq) == pytest.approx(1.0, abs=0.01)


def test_summarize_flags_small_sample():
    eq = make_equity(np.linspace(100, 110, 50))
    holdings = eq * 0.5
    trades = pd.DataFrame([{"date": eq.index[1], "ticker": "A", "side": "buy",
                            "shares": 1, "price": 100.0}] * 5)
    class R: pass
    r = R(); r.equity, r.holdings_value, r.trades = eq, holdings, trades
    s = metrics.summarize(r)
    assert s["n_trades"] == 5
    assert s["sample_flag"] == "INSUFFICIENT <30"
    assert s["exposure"] == pytest.approx(0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/engine/metrics.py`:
```python
"""Performance metrics + honesty stats. All from equity/trades — no magic."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cagr(equity: pd.Series) -> float:
    periods = len(equity) - 1
    if periods <= 0:
        return np.nan
    return (equity.iloc[-1] / equity.iloc[0]) ** (TRADING_DAYS / periods) - 1


def max_drawdown(equity: pd.Series) -> float:
    return (equity / equity.cummax() - 1).min()


def sharpe(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    if len(r) == 0 or r.std() == 0:
        return np.nan
    return r.mean() / r.std() * np.sqrt(TRADING_DAYS)


def yearly_returns(equity: pd.Series) -> pd.Series:
    """Return per calendar year, chaining from previous year's last value."""
    last = equity.groupby(equity.index.year).last()
    first_value = equity.iloc[0]
    prev = last.shift(1)
    prev.iloc[0] = first_value
    return last / prev - 1


def annual_turnover(trades: pd.DataFrame, equity: pd.Series) -> float:
    if len(trades) == 0:
        return 0.0
    traded = (trades["shares"] * trades["price"]).sum()
    years = (len(equity) - 1) / TRADING_DAYS
    return traded / equity.mean() / years if years > 0 else np.nan


def avg_exposure(holdings_value: pd.Series, equity: pd.Series) -> float:
    return (holdings_value / equity).mean()


def summarize(result) -> dict:
    yr = yearly_returns(result.equity)
    n = len(result.trades)
    flag = "OK" if n >= 100 else ("LOW <100" if n >= 30 else "INSUFFICIENT <30")
    return {
        "cagr": cagr(result.equity),
        "max_dd": max_drawdown(result.equity),
        "sharpe": sharpe(result.equity),
        "n_trades": n,
        "turnover": annual_turnover(result.trades, result.equity),
        "exposure": avg_exposure(result.holdings_value, result.equity),
        "positive_years": int((yr > 0).sum()),
        "total_years": len(yr),
        "sample_flag": flag,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_metrics.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/engine/metrics.py tests/test_metrics.py
git commit -m "feat: performance metrics with sample-size honesty flags"
```

---

### Task 8: Strategy base + time-series trend reference strategy

**Files:**
- Create: `src/strategies/base.py`, `src/strategies/ts_trend.py`
- Test: `tests/test_ts_trend.py`

**Interfaces:**
- Produces:
  ```python
  class Strategy:                      # src/strategies/base.py
      name: str = "base"
      def __init__(self, **params): self.params = {**self.DEFAULTS, **params}
      DEFAULTS: dict = {}
      def target_weights(self, window) -> dict | None: raise NotImplementedError
      def is_month_start(self, window) -> bool   # shared monthly-rebalance helper

  class TSTrend(Strategy):             # src/strategies/ts_trend.py
      name = "ts_trend"
      DEFAULTS = {"lookback": 125}
  ```
  TSTrend rule (Clenow ch 16 adapted, long-only): on the first trading day of each month, go long every ETF whose close > close `lookback` bars ago, each at weight `1/n_universe_columns`; otherwise weight 0 (cash). Non-rebalance days return `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ts_trend.py`:
```python
import numpy as np
import pandas as pd
import pytest

from src.strategies.ts_trend import TSTrend


def make_window(n_days, trend_cols, flat_cols, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    data = {}
    for c in trend_cols:
        data[c] = np.linspace(100, 200, n_days)   # rising
    for c in flat_cols:
        data[c] = np.linspace(100, 80, n_days)    # falling
    return pd.DataFrame(data, index=idx)


def test_none_when_not_month_start():
    w = make_window(50, ["A"], ["B"])
    # pick a window whose last two dates share a month
    assert w.index[-1].month == w.index[-2].month
    assert TSTrend(lookback=20).target_weights(w) is None


def _first_month_boundary(window):
    """Truncate window so it ends on the first day of a new month."""
    months = window.index.month
    for i in range(1, len(window)):
        if months[i] != months[i - 1]:
            return window.iloc[: i + 1]
    raise AssertionError("no month boundary in window")


def test_longs_rising_skips_falling_on_month_start():
    w = _first_month_boundary(make_window(60, ["A"], ["B"]))
    weights = TSTrend(lookback=20).target_weights(w)
    assert weights == {"A": 0.5}   # 1/2 columns; B falling -> cash


def test_all_cash_when_everything_falls():
    w = _first_month_boundary(make_window(60, [], ["A", "B"]))
    assert TSTrend(lookback=20).target_weights(w) == {}


def test_none_when_history_shorter_than_lookback():
    w = _first_month_boundary(make_window(60, ["A"], []))
    assert TSTrend(lookback=500).target_weights(w) is None


def test_weights_never_exceed_one():
    w = _first_month_boundary(make_window(60, ["A", "B", "C"], []))
    weights = TSTrend(lookback=20).target_weights(w)
    assert sum(weights.values()) <= 1.0 + 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ts_trend.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/strategies/base.py`:
```python
"""Strategy plugin contract. A strategy is a pure function of PAST closes:
window (wide closes, ending at decision day) -> target weights or None (hold).
The engine fills at next open — strategies never touch prices or shares.
"""


class Strategy:
    name = "base"
    DEFAULTS: dict = {}

    def __init__(self, **params):
        unknown = set(params) - set(self.DEFAULTS)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {unknown}")
        self.params = {**self.DEFAULTS, **params}

    def target_weights(self, window):
        raise NotImplementedError

    @staticmethod
    def is_month_start(window) -> bool:
        """True when the window's last bar is the first trading day of a month."""
        if len(window) < 2:
            return False
        return window.index[-1].month != window.index[-2].month

    def label(self) -> str:
        p = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({p})"
```

`src/strategies/ts_trend.py`:
```python
"""Time-series trend (Clenow Trading Evolved ch 16, long-only ETF adaptation).

Monthly: long each ETF whose close > close `lookback` bars ago, at 1/N_universe
weight each. Unqualified slots stay in cash — exposure auto-scales in bears.
"""
from src.strategies.base import Strategy


class TSTrend(Strategy):
    name = "ts_trend"
    DEFAULTS = {"lookback": 125}

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None
        today = window.iloc[-1]
        past = window.iloc[-1 - look]
        qualified = [t for t in window.columns if today[t] > past[t]]
        w = 1.0 / len(window.columns)
        return {t: w for t in qualified}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ts_trend.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Smoke-run on real playground data**

Run:
```bash
.venv/bin/python -c "
from src.engine.data import load_playground
from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.ts_trend import TSTrend
r = run_backtest(load_playground(), TSTrend())
print(summarize(r))"
```
Expected: prints a metrics dict — sane values (CAGR roughly 0–15%, max_dd negative, n_trades in the hundreds). Numbers are NOT results yet, only an engine smoke test.

- [ ] **Step 6: Commit**

```bash
git add src/strategies/base.py src/strategies/ts_trend.py tests/test_ts_trend.py
git commit -m "feat: strategy base contract and time-series trend reference strategy"
```

---

### Task 9: Momentum rotation reference strategy

**Files:**
- Create: `src/strategies/momentum_rotation.py`
- Test: `tests/test_momentum_rotation.py`

**Interfaces:**
- Consumes: `Strategy` base from Task 8.
- Produces:
  ```python
  momentum_score(closes: pd.Series) -> float   # annualized exp-regression slope * R^2
  class MomentumRotation(Strategy):
      name = "momentum_rotation"
      DEFAULTS = {"lookback": 125, "top_n": 3, "vol_window": 20, "min_score": 0.0}
  ```
  Rule (Clenow ch 12 adapted to a fixed ETF basket): monthly, score every ETF over `lookback` closes, take the `top_n` with score > `min_score`, weight them by inverse 20-day volatility (weights sum to 1 across selected; fewer qualifiers = rest stays cash via fewer names… weights renormalized across qualifiers only).

- [ ] **Step 1: Write the failing tests**

`tests/test_momentum_rotation.py`:
```python
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.strategies.momentum_rotation import MomentumRotation, momentum_score


def geometric_series(daily_ret, n, start=100.0):
    return pd.Series(start * (1 + daily_ret) ** np.arange(n))


def test_momentum_score_matches_hand_computed_linregress():
    s = geometric_series(0.001, 60)
    y = np.log(s.values)
    slope, _, r, _, _ = stats.linregress(np.arange(60), y)
    expected = (np.exp(slope) ** 252 - 1) * 100 * r**2
    assert momentum_score(s) == pytest.approx(expected)


def test_momentum_score_perfect_uptrend_positive():
    assert momentum_score(geometric_series(0.001, 60)) > 0


def test_momentum_score_downtrend_negative():
    assert momentum_score(geometric_series(-0.001, 60)) < 0


def make_window(n_days, rets: dict, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame(
        {t: geometric_series(r, n_days).values for t, r in rets.items()}, index=idx)


def _month_boundary_after(window, min_rows):
    """Truncate so the window ends on a month's first trading day AND has
    at least min_rows history (so lookback checks pass)."""
    months = window.index.month
    for i in range(min_rows, len(window)):
        if months[i] != months[i - 1]:
            return window.iloc[: i + 1]
    raise AssertionError("no month boundary after min_rows — enlarge window")


def test_selects_top_n_by_momentum():
    w = _month_boundary_after(make_window(
        80, {"HOT": 0.002, "WARM": 0.001, "COLD": -0.001, "MEH": 0.0002}), 41)
    weights = MomentumRotation(lookback=40, top_n=2).target_weights(w)
    assert set(weights) == {"HOT", "WARM"}
    assert sum(weights.values()) == pytest.approx(1.0)


def test_min_score_leaves_cash():
    w = _month_boundary_after(make_window(80, {"A": -0.001, "B": -0.002}), 41)
    weights = MomentumRotation(lookback=40, top_n=2, min_score=0.0).target_weights(w)
    assert weights == {}


def test_inverse_vol_weights_favor_calm_asset():
    # equal trend, different noise -> calmer asset gets more weight
    n = 80
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(7)
    base = 0.001 + rng.normal(0, 0.001, n)
    calm = 100 * np.cumprod(1 + base)
    wild = 100 * np.cumprod(1 + base + rng.normal(0, 0.02, n))
    w = _month_boundary_after(pd.DataFrame({"CALM": calm, "WILD": wild}, index=idx), 41)
    weights = MomentumRotation(lookback=40, top_n=2, min_score=-1000).target_weights(w)
    assert weights["CALM"] > weights["WILD"]


def test_none_outside_month_start():
    w = make_window(50, {"A": 0.001})
    assert w.index[-1].month == w.index[-2].month
    assert MomentumRotation(lookback=20).target_weights(w) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_momentum_rotation.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/strategies/momentum_rotation.py`:
```python
"""Cross-sectional momentum rotation (Clenow Trading Evolved ch 12, adapted
from S&P 500 stocks to our fixed ETF basket — no index-membership machinery).

Score = annualized exponential regression slope * R^2: rewards strong AND
smooth trends. Monthly: hold top_n by score (score > min_score), weighted by
inverse 20d volatility (equal-risk-ish). Nothing qualifies -> cash.

NB: min_score is window-length dependent (Clenow used 40 on 125d stock data);
default 0 = 'any positive momentum', sweep it in the batch runner.
"""
import numpy as np
import pandas as pd
from scipy import stats

from src.strategies.base import Strategy


def momentum_score(closes: pd.Series) -> float:
    y = np.log(closes.values)
    x = np.arange(len(y))
    slope, _, r, _, _ = stats.linregress(x, y)
    return (np.exp(slope) ** 252 - 1) * 100 * r**2


class MomentumRotation(Strategy):
    name = "momentum_rotation"
    DEFAULTS = {"lookback": 125, "top_n": 3, "vol_window": 20, "min_score": 0.0}

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None

        recent = window.iloc[-look:]
        scores = recent.apply(momentum_score)
        top = scores.nlargest(self.params["top_n"])
        top = top[top > self.params["min_score"]]
        if top.empty:
            return {}

        vol = (window[top.index].pct_change()
               .iloc[-self.params["vol_window"]:].std())
        inv = 1.0 / vol
        weights = inv / inv.sum()
        return weights.to_dict()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_momentum_rotation.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Smoke-run on playground**

Run:
```bash
.venv/bin/python -c "
from src.engine.data import load_playground
from src.engine.backtest import run_backtest
from src.engine.metrics import summarize
from src.strategies.momentum_rotation import MomentumRotation
r = run_backtest(load_playground(), MomentumRotation())
print(summarize(r))"
```
Expected: sane metrics dict, runs in seconds. Not results — smoke test.

- [ ] **Step 6: Commit**

```bash
git add src/strategies/momentum_rotation.py tests/test_momentum_rotation.py
git commit -m "feat: momentum rotation reference strategy (Clenow ch12 ETF adaptation)"
```

---

### Task 10: Batch runner, leaderboard, plateau report

**Files:**
- Create: `src/batch/runner.py`, `scripts/screen.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `run_backtest`, `summarize`, `Strategy.label()`.
- Produces:
  ```python
  expand_grid(cls, grid: dict[str, list]) -> list[Strategy]
  run_batch(strategies: list, long_df, config=None) -> pd.DataFrame
      # one row per strategy: label, name, <param cols>, <summarize() cols>, error
  luck_warning(n_runs: int, years: float) -> str
      # expected max |Sharpe| by pure luck ~ sqrt(2*ln(N)/T)
  plateau_table(leaderboard, name, param, metric="sharpe") -> pd.DataFrame
      # metric vs one param, other params as rows — spikes vs plateaus at a glance
  ```
  `scripts/screen.py` = CLI: run a hardcoded strategy×grid list on playground, save `results/leaderboard_<runstamp>.csv`, print leaderboard + luck warning.

- [ ] **Step 1: Write the failing tests**

`tests/test_batch.py`:
```python
import numpy as np
import pandas as pd
import pytest

from src.batch.runner import expand_grid, luck_warning, plateau_table, run_batch
from src.engine.backtest import BacktestConfig
from src.strategies.base import Strategy
from src.strategies.ts_trend import TSTrend


def make_long(n_days=300):
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    rows = []
    for t, drift in (("UP", 0.001), ("DOWN", -0.0005)):
        px = 100 * np.cumprod(1 + np.full(n_days, drift))
        for d, p in zip(idx, px):
            rows.append({"date": d, "ticker": t, "open": p, "high": p,
                         "low": p, "close": p, "volume": 1000})
    return pd.DataFrame(rows)


def test_expand_grid_cartesian_product():
    strats = expand_grid(TSTrend, {"lookback": [50, 100, 150]})
    assert len(strats) == 3
    assert sorted(s.params["lookback"] for s in strats) == [50, 100, 150]


def test_run_batch_one_row_per_strategy():
    lb = run_batch(expand_grid(TSTrend, {"lookback": [20, 40]}), make_long(),
                   BacktestConfig(slippage_bps=0))
    assert len(lb) == 2
    assert {"label", "sharpe", "cagr", "n_trades", "lookback"} <= set(lb.columns)


def test_run_batch_survives_crashing_strategy():
    class Crasher(Strategy):
        name = "crasher"
        DEFAULTS = {}
        def target_weights(self, window):
            raise RuntimeError("boom")
    lb = run_batch([Crasher(), TSTrend(lookback=20)], make_long(),
                   BacktestConfig(slippage_bps=0))
    assert len(lb) == 2
    crashed = lb[lb["name"] == "crasher"].iloc[0]
    assert "boom" in crashed["error"]
    assert pd.isna(crashed["sharpe"])


def test_luck_warning_grows_with_n():
    w10, w1000 = luck_warning(10, 11), luck_warning(1000, 11)
    def sharpe_of(w):
        return float(w.split("~")[1].split()[0])
    assert sharpe_of(w1000) > sharpe_of(w10)


def test_plateau_table_pivots_param_vs_metric():
    lb = run_batch(expand_grid(TSTrend, {"lookback": [20, 40, 60]}), make_long(),
                   BacktestConfig(slippage_bps=0))
    tab = plateau_table(lb, "ts_trend", "lookback")
    assert list(tab.columns) == [20, 40, 60]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_batch.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/batch/runner.py`:
```python
"""Batch runner: strategy x parameter grid -> leaderboard with honesty stats.

One crashing strategy never kills the batch — it gets an error row.
The luck warning is printed with every leaderboard: after N tries on the
same data, the best Sharpe you see is inflated by selection alone.
"""
import itertools
import math
import traceback

import numpy as np
import pandas as pd

from src.engine.backtest import run_backtest
from src.engine.metrics import summarize


def expand_grid(cls, grid: dict) -> list:
    keys = sorted(grid)
    return [cls(**dict(zip(keys, combo)))
            for combo in itertools.product(*(grid[k] for k in keys))]


def run_batch(strategies: list, long_df: pd.DataFrame, config=None) -> pd.DataFrame:
    rows = []
    for strat in strategies:
        row = {"label": strat.label(), "name": strat.name, **strat.params,
               "error": ""}
        try:
            row.update(summarize(run_backtest(long_df, strat, config)))
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        rows.append(row)
    lb = pd.DataFrame(rows)
    if "sharpe" in lb.columns:
        lb = lb.sort_values("sharpe", ascending=False, na_position="last")
    return lb.reset_index(drop=True)


def luck_warning(n_runs: int, years: float) -> str:
    exp_max = math.sqrt(2 * math.log(max(n_runs, 2)) / years)
    return (f"MULTIPLE-TESTING WARNING: {n_runs} runs on {years:.0f}y of data -> "
            f"best-by-pure-luck Sharpe ~{exp_max:.2f} — results below that "
            f"line are indistinguishable from noise.")


def plateau_table(leaderboard: pd.DataFrame, name: str, param: str,
                  metric: str = "sharpe") -> pd.DataFrame:
    """Pivot metric over one param; other params become rows.
    A robust edge shows a PLATEAU across columns; a spike = curve fit."""
    fam = leaderboard[leaderboard["name"] == name].copy()
    fam = fam.dropna(axis=1, how="all")  # other families' param cols are all-NaN here
    other = [c for c in fam.columns
             if c not in {param, metric, "label", "name", "error"}
             and c in _param_cols(fam, name)]
    index = other if other else None
    if index is None:
        fam["_"] = "all"
        index = ["_"]
    return fam.pivot_table(index=index, columns=param, values=metric)


def _param_cols(fam: pd.DataFrame, name: str) -> set:
    metric_cols = {"cagr", "max_dd", "sharpe", "n_trades", "turnover",
                   "exposure", "positive_years", "total_years", "sample_flag"}
    return {c for c in fam.columns
            if c not in metric_cols | {"label", "name", "error"}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_batch.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Write the screening CLI**

`scripts/screen.py`:
```python
"""First screening session: reference strategies x parameter grids on
PLAYGROUND data only. Saves results/leaderboard_<runstamp>.csv.

Runstamp comes from the git commit + wall clock so runs are traceable.
"""
import subprocess
from datetime import datetime
from pathlib import Path

from src.batch.runner import expand_grid, luck_warning, plateau_table, run_batch
from src.engine.data import load_playground
from src.strategies.momentum_rotation import MomentumRotation
from src.strategies.ts_trend import TSTrend

GRIDS = [
    (TSTrend, {"lookback": [63, 125, 189, 252]}),
    (MomentumRotation, {"lookback": [63, 125, 189],
                        "top_n": [2, 3, 5],
                        "vol_window": [20],
                        "min_score": [0.0, 20.0, 40.0]}),
]


def main():
    long_df = load_playground()
    strategies = [s for cls, grid in GRIDS for s in expand_grid(cls, grid)]
    print(f"running {len(strategies)} backtests on playground…")
    lb = run_batch(strategies, long_df)

    years = (long_df["date"].max() - long_df["date"].min()).days / 365.25
    print()
    print(luck_warning(len(strategies), years))
    print()
    print(lb.to_string(index=False, max_colwidth=40))
    for cls, grid in GRIDS:
        for param, values in grid.items():
            if len(values) > 1:
                print(f"\nPlateau: {cls.name} / {param} (sharpe)")
                print(plateau_table(lb, cls.name, param).round(2).to_string())

    Path("results").mkdir(exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(f"results/leaderboard_{stamp}_{sha}.csv")
    lb.to_csv(out, index=False)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the full suite, then the screen**

Run: `.venv/bin/pytest -q`
Expected: all green.

Run: `.venv/bin/python scripts/screen.py`
Expected: 31 backtests (4 ts_trend + 27 momentum_rotation) complete in under a couple of minutes, luck warning printed, plateau tables printed, CSV saved. Findings go to the vault (Backtests notes) — that's a session activity after this plan, not a code task.

- [ ] **Step 7: Commit**

```bash
git add src/batch/runner.py scripts/screen.py tests/test_batch.py
git commit -m "feat: batch runner with leaderboard, luck warning, and plateau report"
```

---

## Post-plan (session work, not code tasks)

- Write vault Backtests notes for the first screening results (verdicts keep/kill/iterate per propagation rules).
- Update `07 ETF Bot/_STATUS.md` phase → "screening".
- Exam runs remain forbidden until a strategy earns a pre-registered exam decision note.
