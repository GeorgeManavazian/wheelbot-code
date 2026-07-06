# Iteration Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the six iteration candidates from the first-screening verdict (vault note `07 ETF Bot/Backtests/2026-07-05 — First screening`) as new strategy configs/plugins plus benchmark rows, then a second screening script — playground only.

**Architecture:** Everything plugs into the existing engine untouched (strategy-as-plugin: `target_weights(window) -> {ticker: weight} | None`). New params on existing strategies default to OLD behavior so the 31 already-tested configs stay reproducible. New strategies are new files. The batch runner gains one optional `extra` dict so leaderboard rows can carry a `universe` tag.

**Tech Stack:** Python 3.12, pandas, numpy, scipy, pytest. Repo: `~/Documents/Trading code/etf-bot`, branch `iteration-2` off `main` (@5803107 + this plan).

## Global Constraints

- NEVER touch `data/` — playground/exam parquets are frozen with SHA256 manifests. No new data pulls; the equity-heavy universe is a SUBSET of the existing 20 tickers.
- NEVER load exam data (`load_exam`). All screening is playground 2010–2020.
- New params on existing strategies MUST default to prior behavior (first-screen configs must be re-runnable bit-identical).
- Every concrete `Strategy` subclass needs a `description` >100 chars containing the literal sections "What it does", "Why it should work", "When it fails" (enforced by `tests/test_descriptions.py`).
- Long-only, no leverage: weights ≥ 0, sum ≤ 1.0 (engine hard-errors otherwise).
- Strategies are pure functions of the closes window — no state between calls, no engine access.
- Run `python -m pytest tests/ -q` (full suite, currently 53 green) at the end of every task, not just the new tests.
- Commit after every task; message style: `feat:`/`fix:` one-liner, no attribution footers (match repo history).

---

### Task 1: Branch + buy-reduction warning threshold

The fill code warns on EVERY whole-share buy reduction; 31-run screens spam hundreds of cosmetic lines. Only reductions >0.1% of the intended shares are worth a warning.

**Files:**
- Modify: `src/engine/fills.py:52-55`
- Test: `tests/test_fills.py` (append)

**Interfaces:**
- Consumes: existing `execute_rebalance(positions, cash, targets, open_prices, slippage_bps)`
- Produces: same signature, unchanged accounting — only the print condition changes.

- [ ] **Step 1: Create branch**

```bash
cd ~/Documents/Trading\ code/etf-bot && git checkout main && git checkout -b iteration-2
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_fills.py` (it already imports `execute_rebalance` and `pd`):

```python
def test_tiny_buy_reduction_does_not_warn(capsys):
    # target 10_000 shares, cash covers 9_999 -> 0.01% reduction, no warning
    prices = pd.Series({"A": 1.0})
    positions, cash, trades = execute_rebalance(
        {}, 9_999.0, {"A": 1.0}, prices, slippage_bps=0)
    assert positions == {"A": 9_999}
    assert "WARNING" not in capsys.readouterr().out


def test_large_buy_reduction_still_warns(capsys):
    # engine invariant makes big reductions rare (weights<=1), but a stale
    # position can produce one: hold B, cash tiny, target 100% A.
    prices = pd.Series({"A": 100.0, "B": 0.01})
    # value = 10 + 100*0.01 = 11 -> target 0 shares of A? No: use cheap A.
    prices = pd.Series({"A": 1.0, "B": 100.0})
    # hold 1 B (100), cash 0 -> value 100, target 100 A shares.
    # Sell of B happens first and frees cash, so craft NO sell: keep B in targets.
    positions, cash, trades = execute_rebalance(
        {"B": 1}, 0.0, {"A": 0.5, "B": 0.5}, prices, slippage_bps=0)
    # target A = floor(0.5*100/1) = 50 shares, cash freed by selling 0 B... B target
    # = floor(0.5*100/100) = 0 -> B sold, frees 100 -> no reduction. Simplify:
    assert True  # replaced below
```

That construction is fiddly — the honest trigger is cash-capped buys with no sells. REPLACE the second test with this direct one:

```python
def test_large_buy_reduction_still_warns(capsys):
    # no positions, weights sum to 1.0, but slippage makes the buy cost more
    # than floor allows: target floor(1.0*100/ (1*1.05)) = 95 at slipped price,
    # affordable floor(100/1.05) = 95 -> equal, no reduction. Force it with two
    # buys: A consumes cash first (sorted order), B gets starved.
    prices = pd.Series({"A": 1.0, "B": 1.0})
    positions, cash, trades = execute_rebalance(
        {}, 100.0, {"A": 0.99, "B": 0.01}, prices, slippage_bps=5000)
    # slip=0.5: A target floor(99/1.5)=66 costing 99 -> cash left 1.0
    # B target floor(1/1.5)=0 -> no trade. Still no starvation. So: weights
    # {"A": 1.0} with a pre-held B position that isn't sold (B in targets too).
    out = capsys.readouterr().out
    assert True  # replaced below
```

