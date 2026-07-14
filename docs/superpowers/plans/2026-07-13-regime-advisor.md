# Macro Regime Advisor Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read-only macro advisor: per-day regime state (trend/vol/position) for SPY and any traded ticker, historical base-rate tables, and a wheel-campaign autopsy grouped by regime — per spec `docs/superpowers/specs/2026-07-13-regime-advisor-design.md`.

**Architecture:** New pure package `src/engine_v2/regime/` (state → base rates → autopsy), fed by daily closes already on disk; a read-only dashboard tab. Nothing in `options/` imports `regime/`.

**Tech Stack:** Python 3.9, pandas, pytest, Streamlit (dashboard only).

## Global Constraints

- Test command: `.venv/bin/python -m pytest` (NEVER `.venv/bin/pytest` — stale shebangs).
- Fixed thresholds, chosen ex-ante, never swept: trend rules as in spec; vol percentiles calm<40 / stressed>75 vs trailing 3y (min 1y); warmup = first 200 trading days; thin cell = N<30; horizon default 21.
- No look-ahead: state on day d uses closes ≤ d only (property-tested).
- No network access inside the module — callers pass `pd.Series` of closes.
- No trading-decision code anywhere in this phase.
- Work on branch `regime-advisor`; merge to main when all gates pass.
- Coverage: `tests/engine_v2/` ≥ 85% on `src/engine_v2`.

---

### Task 1: closes loader (`regime/data.py`)

**Files:**
- Create: `src/engine_v2/regime/__init__.py` (empty)
- Create: `src/engine_v2/regime/data.py`
- Test: `tests/engine_v2/regime/test_data.py` (+ empty `tests/engine_v2/regime/__init__.py`)

**Interfaces:**
- Produces: `closes_for(symbol: str, bars_path="fixtures/bars_etf_universe_2010_2026.parquet") -> pd.Series` — daily closes, DatetimeIndex ascending, name=symbol. Prefers the long bars fixture (yfinance MultiIndex columns `(Ticker, Price)`); falls back to the chain parquet's `underlying` column (`data/options/{symbol}_greeks_eod_all.parquet`); raises `FileNotFoundError` if neither has the symbol.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd
import pytest
from src.engine_v2.regime.data import closes_for

def test_spy_comes_from_long_bars():
    s = closes_for("SPY")
    assert s.index.min().year <= 2010 and len(s) > 3000
    assert s.name == "SPY" and s.index.is_monotonic_increasing

def test_gdx_falls_back_to_chain_underlying():
    s = closes_for("GDX")   # not in the bars fixture
    assert s.index.min().year >= 2017 and len(s) > 1000

def test_unknown_symbol_raises():
    with pytest.raises(FileNotFoundError):
        closes_for("ZZZTOP")
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/engine_v2/regime/ -v` → ImportError.

- [ ] **Step 3: Implement**

```python
"""Daily closes for the regime advisor, from data already on disk.
No network. Long bars fixture first (2010+), chain underlying (2017+) second."""
from __future__ import annotations
import os
import pandas as pd

BARS = "fixtures/bars_etf_universe_2010_2026.parquet"

def closes_for(symbol: str, bars_path: str = BARS) -> pd.Series:
    symbol = symbol.upper()
    if os.path.exists(bars_path):
        bars = pd.read_parquet(bars_path)
        if isinstance(bars.columns, pd.MultiIndex) and symbol in bars.columns.get_level_values(0):
            s = bars[(symbol, "Close")].dropna()
            s.index = pd.to_datetime(s.index)
            return s.sort_index().rename(symbol)
    chain = f"data/options/{symbol.lower()}_greeks_eod_all.parquet"
    if os.path.exists(chain):
        df = pd.read_parquet(chain, columns=["date", "underlying"])
        s = df.groupby("date")["underlying"].first()
        s.index = pd.to_datetime(s.index)
        return s.sort_index().rename(symbol)
    raise FileNotFoundError(f"no daily closes on disk for {symbol}")
