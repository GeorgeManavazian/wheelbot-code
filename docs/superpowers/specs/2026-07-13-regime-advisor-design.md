# Macro regime advisor — phase 1 design

- **Date:** 2026-07-13
- **Status:** design — awaiting owner review
- **Part of:** the macro layer for the wheel bot (decision record: vault `03 Decisions/2026-07-13 — Un-park wheel defense mechanics (repair pass)` records the macro-layer decisions from the same brainstorm)
- **Owner decisions already locked:** advisor first (no trade wiring); price-derived inputs only; direction = historical base rates, never a trained prediction model; states computed for **both** the market (SPY) and the traded ticker; later wiring will be fixed built-in rules with zero knobs.

## What phase 1 is

A read-only advisor with three pieces:

1. **Regime state module** — per-day market state from daily closes alone.
2. **Base-rate table** — from each state, what the underlying historically did next (the honest "most likely direction and why").
3. **Wheel autopsy by regime** — the wheel's own campaign ledger (built in the defense repair) grouped by the regime each campaign lived in: where rolls save money, where stops earn their keep, where the wheel bleeds.

Explicitly NOT phase 1: any trading decision, any engine wiring, prediction models, new data vendors, new dashboard knobs.

## Module layout

New package `src/engine_v2/regime/` — no imports from `options/` except the trade-ledger types used by the autopsy; nothing in `options/` imports `regime/` (advisor is a pure consumer).

### `state.py`

```python
@dataclass(frozen=True)
class RegimeState:
    date: pd.Timestamp
    trend: str          # "uptrend" | "downtrend" | "chop"
    vol: str            # "calm" | "normal" | "stressed"
    px_vs_200: float    # close / SMA200 - 1
    px_vs_50: float     # close / SMA50 - 1
    ma50_vs_200: float  # SMA50 / SMA200 - 1
    drawdown: float     # close / 252d rolling high - 1
    realized_vol: float # annualized 21d
    vol_pctile: float   # vs trailing 3y (min 1y) of its own history

def regime_series(closes: pd.Series) -> pd.DataFrame   # one row per day
def describe(state: RegimeState) -> str                 # plain language
```

**Rules (fixed, documented, no knobs):**
- `trend`: uptrend = close > SMA200 AND SMA50 > SMA200; downtrend = close < SMA200 AND SMA50 < SMA200; else chop.
- `vol`: 21d realized vol (annualized stdev of daily log returns); percentile against its own trailing 3 years (minimum 1 year of history, else state = "warmup" and excluded from tables). calm < 40th pctile, stressed > 75th, normal between. Percentile thresholds are constants chosen ex-ante — never swept.
- All windows trailing; a state on day *d* uses closes ≤ *d* only. **No look-ahead, property-tested.**
- First 200 trading days of any series = "warmup", excluded everywhere.
- `describe()` example: `"Uptrend (4.1% above 200d, 50>200), calm vol (23rd pctile), 1.8% off 252d high."`

### `base_rates.py`

```python
def base_rate_table(closes: pd.Series, horizon: int = 21) -> pd.DataFrame
```

For each (trend × vol) cell over the full history: forward `horizon`-day return — median, mean, win rate, 5th percentile, N samples. Cells with N < 30 are shown but flagged `thin=True`.

**Honesty rules:**
- Descriptive statistics over the series' own history — context for a human, not a signal. **If a future phase ever feeds these numbers into a backtested decision, they must be recomputed walk-forward** (only data before each decision date). This limitation is printed in the table header, not buried.
- Overlapping forward windows inflate effective sample size; N counts days, and the table says so.

### `autopsy.py`

```python
def campaign_regimes(result, cfg, market_closes, ticker_closes) -> pd.DataFrame
def autopsy_table(campaigns_df) -> pd.DataFrame
```

- Tags each campaign (from `campaign_table` + the trade ledger) with the **market state (SPY)** and the **ticker state** on its open date.
- Groups campaign P&L, roll counts, stop counts, win rate by regime cell — separately for market-state cells and ticker-state cells.
- Consumes only closed campaigns; open campaign flagged and excluded from win rates.

### Data

- Daily closes come from what is already on disk: chain parquets' `underlying` column (2017+, per ticker) and `fixtures/bars_etf_universe_2010_2026.parquet` / the existing bars data for longer SPY history. No new vendor, no network dependency in the module (callers pass `pd.Series`).
- Base-rate tables prefer the longest clean series available for the symbol.

### Dashboard

New "Regime" tab (read-only):
- Today's state — SPY and selected ticker, `describe()` strings + the raw numbers
- Base-rate table for both, thin cells greyed, walk-forward caveat shown
- Autopsy table when a wheel run is stashed in the session

## Testing

- **Known dates:** 2020-03-20 SPY = downtrend + stressed; 2017-06-01 SPY = uptrend + calm; 2022-06 = downtrend. Asserted on real fixture data.
- **No look-ahead property:** for random split points, `regime_series(closes[:k]).iloc[-1]` equals `regime_series(closes).iloc[k-1]`.
- **Warmup:** first 200 days excluded; series shorter than warmup → empty result, no crash.
- **Base rates:** synthetic series with known forward returns → exact medians/win rates; thin-cell flag at N<30.
- **Autopsy:** synthetic ledger with campaigns in known regimes → correct grouping; open campaign excluded from win rate.
- Coverage folded into the engine_v2 gate (≥85%).

## Out of scope (phase 2+, each needs its own spec)

- Wiring states into entry/roll/stop/assignment decisions (fixed rules, pre-registered, tested via the same A/B + execution-audit discipline as the defense repair)
- Support/resistance zone detection beyond MA distances
- VIX or any external data series
- Any per-regime parameter variation (that would be knobs)