**STOP — implementer note.** Constructing a >0.1% starved buy through the public API requires a stale-position setup. Use this exact scenario, verified against the accounting: hold 50 shares of B at price 2.0 (value 100), cash 0, targets `{"A": 0.5, "B": 0.5}`, prices A=1.0, B=2.0, no slippage. Sells run first: B target = floor(0.5·100/2)=25, sell 25 → cash 50. Buys: A target = floor(0.5·100/1)=50, affordable = floor(50/1)=50 → still no starvation (the engine's sell-first design makes real starvation nearly impossible with weights ≤ 1). **Therefore test the threshold logic directly instead** — final tests to commit:

```python
def test_tiny_buy_reduction_does_not_warn(capsys):
    # cash 9_999 vs 10_000-share intent: 0.01% reduction -> silent
    prices = pd.Series({"A": 1.0})
    positions, cash, trades = execute_rebalance(
        {}, 9_999.0, {"A": 1.0}, prices, slippage_bps=0)
    assert positions == {"A": 9_999}
    assert "WARNING" not in capsys.readouterr().out


def test_buy_reduction_above_threshold_warns(monkeypatch, capsys):
    # force starvation by shrinking cash between sell and buy legs is not
    # possible through the API; instead simulate the 1-share case that
    # motivated the fix (screen log spam): intent 3 shares, afford 2 (33%).
    prices = pd.Series({"A": 3.0})
    positions, cash, trades = execute_rebalance(
        {}, 8.0, {"A": 1.0}, prices, slippage_bps=0)
    # value=8 -> target floor(8/3)=2, affordable floor(8/3)=2 -> equal again.
    # Public-API starvation truly cannot occur without slippage asymmetry:
    # slipped buy target uses (1+slip) price, affordable uses same -> equal.
    # The ONLY real trigger is float dust (cash 5.999999 vs price 2).
    positions, cash, trades = execute_rebalance(
        {}, 5.999999, {"A": 1.0}, prices, slippage_bps=0)
    out = capsys.readouterr().out
    assert "WARNING" not in out  # 5.999999/3 -> intent 1, afford 1
```

If after 15 minutes you cannot construct a legitimate >0.1% starvation through `execute_rebalance`, that is itself the finding: keep `test_tiny_buy_reduction_does_not_warn`, drop the second test, and note in the commit body that the warning path is defensive-only. Do NOT test private helpers or monkeypatch internals.

- [ ] **Step 3: Run tests to verify the tiny-reduction one fails**

Run: `python -m pytest tests/test_fills.py -q`
Expected: `test_tiny_buy_reduction_does_not_warn` FAILS (warning currently printed on any reduction).

- [ ] **Step 4: Implement threshold**

In `src/engine/fills.py`, replace lines 52–55:

```python
            affordable = math.floor(cash / price)
            if affordable < diff:
                if (diff - affordable) > 0.001 * diff:
                    print(f"WARNING: {t} buy reduced {diff} -> {affordable} (cash)")
                diff = affordable
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (53 + new).

- [ ] **Step 6: Commit**

```bash
git add src/engine/fills.py tests/test_fills.py
git commit -m "fix: only warn on buy reductions >0.1% of intent (kills screen log spam)"
```

---

### Task 2: `extra` columns in run_batch

Second screening runs two universes; leaderboard rows need a `universe` tag or the registries can't tell them apart.

**Files:**
- Modify: `src/batch/runner.py:28-51`
- Test: `tests/test_batch.py` (append)

**Interfaces:**
- Consumes: existing `run_batch(strategies, long_df, config=None, out_csv=None)`
- Produces: `run_batch(strategies, long_df, config=None, out_csv=None, extra=None)` — `extra: dict[str, str] | None`; each key becomes a column (positioned after `name`, before param cols) with that constant value in every row, in-memory AND in the streamed CSV.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_batch.py` (reuse its existing `make_long` helper and `TSTrend` import):

```python
def test_extra_columns_appear_in_every_row_and_csv(tmp_path):
    out = tmp_path / "lb.csv"
    lb = run_batch([TSTrend(lookback=20)], make_long(),
                   out_csv=out, extra={"universe": "equity_heavy"})
    assert (lb["universe"] == "equity_heavy").all()
    # column order: label, name, universe, then params
    cols = list(lb.columns)
    assert cols.index("universe") == cols.index("name") + 1
    streamed = pd.read_csv(out)
    assert (streamed["universe"] == "equity_heavy").all()


def test_no_extra_keeps_old_columns():
    lb = run_batch([TSTrend(lookback=20)], make_long())
    assert "universe" not in lb.columns
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_batch.py -q`
Expected: FAIL — `run_batch() got an unexpected keyword argument 'extra'`.

- [ ] **Step 3: Implement**

In `src/batch/runner.py`, change the `run_batch` signature and first lines:

```python
def run_batch(strategies: list, long_df: pd.DataFrame, config=None,
              out_csv=None, extra: dict | None = None) -> pd.DataFrame:
    extra = extra or {}
    param_cols = sorted({k for s in strategies for k in s.params})
    columns = ["label", "name", *extra.keys(), *param_cols, *METRIC_COLS, "error"]
    rows = []
    for i, strat in enumerate(strategies, 1):
        row = {"label": strat.label(), "name": strat.name, **extra,
               **strat.params, "error": ""}
```

(Everything else in the function is unchanged — the streamed-CSV `reindex(columns=columns)` picks the new columns up automatically.)

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/batch/runner.py tests/test_batch.py
git commit -m "feat: optional extra tag columns in run_batch (universe tagging)"
```

---

### Task 3: Benchmark strategies (buy-hold SPY, 60/40)

First-screen lesson: leaderboards without a passive benchmark row invite self-deception. These are ordinary plugins so they appear as rows with full honesty stats.

**Files:**
- Create: `src/strategies/benchmarks.py`
- Test: `tests/test_benchmarks.py` (new)
- Modify: `tests/test_descriptions.py:3-4` (add import so subclass discovery sees the new classes)

**Interfaces:**
- Consumes: `Strategy` base (`is_month_start`, `DEFAULTS`, param validation).
- Produces: `BuyHold` (name `buy_hold`, DEFAULTS `{"ticker": "SPY"}`) and `SixtyForty` (name `sixty_forty`, DEFAULTS `{"equity": "SPY", "bond": "TLT"}`). Task 8 imports both from `src.strategies.benchmarks`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_benchmarks.py`:

```python
import numpy as np
import pandas as pd

from src.strategies.benchmarks import BuyHold, SixtyForty


def make_window(n_days, cols, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame({c: np.linspace(100, 120, n_days) for c in cols},
                        index=idx)


def month_start_window(n_days, cols):
    w = make_window(n_days, cols)
    months = w.index.month
    for i in range(1, len(w)):
        if months[i] != months[i - 1]:
            return w.iloc[: i + 1]
    raise AssertionError("no month boundary")


def test_buy_hold_all_in_on_month_start():
    w = month_start_window(40, ["SPY", "TLT"])
    assert BuyHold().target_weights(w) == {"SPY": 1.0}


def test_buy_hold_holds_mid_month():
    w = make_window(40, ["SPY", "TLT"])
    assert w.index[-1].month == w.index[-2].month
    assert BuyHold().target_weights(w) is None


def test_sixty_forty_split():
    w = month_start_window(40, ["SPY", "TLT"])
    assert SixtyForty().target_weights(w) == {"SPY": 0.6, "TLT": 0.4}


def test_configurable_tickers():
    w = month_start_window(40, ["QQQ", "IEF"])
    assert BuyHold(ticker="QQQ").target_weights(w) == {"QQQ": 1.0}
    assert SixtyForty(equity="QQQ", bond="IEF").target_weights(w) == \
        {"QQQ": 0.6, "IEF": 0.4}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_benchmarks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.strategies.benchmarks'`.

- [ ] **Step 3: Implement**

Create `src/strategies/benchmarks.py`:

```python
"""Passive benchmarks as ordinary strategy plugins, so every leaderboard
carries the 'did you even beat doing nothing?' rows with full honesty stats.

Monthly re-statement of constant weights = buy-and-hold plus a monthly
whole-share drift top-up (turnover ~0). First investment happens at the
first month boundary in the data (is_month_start needs 2 bars) — identical
warm-up treatment to every other strategy, so rows are comparable.
"""
from src.strategies.base import Strategy


class BuyHold(Strategy):
    name = "buy_hold"
    DEFAULTS = {"ticker": "SPY"}
    description = """\
**What it does:** Puts 100% of the account into one ETF (default SPY) and
holds it, restating the target monthly so whole-share drift gets topped up.

**Why it should work:** It's the market. Equities carry a long-run risk
premium, and most active daily-bar strategies fail to beat simply owning
the index after costs. This row is the bar every other row must clear.

**When it fails:** Bear markets — it rides every drawdown to the bottom
(-55% in 2008-style events). No brake, no cash, no risk management.
"""

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        return {self.params["ticker"]: 1.0}


class SixtyForty(Strategy):
    name = "sixty_forty"
    DEFAULTS = {"equity": "SPY", "bond": "TLT"}
    description = """\
**What it does:** Classic balanced portfolio: 60% equity ETF, 40% long
bond ETF, rebalanced back to those weights monthly.

**Why it should work:** Stocks and long Treasuries were negatively
correlated for most of 2000-2020, so the bond sleeve cushions equity
drawdowns while capturing most of the equity premium. The standard
institutional default portfolio.

**When it fails:** Inflationary regimes where stocks and bonds fall
together (2022). Also lags pure equity badly in strong bull runs.
"""

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        return {self.params["equity"]: 0.6, self.params["bond"]: 0.4}
```

- [ ] **Step 4: Register in the description test**

In `tests/test_descriptions.py`, after the existing strategy imports add:

```python
from src.strategies.benchmarks import BuyHold, SixtyForty  # noqa: F401
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass — including `test_every_concrete_strategy_has_full_description` now covering 4 classes.

- [ ] **Step 6: Commit**

```bash
git add src/strategies/benchmarks.py tests/test_benchmarks.py tests/test_descriptions.py
git commit -m "feat: buy-hold and 60/40 benchmark strategies for leaderboard context"
```

---

### Task 4: Inverse-vol sizing for TSTrend

Iteration candidate #1. Clenow ch 16 actually sizes by volatility; our v1 used fixed 1/N. New `sizing` param defaults to `"fixed"` (old behavior, bit-identical). `"inv_vol"` distributes the SAME breadth-scaled total exposure (`n_qualified/N_universe`) by inverse volatility — this isolates the sizing change from any exposure change, so the comparison against the first-screen rows is clean.

**Files:**
- Modify: `src/strategies/ts_trend.py`
- Test: `tests/test_ts_trend.py` (append)

**Interfaces:**
- Consumes: `Strategy` base.
- Produces: `TSTrend` with `DEFAULTS = {"lookback": 125, "sizing": "fixed", "vol_window": 20}`. `sizing` ∈ {"fixed", "inv_vol"} — anything else raises `ValueError` at construction. Task 8 constructs `TSTrend(lookback=..., sizing="inv_vol", vol_window=20)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ts_trend.py` (reuse its `make_window` / `_first_month_boundary` helpers):

```python
def test_fixed_sizing_unchanged_by_default():
    # regression pin: default params must reproduce first-screen behavior
    w = _first_month_boundary(make_window(60, ["A"], ["B"]))
    assert TSTrend(lookback=20).target_weights(w) == {"A": 0.5}


def test_inv_vol_total_exposure_equals_breadth():
    w = _first_month_boundary(make_window(80, ["A", "B"], ["C"]))
    weights = TSTrend(lookback=20, sizing="inv_vol").target_weights(w)
    assert set(weights) == {"A", "B"}
    assert abs(sum(weights.values()) - 2 / 3) < 1e-9  # 2 of 3 qualify


def test_inv_vol_gives_calmer_asset_more_weight():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range("2020-01-01", periods=80)
    rng = np.random.default_rng(7)
    calm = 100 * np.cumprod(1 + rng.normal(0.001, 0.001, 80))
    wild = 100 * np.cumprod(1 + rng.normal(0.001, 0.03, 80))
    w = pd.DataFrame({"CALM": calm, "WILD": wild}, index=idx)
    w = _first_month_boundary(w)
    # both must qualify (rising); rng with drift makes that overwhelmingly
    # likely, but assert it so a bad seed fails loudly not silently
    weights = TSTrend(lookback=20, sizing="inv_vol").target_weights(w)
    assert set(weights) == {"CALM", "WILD"}
    assert weights["CALM"] > weights["WILD"]


def test_unknown_sizing_rejected():
    import pytest
    with pytest.raises(ValueError, match="sizing"):
        TSTrend(sizing="equal_risk")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_ts_trend.py -q`
Expected: new tests FAIL (`unknown params {'sizing'}` from base validation).

- [ ] **Step 3: Implement**

Replace `src/strategies/ts_trend.py` class body (keep module docstring, update it to mention both sizings; keep the existing `description` text and append one sentence to the "What it does" paragraph: `With sizing="inv_vol", the same total exposure is split by inverse volatility — calmer holdings get more.`):

```python
class TSTrend(Strategy):
    name = "ts_trend"
    DEFAULTS = {"lookback": 125, "sizing": "fixed", "vol_window": 20}
    description = ...  # existing text + the one added sentence

    def __init__(self, **params):
        super().__init__(**params)
        if self.params["sizing"] not in ("fixed", "inv_vol"):
            raise ValueError(f"{self.name}: sizing must be 'fixed' or "
                             f"'inv_vol', got {self.params['sizing']!r}")

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None
        today = window.iloc[-1]
        past = window.iloc[-1 - look]
        qualified = [t for t in window.columns if today[t] > past[t]]
        if not qualified:
            return {}
        total = len(qualified) / len(window.columns)
        if self.params["sizing"] == "fixed":
            w = 1.0 / len(window.columns)
            return {t: w for t in qualified}
        vol = (window[qualified].pct_change()
               .iloc[-self.params["vol_window"]:].std())
        vol = vol.clip(lower=1e-9)  # flat 20d price would give inf weight
        inv = 1.0 / vol
        return (inv / inv.sum() * total).to_dict()
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass. NOTE: `TSTrend.label()` now includes `sizing=` and `vol_window=` — no test pins the old label string (verified), but if one fails on label content, update the expectation deliberately and say so in the commit.

- [ ] **Step 5: Commit**

```bash
git add src/strategies/ts_trend.py tests/test_ts_trend.py
git commit -m "feat: inverse-vol sizing option for ts_trend (Clenow's actual sizing)"
```

---

### Task 5: 200d index trend filter for MomentumRotation

Iteration candidate #2. Clenow ch 12's contested filter: only be in the market when the index (SPY) is above its 200d SMA. His version blocks NEW entries but keeps existing positions; our strategies are stateless pure functions (no access to current positions), so the honest stateless variant is: index below SMA at rebalance → all cash. Flagged in the vault as possibly curve-fit on two known bears — we test it, we don't assume it.

**Files:**
- Modify: `src/strategies/momentum_rotation.py`
- Test: `tests/test_momentum_rotation.py` (append)

**Interfaces:**
- Consumes: `Strategy` base, existing `momentum_score`.
- Produces: `MomentumRotation` with `DEFAULTS = {"lookback": 125, "top_n": 3, "vol_window": 20, "min_score": 0.0, "index_filter": 0, "filter_ticker": "SPY"}`. `index_filter=0` (default) = filter off = old behavior. `index_filter=N>0` = need `filter_ticker` close > N-day SMA, else `{}`. Warm-up: return `None` until `len(window) > max(lookback, index_filter)`. Task 8 constructs `MomentumRotation(..., index_filter=200)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_momentum_rotation.py` (check its existing helpers first and reuse them; if it has no month-boundary helper, copy `_first_month_boundary` from `tests/test_ts_trend.py`):

```python
import numpy as np
import pandas as pd


def _window_with_spy(n_days, spy_path, other_cols, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    data = {"SPY": spy_path}
    for c in other_cols:
        data[c] = np.linspace(100, 200, n_days)  # strong rising trend
    return pd.DataFrame(data, index=idx)


def _at_month_boundary(w):
    months = w.index.month
    for i in range(len(w) - 1, 0, -1):
        if months[i] != months[i - 1]:
            return w.iloc[: i + 1]
    raise AssertionError("no month boundary")


def test_filter_off_by_default_is_old_behavior():
    w = _at_month_boundary(_window_with_spy(
        260, np.linspace(200, 100, 260), ["A", "B"]))
    weights = MomentumRotation(lookback=125, top_n=2).target_weights(w)
    assert weights  # SPY crashing is irrelevant when filter off


def test_below_sma_goes_to_cash():
    # SPY falling all year -> last close < 200d SMA -> cash despite A,B rising
    w = _at_month_boundary(_window_with_spy(
        260, np.linspace(200, 100, 260), ["A", "B"]))
    weights = MomentumRotation(lookback=125, top_n=2,
                               index_filter=200).target_weights(w)
    assert weights == {}


def test_above_sma_trades_normally():
    w = _at_month_boundary(_window_with_spy(
        260, np.linspace(100, 200, 260), ["A", "B"]))
    weights = MomentumRotation(lookback=125, top_n=2,
                               index_filter=200).target_weights(w)
    assert weights and set(weights) <= {"SPY", "A", "B"}


def test_warmup_covers_filter_window():
    # 150 bars < 200d filter -> None even though lookback 125 is satisfied
    w = _at_month_boundary(_window_with_spy(
        150, np.linspace(100, 150, 150), ["A"]))
    assert MomentumRotation(lookback=125, top_n=2,
                            index_filter=200).target_weights(w) is None


def test_missing_filter_ticker_raises():
    idx = pd.bdate_range("2019-01-01", periods=260)
    w = pd.DataFrame({"A": np.linspace(100, 200, 260)}, index=idx)
    w = _at_month_boundary(w)
    import pytest
    with pytest.raises(KeyError, match="SPY"):
        MomentumRotation(lookback=125, top_n=1,
                         index_filter=200).target_weights(w)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_momentum_rotation.py -q`
Expected: new tests FAIL (`unknown params {'index_filter', ...}`).

- [ ] **Step 3: Implement**

In `src/strategies/momentum_rotation.py`:

1. `DEFAULTS = {"lookback": 125, "top_n": 3, "vol_window": 20, "min_score": 0.0, "index_filter": 0, "filter_ticker": "SPY"}`
2. Append to the module docstring: `index_filter=N (0=off): stateless adaptation of Clenow's index trend filter — filter_ticker close below its N-day SMA at rebalance -> all cash. Clenow's stateful version only blocks NEW entries; flagged as possible curve-fit in ch 12, test honestly.`
3. Append one sentence to the description's "What it does" paragraph: `With index_filter=200, it additionally goes to cash whenever SPY sits below its 200-day average — a regime brake.`
4. Rewrite `target_weights`:

```python
    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        flt = self.params["index_filter"]
        if len(window) <= max(look, flt):
            return None

        if flt > 0:
            ft = self.params["filter_ticker"]
            if ft not in window.columns:
                raise KeyError(f"filter_ticker {ft!r} not in universe window")
            spy = window[ft]
            if spy.iloc[-1] < spy.iloc[-flt:].mean():
                return {}

        recent = window.iloc[-look:]
        scores = recent.apply(momentum_score)
        top = scores.nlargest(self.params["top_n"])
        top = top[top > self.params["min_score"]]
        if top.empty:
            return {}

        vol = (window[top.index].pct_change()
               .iloc[-self.params["vol_window"]:].std())
        vol = vol.clip(lower=1e-9)  # a 20d flat price would give inf weight
        inv = 1.0 / vol
        weights = inv / inv.sum()
        return weights.to_dict()
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (same label-change caveat as Task 4).

- [ ] **Step 5: Commit**

```bash
git add src/strategies/momentum_rotation.py tests/test_momentum_rotation.py
git commit -m "feat: optional 200d index trend filter for momentum rotation"
```

---

### Task 6: Dual Momentum strategy

Iteration candidate #3 (Antonacci-style, adapted to the fixed basket). Relative momentum picks the `top_n` by simple total return over `lookback`; absolute momentum then drops any pick whose return is ≤ 0 — those slots sit in cash. Equal weight `1/top_n` per surviving pick (dropped slots = cash brake, same breadth idea as ts_trend). Simple return, not regression score — deliberately a DIFFERENT signal family from MomentumRotation.

**Files:**
- Create: `src/strategies/dual_momentum.py`
- Test: `tests/test_dual_momentum.py` (new)
- Modify: `tests/test_descriptions.py` (add import)

**Interfaces:**
- Consumes: `Strategy` base.
- Produces: `DualMomentum` (name `dual_momentum`, `DEFAULTS = {"lookback": 252, "top_n": 3}`). Task 8 imports it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dual_momentum.py`:

```python
import numpy as np
import pandas as pd
import pytest

from src.strategies.dual_momentum import DualMomentum


def make_window(n_days, paths: dict, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    return pd.DataFrame(
        {c: np.linspace(100, end, n_days) for c, end in paths.items()},
        index=idx)


def month_boundary(w):
    months = w.index.month
    for i in range(len(w) - 1, 0, -1):
        if months[i] != months[i - 1]:
            return w.iloc[: i + 1]
    raise AssertionError("no month boundary")


def test_picks_top_n_by_return_equal_weight():
    w = month_boundary(make_window(
        60, {"BEST": 180, "GOOD": 150, "MEH": 110, "BAD": 90}))
    weights = DualMomentum(lookback=40, top_n=2).target_weights(w)
    assert weights == {"BEST": 0.5, "GOOD": 0.5}


def test_absolute_filter_drops_negative_winners():
    # top 2 by RELATIVE momentum are MEH (+10%) and BAD (-10%); absolute
    # filter keeps only MEH -> one 1/top_n slot invested, rest cash
    w = month_boundary(make_window(
        60, {"MEH": 110, "BAD": 90, "AWFUL": 60, "DIRE": 50}))
    weights = DualMomentum(lookback=40, top_n=2).target_weights(w)
    assert weights == {"MEH": 0.5}


def test_all_negative_means_all_cash():
    w = month_boundary(make_window(60, {"A": 90, "B": 80, "C": 70}))
    assert DualMomentum(lookback=40, top_n=2).target_weights(w) == {}


def test_none_mid_month_and_short_history():
    w = make_window(60, {"A": 120, "B": 110})
    assert w.index[-1].month == w.index[-2].month
    assert DualMomentum(lookback=40, top_n=2).target_weights(w) is None
    short = month_boundary(make_window(60, {"A": 120}))
    assert DualMomentum(lookback=500, top_n=1).target_weights(short) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_dual_momentum.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/strategies/dual_momentum.py`:

```python
"""Dual momentum (Antonacci, adapted to the fixed ETF basket).

Monthly: rank the basket by simple total return over `lookback` bars
(relative momentum), take the top_n, then drop any pick whose return is
<= 0 (absolute momentum) — those slots stay in cash. Equal weight 1/top_n
per surviving pick. Deliberately a different signal family from
momentum_rotation's regression-slope score: simple return, no smoothness
term, plus the absolute-return gate.
"""
from src.strategies.base import Strategy


class DualMomentum(Strategy):
    name = "dual_momentum"
    DEFAULTS = {"lookback": 252, "top_n": 3}
    description = """\
**What it does:** Once a month, ranks every ETF by its plain return over
the past `lookback` days and takes the `top_n` winners — but only keeps
the ones whose return is actually positive. Winners that are merely
"least bad" get replaced with cash. Each kept winner gets an equal slice.

**Why it should work:** Combines the two best-documented momentum
effects: relative momentum (winners keep winning vs peers) and absolute
momentum (assets above their own past level keep drifting up). The
absolute gate is the crash protection — in broad bear markets nothing
passes and the book goes to cash instead of rotating into the
least-terrible asset.

**When it fails:** Sideways chop (whipsaw in and out of cash), sharp
V-recoveries (still in cash while the market rips back), and any regime
where last year's winner mean-reverts hard right after you buy it.
"""

    def target_weights(self, window):
        if not self.is_month_start(window):
            return None
        look = self.params["lookback"]
        if len(window) <= look:
            return None
        returns = window.iloc[-1] / window.iloc[-1 - look] - 1.0
        top = returns.nlargest(self.params["top_n"])
        top = top[top > 0]
        if top.empty:
            return {}
        w = 1.0 / self.params["top_n"]
        return {t: w for t in top.index}
```

- [ ] **Step 4: Register in the description test**

In `tests/test_descriptions.py`, add:

```python
from src.strategies.dual_momentum import DualMomentum  # noqa: F401
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/strategies/dual_momentum.py tests/test_dual_momentum.py tests/test_descriptions.py
git commit -m "feat: dual momentum strategy (relative + absolute, Antonacci)"
```

---

### Task 7: Equity-heavy universe subset + filter helper

Iteration candidate #5. The 20-ticker basket is bond/commodity-heavy for a 2010–2020 bull; this variant re-tests the best families on a 14-ticker equity-tilted SUBSET of the SAME frozen data. No new data, no manifest changes — `filter_universe` just slices rows.

**Files:**
- Modify: `src/engine/universe.py`
- Modify: `src/engine/data.py` (append function)
- Test: `tests/test_universe.py` and `tests/test_data.py` (append)

**Interfaces:**
- Consumes: frozen playground long-format DataFrame (columns: date, ticker, open, high, low, close, volume).
- Produces: `universe.TICKERS_EQUITY_HEAVY: list[str]` (14 tickers, strict subset of `TICKERS`) and `data.filter_universe(long_df, tickers) -> pd.DataFrame`. Task 8 uses both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universe.py`:

```python
def test_equity_heavy_is_strict_subset_of_frozen_universe():
    from src.engine.universe import TICKERS, TICKERS_EQUITY_HEAVY
    assert set(TICKERS_EQUITY_HEAVY) < set(TICKERS)
    assert len(TICKERS_EQUITY_HEAVY) == 14
    # equity-tilted: the pure-bond/credit and commodity line items are out
    assert {"IEF", "SHY", "LQD", "HYG", "SLV", "DBC"}.isdisjoint(
        TICKERS_EQUITY_HEAVY)
```

Append to `tests/test_data.py` (reuse whatever long-frame fixture/helper it already has; if none fits, build inline):

```python
def test_filter_universe_slices_and_validates():
    import pandas as pd
    from src.engine.data import filter_universe
    df = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-02"] * 3),
        "ticker": ["SPY", "TLT", "GLD"],
        "open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3,
        "close": [1.0] * 3, "volume": [1] * 3})
    out = filter_universe(df, ["SPY", "GLD"])
    assert set(out["ticker"]) == {"SPY", "GLD"}
    import pytest
    with pytest.raises(ValueError, match="QQQ"):
        filter_universe(df, ["SPY", "QQQ"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_universe.py tests/test_data.py -q`
Expected: FAIL — `ImportError` on both new names.

- [ ] **Step 3: Implement**

Append to `src/engine/universe.py`:

```python
# Iteration-2 variant: equity-tilted subset of the SAME frozen data (the
# core basket is bond/commodity-heavy for an equity bull decade). Strict
# subset — no new tickers, no data pull, manifests untouched.
TICKERS_EQUITY_HEAVY = [
    "SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ",   # broad equity + REIT
    "XLE", "XLF", "XLK", "XLV", "XLU", "XLI",   # sectors
    "TLT", "GLD",                                # two diversifiers kept
]
```

Append to `src/engine/data.py`:

```python
def filter_universe(long_df: pd.DataFrame, tickers: list) -> pd.DataFrame:
    """Row-slice a frozen long frame to a ticker subset. Frozen data is
    never modified — this is the only sanctioned way to run a universe
    variant without a new freeze."""
    missing = sorted(set(tickers) - set(long_df["ticker"].unique()))
    if missing:
        raise ValueError(f"tickers not in frozen data: {missing}")
    return long_df[long_df["ticker"].isin(tickers)].copy()
```

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/engine/universe.py src/engine/data.py tests/test_universe.py tests/test_data.py
git commit -m "feat: equity-heavy universe subset + filter_universe helper"
```

---

### Task 8: Second screening script

Ties everything together: 20 NEW configs (never re-tests the first 31 — anti-duplicate rule), benchmark rows in both universes' context, universe tagging, and a CUMULATIVE luck line (51 total playground tries to date, not just this batch's 20 — the honest number).

**Files:**
- Create: `scripts/screen2.py`
- Test: `tests/test_screen2.py` (new)

**Interfaces:**
- Consumes: everything above — `run_batch(extra=...)`, `BuyHold`, `SixtyForty`, `TSTrend(sizing=...)`, `MomentumRotation(index_filter=...)`, `DualMomentum`, `TICKERS_EQUITY_HEAVY`, `filter_universe`, plus existing `load_playground`, `expand_grid`, `luck_warning`, `plateau_table`.
- Produces: `results/leaderboard2_<stamp>_<sha>.csv` with a `universe` column; grid constants `CORE_GRIDS`, `EQUITY_GRIDS`, `PRIOR_RUNS = 31` importable for the test.

- [ ] **Step 1: Write the failing test**

Create `tests/test_screen2.py`:

```python
"""Guards the screening manifest: exactly the planned 20 NEW configs,
none colliding with the 31 first-screen registry rows."""
from scripts.screen2 import CORE_GRIDS, EQUITY_GRIDS, PRIOR_RUNS
from src.batch.runner import expand_grid


def _labels(grids):
    return [s.label() for cls, grid in grids for s in expand_grid(cls, grid)]


def test_planned_run_counts():
    core, equity = _labels(CORE_GRIDS), _labels(EQUITY_GRIDS)
    assert len(core) == 16   # 2 benchmarks + 4 ts inv_vol + 4 mom filtered + 6 dual
    assert len(equity) == 4  # 2 ts sizings + 2 mom lookbacks
    assert len(set(core)) == 16 and len(set(equity)) == 4
    assert PRIOR_RUNS == 31


def test_no_config_repeats_first_screen():
    # First screen tested: ts_trend fixed sizing (label had no sizing param
    # then; any sizing=fixed row would re-test it) and momentum with
    # index_filter absent (any index_filter=0 row in CORE would re-test).
    for label in _labels(CORE_GRIDS):
        assert "sizing=fixed" not in label
        if label.startswith("momentum_rotation"):
            assert "index_filter=200" in label
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_screen2.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.screen2'`. (If `scripts/` lacks `__init__.py` and the import fails for that reason even after creating the file, check how existing tests import — if none import from scripts, add `scripts/__init__.py` as an empty file in Step 3.)

- [ ] **Step 3: Implement**

Create `scripts/screen2.py`:

```python
"""Second screening session (iteration round 2): NEW configs only, on
PLAYGROUND data. Never re-tests the 31 first-screen configs (registry rule).

Core universe (20 ETFs): benchmarks + inverse-vol ts_trend + 200d-filtered
momentum + dual momentum. Equity-heavy universe (14 ETFs): best first-screen
families re-tested on an equity-tilted subset of the same frozen data.

Luck line is CUMULATIVE (31 prior + 20 now = 51 tries on this decade) —
per-batch luck lines understate the selection effect.
"""
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.batch.runner import expand_grid, luck_warning, plateau_table, run_batch
from src.engine.data import filter_universe, load_playground
from src.engine.universe import TICKERS_EQUITY_HEAVY
from src.strategies.benchmarks import BuyHold, SixtyForty
from src.strategies.dual_momentum import DualMomentum
from src.strategies.momentum_rotation import MomentumRotation
from src.strategies.ts_trend import TSTrend

PRIOR_RUNS = 31  # first screen, 2026-07-05 — cumulative luck-line input

CORE_GRIDS = [
    (BuyHold, {"ticker": ["SPY"]}),
    (SixtyForty, {"equity": ["SPY"], "bond": ["TLT"]}),
    (TSTrend, {"lookback": [63, 125, 189, 252], "sizing": ["inv_vol"],
               "vol_window": [20]}),
    (MomentumRotation, {"lookback": [63, 125], "top_n": [3, 5],
                        "vol_window": [20], "min_score": [0.0],
                        "index_filter": [200], "filter_ticker": ["SPY"]}),
    (DualMomentum, {"lookback": [125, 189, 252], "top_n": [3, 5]}),
]

EQUITY_GRIDS = [
    (TSTrend, {"lookback": [125], "sizing": ["fixed", "inv_vol"],
               "vol_window": [20]}),
    (MomentumRotation, {"lookback": [63, 125], "top_n": [5],
                        "vol_window": [20], "min_score": [0.0],
                        "index_filter": [0], "filter_ticker": ["SPY"]}),
]


def main():
    long_df = load_playground()
    core = [s for cls, grid in CORE_GRIDS for s in expand_grid(cls, grid)]
    equity = [s for cls, grid in EQUITY_GRIDS for s in expand_grid(cls, grid)]

    Path("results").mkdir(exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_core = Path(f"results/leaderboard2_{stamp}_{sha}_core.csv")
    out_eq = Path(f"results/leaderboard2_{stamp}_{sha}_equity.csv")

    n = len(core) + len(equity)
    print(f"running {n} NEW backtests on playground (core {len(core)}, "
          f"equity-heavy {len(equity)})…")

    lb_core = run_batch(core, long_df, out_csv=out_core,
                        extra={"universe": "core20"})
    eq_df = filter_universe(long_df, TICKERS_EQUITY_HEAVY)
    lb_eq = run_batch(equity, eq_df, out_csv=out_eq,
                      extra={"universe": "equity14"})
    lb = (pd.concat([lb_core, lb_eq], ignore_index=True)
          .sort_values("sharpe", ascending=False).reset_index(drop=True))

    years = (long_df["date"].max() - long_df["date"].min()).days / 365.25
    print()
    print("THIS BATCH:      " + luck_warning(n, years))
    print("CUMULATIVE:      " + luck_warning(PRIOR_RUNS + n, years))
    print()
    print(lb.to_string(index=False, max_colwidth=40))
    print(f"\nPlateau: ts_trend / lookback (inv_vol, core20)")
    print(plateau_table(lb_core, "ts_trend", "lookback").round(2).to_string())
    print(f"\nPlateau: dual_momentum / lookback")
    print(plateau_table(lb_core, "dual_momentum", "lookback").round(2).to_string())
    print(f"\nsaved {out_core}\nsaved {out_eq}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the new test, then full suite**

Run: `python -m pytest tests/test_screen2.py -q` → PASS.
Run: `python -m pytest tests/ -q` → all pass.

- [ ] **Step 5: Smoke the script WITHOUT running the batch**

Run: `python -c "from scripts.screen2 import CORE_GRIDS, EQUITY_GRIDS; from src.batch.runner import expand_grid; print(sum(len(expand_grid(c,g)) for c,g in CORE_GRIDS+EQUITY_GRIDS), 'configs')"`
Expected: `20 configs`. Do NOT execute `main()` — the screening run happens live in the main session (owner watches via dashboard).

- [ ] **Step 6: Commit**

```bash
git add scripts/screen2.py tests/test_screen2.py
git commit -m "feat: second screening script — 20 new configs, benchmarks, cumulative luck line"
```

---

## Deferred (explicitly OUT of this plan)

- **Walk-forward analysis (engine v2)** — iteration candidate #6, a real engine change; separate spec/plan after this round's results.
- **Merging `research-dashboard`** — parallel session's branch; owner coordinates the merge.
- **The screening run itself + vault notes** (backtest note, registry updates, dual-momentum strategy note) — done live in the main session after this plan lands, per protocol.

## Self-Review

- Spec coverage: candidates 1 (Task 4), 2 (Task 5), 3 (Task 6), 4 (Task 3), 5 (Task 7), warning fix (Task 1), screening manifest (Task 8). Candidate 6 explicitly deferred. ✓
- Type consistency: `run_batch(extra=dict)` (Task 2) matches Task 8's calls; `TICKERS_EQUITY_HEAVY`/`filter_universe` names match; strategy DEFAULTS in Task 8 grids match Tasks 3–6 definitions (incl. `filter_ticker` passed explicitly so labels are stable). ✓
- Task 1's test construction is flagged honestly: public-API starvation may be unreachable; the implementer has an explicit fallback with a decision rule rather than a placeholder. ✓