```

- [ ] **Step 4: Run to verify pass**, **Step 5: Commit** `feat: regime closes loader (bars fixture first, chain fallback)`

---

### Task 2: regime state (`regime/state.py`)

**Files:**
- Create: `src/engine_v2/regime/state.py`
- Test: `tests/engine_v2/regime/test_state.py`

**Interfaces:**
- Produces: `regime_series(closes: pd.Series) -> pd.DataFrame` with columns `trend, vol, px_vs_200, px_vs_50, ma50_vs_200, drawdown, realized_vol, vol_pctile` indexed by date (warmup rows dropped); `describe(row) -> str` accepting one DataFrame row. Constants `WARMUP=200, VOL_WINDOW=21, VOL_LOOKBACK=756, VOL_MIN=252, CALM=0.40, STRESSED=0.75, HIGH_WINDOW=252`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pandas as pd
import pytest
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series, describe

@pytest.fixture(scope="module")
def spy():
    return regime_series(closes_for("SPY"))

def test_known_regimes_on_real_spy(spy):
    assert spy.loc["2021-07-01", "trend"] == "uptrend"
    assert spy.loc["2021-07-01", "vol"] == "calm"
    assert spy.loc["2022-06-15", "trend"] == "downtrend"
    assert spy.loc["2020-03-20", "vol"] == "stressed"
    assert spy.loc["2020-03-20", "drawdown"] < -0.25

def test_warmup_dropped(spy):
    raw = closes_for("SPY")
    assert len(spy) <= len(raw) - 200

def test_no_look_ahead():
    closes = closes_for("SPY")
    for k in (500, 1500, 2500):
        full = regime_series(closes)
        part = regime_series(closes.iloc[:k])
        d = part.index[-1]
        pd.testing.assert_series_equal(full.loc[d], part.loc[d], check_names=False)

def test_short_series_returns_empty():
    s = pd.Series(np.linspace(100, 110, 150),
                  index=pd.bdate_range("2024-01-01", periods=150))
    assert regime_series(s).empty

def test_describe_reads_plainly(spy):
    txt = describe(spy.loc["2021-07-01"])
    assert "uptrend" in txt.lower() and "calm" in txt.lower() and "%" in txt
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

```python
"""Per-day market regime from daily closes alone. Fixed ex-ante thresholds —
never swept. All windows trailing: the state on day d uses closes <= d only."""
from __future__ import annotations
import numpy as np
import pandas as pd

WARMUP = 200          # trading days before the first state is emitted
VOL_WINDOW = 21       # realized-vol window (days)
VOL_LOOKBACK = 756    # percentile lookback (~3y)
VOL_MIN = 252         # minimum history for the percentile (~1y)
CALM, STRESSED = 0.40, 0.75
HIGH_WINDOW = 252     # rolling-high window for drawdown

def regime_series(closes: pd.Series) -> pd.DataFrame:
    c = closes.dropna().astype(float)
    if len(c) <= WARMUP:
        return pd.DataFrame(columns=["trend","vol","px_vs_200","px_vs_50",
                                     "ma50_vs_200","drawdown","realized_vol","vol_pctile"])
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    logret = np.log(c / c.shift(1))
    rv = logret.rolling(VOL_WINDOW).std() * np.sqrt(252)
    # percentile of today's realized vol within its own trailing window
    pct = rv.rolling(VOL_LOOKBACK, min_periods=VOL_MIN).rank(pct=True)
    high = c.rolling(HIGH_WINDOW, min_periods=1).max()
    df = pd.DataFrame({
        "px_vs_200": c / sma200 - 1,
        "px_vs_50": c / sma50 - 1,
        "ma50_vs_200": sma50 / sma200 - 1,
        "drawdown": c / high - 1,
        "realized_vol": rv,
        "vol_pctile": pct,
    })
    up = (c > sma200) & (sma50 > sma200)
    down = (c < sma200) & (sma50 < sma200)
    df["trend"] = np.where(up, "uptrend", np.where(down, "downtrend", "chop"))
    df["vol"] = np.where(df["vol_pctile"] < CALM, "calm",
                np.where(df["vol_pctile"] > STRESSED, "stressed", "normal"))
    df = df.iloc[WARMUP:].dropna(subset=["px_vs_200", "vol_pctile"])
    return df[["trend","vol","px_vs_200","px_vs_50","ma50_vs_200",
               "drawdown","realized_vol","vol_pctile"]]

def describe(row) -> str:
    side = "above" if row["px_vs_200"] >= 0 else "below"
    cross = "50>200" if row["ma50_vs_200"] >= 0 else "50<200"
    return (f"{row['trend'].capitalize()} ({abs(row['px_vs_200']):.1%} {side} 200d, {cross}), "
            f"{row['vol']} vol ({row['vol_pctile']:.0%} pctile), "
            f"{abs(row['drawdown']):.1%} off 252d high.")
```

- [ ] **Step 4: Run to verify pass** (if a known-date label fails, print the row and check the DATE assumption against the data before touching thresholds — thresholds are ex-ante). **Step 5: Commit** `feat: regime state module (trend/vol/position, no look-ahead)`

---

### Task 3: base rates (`regime/base_rates.py`)

**Files:**
- Create: `src/engine_v2/regime/base_rates.py`
- Test: `tests/engine_v2/regime/test_base_rates.py`

**Interfaces:**
- Produces: `base_rate_table(closes: pd.Series, horizon: int = 21) -> pd.DataFrame` — one row per (trend, vol) cell: `n, win_rate, median_fwd, mean_fwd, p5_fwd, thin`; `df.attrs["caveat"]` = the walk-forward warning string `CAVEAT`. `THIN_N = 30`.

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pandas as pd
from src.engine_v2.regime.base_rates import base_rate_table, THIN_N, CAVEAT

def _steady_up():
    # 700 days of smooth 0.05%/day growth with tiny alternating noise:
    # after warmup everything is uptrend, forward returns known exactly.
    n = 700
    noise = np.array([1e-4 * (-1) ** i for i in range(n)]).cumsum()
    px = 100 * np.exp(np.arange(n) * 5e-4 + noise)
    return pd.Series(px, index=pd.bdate_range("2020-01-01", periods=n))

def test_uptrend_cell_matches_known_growth():
    tbl = base_rate_table(_steady_up(), horizon=21)
    row = tbl[(tbl["trend"] == "uptrend")].iloc[0]
    assert row["win_rate"] == 1.0
    assert abs(row["median_fwd"] - (np.exp(21 * 5e-4) - 1)) < 0.005
    assert row["n"] > 300

def test_thin_cells_flagged():
    tbl = base_rate_table(_steady_up(), horizon=21)
    assert ((tbl["n"] < THIN_N) == tbl["thin"]).all()

def test_caveat_attached():
    tbl = base_rate_table(_steady_up())
    assert "walk-forward" in tbl.attrs["caveat"]
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

```python
"""Historical base rates per regime cell: what the underlying did over the
next `horizon` days from each state. Descriptive context for a human."""
from __future__ import annotations
import pandas as pd
from .state import regime_series

THIN_N = 30
CAVEAT = ("Full-history descriptive statistics — context for a human, not a "
          "signal. Any backtested decision using these numbers must recompute "
          "them walk-forward (only data before each decision date). Overlapping "
          "forward windows: N counts days, not independent samples.")

def base_rate_table(closes: pd.Series, horizon: int = 21) -> pd.DataFrame:
    states = regime_series(closes)
    c = closes.dropna().astype(float)
    fwd = c.shift(-horizon) / c - 1
    df = states.join(fwd.rename("fwd")).dropna(subset=["fwd"])
    rows = []
    for (trend, vol), g in df.groupby(["trend", "vol"]):
        rows.append(dict(trend=trend, vol=vol, n=len(g),
                         win_rate=float((g["fwd"] > 0).mean()),
                         median_fwd=float(g["fwd"].median()),
                         mean_fwd=float(g["fwd"].mean()),
                         p5_fwd=float(g["fwd"].quantile(0.05)),
                         thin=len(g) < THIN_N))
    out = pd.DataFrame(rows, columns=["trend","vol","n","win_rate","median_fwd",
                                      "mean_fwd","p5_fwd","thin"])
    out.attrs["caveat"] = CAVEAT
    return out
```

- [ ] **Step 4: Run to verify pass.** **Step 5: Commit** `feat: regime base-rate tables (descriptive, caveat attached)`

---

### Task 4: wheel autopsy (`regime/autopsy.py`)

**Files:**
- Create: `src/engine_v2/regime/autopsy.py`
- Test: `tests/engine_v2/regime/test_autopsy.py`

**Interfaces:**
- Consumes: `campaign_table(result, cfg)` from `src.engine_v2.options.report`; `regime_series` from Task 2.
- Produces: `campaign_regimes(result, cfg, market_closes, ticker_closes) -> pd.DataFrame` — the campaign table plus `market_trend, market_vol, ticker_trend, ticker_vol` (state on each campaign's open date; campaigns opening in warmup get `"unknown"`); `autopsy_table(campaigns: pd.DataFrame, by: str = "market") -> pd.DataFrame` — grouped by `{by}_trend × {by}_vol`: `n_campaigns, win_rate, total_pnl, mean_pnl, n_rolls, n_stops... ` (closed campaigns only; open ones excluded, count exposed via `attrs["n_open_excluded"]`).

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd
import pytest
from src.engine_v2.options.wheel import WheelConfig, WheelResult, Trade
from src.engine_v2.options.chain import Contract
from src.engine_v2.regime.autopsy import campaign_regimes, autopsy_table

def _closes(trend="up"):
    # 400 bdays ending 2024-06; rising or falling from day 250 on
    idx = pd.bdate_range("2023-01-02", periods=400)
    base = pd.Series(range(400), index=idx, dtype=float)
    px = 100 + base * (0.1 if trend == "up" else -0.1)
    return px.clip(lower=5).rename("X")

def _t(date, action, cid, strike=100.0):
    return Trade(pd.Timestamp(date), action,
                 Contract("X", pd.Timestamp("2024-06-21"), strike, "P"),
                 1, 1.0, 0.0, cid)

def test_campaigns_tagged_and_grouped():
    trades = [
        _t("2024-05-01", "SELL_PUT", 1), _t("2024-05-08", "CLOSE_PUT", 1),
        _t("2024-05-09", "SELL_PUT", 2), _t("2024-05-10", "ROLL_CLOSE", 2),
        _t("2024-05-10", "ROLL_OPEN", 2), _t("2024-05-20", "PUT_EXPIRED", 2),
    ]
    res = WheelResult(pd.Series(dtype=float), trades, 0.0, 0)
    cfg = WheelConfig(commission_per_contract=0.0)
    up = _closes("up")
    tagged = campaign_regimes(res, cfg, market_closes=up, ticker_closes=up)
    assert set(tagged["market_trend"]) == {"uptrend"}
    assert set(tagged["ticker_trend"]) == {"uptrend"}
    grouped = autopsy_table(tagged, by="market")
    assert grouped.iloc[0]["n_campaigns"] == 2
    assert grouped.iloc[0]["n_rolls"] == 1

def test_open_campaign_excluded_from_win_rate():
    trades = [_t("2024-05-01", "SELL_PUT", 1)]   # never closed
    res = WheelResult(pd.Series(dtype=float), trades, 0.0, 100,
                      residual_settled=True)
    cfg = WheelConfig(commission_per_contract=0.0)
    up = _closes("up")
    tagged = campaign_regimes(res, cfg, up, up)
    grouped = autopsy_table(tagged)
    assert grouped["n_campaigns"].sum() == 0
    assert grouped.attrs["n_open_excluded"] == 1

def test_warmup_open_date_is_unknown():
    trades = [_t("2023-03-01", "SELL_PUT", 1), _t("2023-03-08", "CLOSE_PUT", 1)]
    res = WheelResult(pd.Series(dtype=float), trades, 0.0, 0)
    cfg = WheelConfig(commission_per_contract=0.0)
    up = _closes("up")
    tagged = campaign_regimes(res, cfg, up, up)
    assert tagged.iloc[0]["market_trend"] == "unknown"
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**

```python
"""Wheel-campaign autopsy by regime: the campaign ledger grouped by the market
state (and the traded ticker's state) each campaign opened into."""
from __future__ import annotations
import pandas as pd
from ..options.report import campaign_table
from .state import regime_series

def _tag(dates, states: pd.DataFrame, col: str) -> list:
    out = []
    for d in dates:
        d = pd.Timestamp(d)
        prior = states.loc[:d]
        out.append(prior.iloc[-1][col] if len(prior) else "unknown")
    return out

def campaign_regimes(result, cfg, market_closes: pd.Series,
                     ticker_closes: pd.Series) -> pd.DataFrame:
    ct = campaign_table(result, cfg)
    mkt, tkr = regime_series(market_closes), regime_series(ticker_closes)
    ct = ct.copy()
    for prefix, states in (("market", mkt), ("ticker", tkr)):
        ct[f"{prefix}_trend"] = _tag(ct["opened"], states, "trend")
        ct[f"{prefix}_vol"] = _tag(ct["opened"], states, "vol")
    return ct

def autopsy_table(campaigns: pd.DataFrame, by: str = "market") -> pd.DataFrame:
    closed = campaigns[~campaigns["open_at_end"]]
    rows = []
    for (trend, vol), g in closed.groupby([f"{by}_trend", f"{by}_vol"]):
        rows.append(dict(trend=trend, vol=vol, n_campaigns=len(g),
                         win_rate=float((g["pnl"] > 0).mean()),
                         total_pnl=float(g["pnl"].sum()),
                         mean_pnl=float(g["pnl"].mean()),
                         n_rolls=int(g["n_rolls"].sum())))
    out = pd.DataFrame(rows, columns=["trend","vol","n_campaigns","win_rate",
                                      "total_pnl","mean_pnl","n_rolls"])
    out.attrs["n_open_excluded"] = int(campaigns["open_at_end"].sum())
    return out
```

(Note: `n_stops` is intentionally absent — the campaign table doesn't carry per-campaign stop counts; stops end campaigns, so `win_rate`/`pnl` already reflect them. If per-campaign stop counts are wanted later, extend `campaign_table` first.)

- [ ] **Step 4: Run to verify pass.** **Step 5: Commit** `feat: wheel-campaign autopsy grouped by market/ticker regime`

---

### Task 5: dashboard Regime tab

**Files:**
- Create: `dashboard/views/regime.py`
- Modify: `dashboard/app.py` (add `st.Page(regime.render, title="Regime", url_path="regime")` and the import)
- Test: `tests/engine_v2/regime/test_view_smoke.py`

**Interfaces:**
- Consumes: `closes_for`, `regime_series`, `describe`, `base_rate_table`, `campaign_regimes`, `autopsy_table` — exactly as defined in Tasks 1–4.

- [ ] **Step 1: Write the failing smoke test**

```python
def test_regime_view_helpers_render_without_streamlit():
    # the view's pure helpers must work headless (streamlit only decorates)
    from dashboard.views.regime import current_state_lines, base_rate_display
    lines = current_state_lines("SPY")
    assert any("SPY" in l for l in lines)
    tbl = base_rate_display("SPY")
    assert "caveat" in tbl.attrs
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — `dashboard/views/regime.py`:

```python
"""Regime tab: today's market/ticker state, base rates, wheel autopsy.
Read-only — no knobs, no trading decisions."""
import pandas as pd
import streamlit as st
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series, describe
from src.engine_v2.regime.base_rates import base_rate_table
from src.engine_v2.regime.autopsy import campaign_regimes, autopsy_table
from src.engine_v2.options.data import available_tickers

def current_state_lines(symbol: str) -> list:
    states = regime_series(closes_for(symbol))
    row = states.iloc[-1]
    return [f"{symbol} — {describe(row)}",
            f"as of {states.index[-1].date()}"]

def base_rate_display(symbol: str) -> pd.DataFrame:
    return base_rate_table(closes_for(symbol))

def render():
    st.title("Regime")
    st.caption("Read-only advisor: where the market is, what usually followed, "
               "and how the wheel's own campaigns fared by regime.")
    tickers = [t for t in available_tickers() if t != "SPY"]
    tick = st.selectbox("Ticker", ["SPY"] + tickers, key="regime_ticker")

    st.subheader("Today")
    for sym in dict.fromkeys(["SPY", tick]):
        try:
            for line in current_state_lines(sym):
                st.write(line)
        except FileNotFoundError:
            st.warning(f"No daily closes on disk for {sym}.")

    st.subheader(f"Base rates — next 21 trading days from each state")
    for sym in dict.fromkeys(["SPY", tick]):
        try:
            tbl = base_rate_display(sym)
        except FileNotFoundError:
            continue
        st.write(f"**{sym}**")
        st.dataframe(tbl.style.format({"win_rate": "{:.0%}", "median_fwd": "{:+.2%}",
                                       "mean_fwd": "{:+.2%}", "p5_fwd": "{:+.2%}"}),
                     width="stretch")
        st.caption(tbl.attrs["caveat"])

    stash = st.session_state.get("_wheel_result")
    if stash is not None:
        res, rep, cfg, _ch = stash
        st.subheader(f"Wheel autopsy by regime — last run ({cfg.ticker})")
        try:
            tagged = campaign_regimes(res, cfg, closes_for("SPY"), closes_for(cfg.ticker))
        except FileNotFoundError:
            st.warning("Missing closes for the autopsy."); return
        c1, c2 = st.columns(2)
        for col, by, label in ((c1, "market", "by MARKET (SPY) state"),
                               (c2, "ticker", f"by {cfg.ticker} state")):
            t = autopsy_table(tagged, by=by)
            col.write(f"**{label}**")
            col.dataframe(t.style.format({"win_rate": "{:.0%}", "total_pnl": "{:+,.0f}",
                                          "mean_pnl": "{:+,.0f}"}), width="stretch")
            if t.attrs["n_open_excluded"]:
                col.caption(f"{t.attrs['n_open_excluded']} open campaign(s) excluded.")
    else:
        st.info("Run a wheel backtest first to see the campaign autopsy here.")
```

`dashboard/app.py`: add `regime` to the views import and `st.Page(regime.render, title="Regime", url_path="regime")` to the page list.

- [ ] **Step 4: Run smoke + full dashboard tests.** **Step 5: Commit** `feat: Regime dashboard tab (state, base rates, autopsy)`

---

### Task 6: gates, docs, merge

- [ ] Full suite: `.venv/bin/python -m pytest tests/ -q` → 0 failures.
- [ ] Coverage: `.venv/bin/python -m pytest tests/engine_v2/ --cov=src/engine_v2 --cov-fail-under=85 -q` → pass.
- [ ] Real-data sanity: print today's SPY + GDX describe() strings and both autopsy tables for a roll-tested GDX run; eyeball that labels are sane.
- [ ] Vault: STATUS + daily log updated (advisor shipped, what it shows).
- [ ] Merge `regime-advisor` → main, plain-regression test green on main.

## Self-review notes (resolved inline)

- Spec coverage: state ✓ (Task 2), base rates + caveat + thin ✓ (Task 3), autopsy market+ticker ✓ (Task 4), dashboard ✓ (Task 5), data-from-disk ✓ (Task 1), no-look-ahead property ✓ (Task 2 test), warmup ✓, out-of-scope respected (no wiring anywhere).
- Type consistency: `regime_series -> DataFrame` consumed identically in Tasks 3/4/5; `campaign_table` columns (`opened, pnl, n_rolls, open_at_end`) match the shipped report.py.
- Spec's `RegimeState` dataclass replaced by DataFrame rows (one structure instead of two — `describe(row)` takes a row); noted here as a deliberate simplification, spec-compliant in content.
