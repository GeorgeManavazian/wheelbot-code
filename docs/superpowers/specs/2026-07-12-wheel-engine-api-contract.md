# Wheel engine ↔ dashboard API contract

Date: 2026-07-12
Status: **binding**. Both sides build to this; neither edits the other's files.

Companion to `2026-07-12-wheel-multi-ticker-design.md` (the *why*). This doc is
only the *seam*.

## Ownership

| Owner | Files |
|---|---|
| **Dashboard agent** | `dashboard/**` |
| **Engine agent** | `src/engine_v2/options/**`, `tests/engine_v2/options/**`, `scripts/**` |

Nobody edits across the line. The wiring works because both sides build to the
signatures below.

---

## 1. `WheelConfig` — the new shape

`src/engine_v2/options/wheel.py`

```python
@dataclass
class WheelConfig:
    starting_capital: float = 100_000.0
    put_delta: float = 0.20            # was 0.30
    call_delta: float = 0.20           # was 0.30
    target_dte: int = 7                # NEW — the only DTE input
    take_profit_pct: float | None = 0.50
    cash_yield: float = 0.0            # NEW — annualized rate on idle collateral
    contract_multiplier: int = 100
    commission_per_contract: float = 0.65
```

### ⚠ Removed: `dte_min`, `dte_max`

**They are gone.** `dashboard/views/wheel.py:67-69` currently constructs
`WheelConfig(dte_min=..., dte_max=...)` — that will raise `TypeError` after the
engine lands.

They were never a strategy. They were a search window, and the engine picked an
expiry inside it *arbitrarily* — measured on the published SPY run, the chosen DTE
had std 6.0 against 6.06 for a uniform random pick over the same window. The bot
was rolling a die. `target_dte` replaces them with an actual decision.

### The band is derived, not entered

```python
def derived_band(target_dte: int) -> tuple[int, int]:
    """The DTE guard rail. Display read-only; never a user input."""
    return max(5, target_dte - 2), target_dte + 3
```

`derived_band(7) == (5, 10)` · `derived_band(30) == (28, 33)`

Its only job is to say **no**: floor rejects expiry stubs, ceiling rejects a
monthly when the weekly doesn't exist that day (the bot sits in cash instead).

**Dashboard:** render `Target DTE` as the input; call `derived_band()` and show the
result as read-only caption text (e.g. *"trades expiries 5–10 days out"*).

---

## 2. Take-profit slider

- Widget: **slider, 1 → 100, step 1, default 50.**
- Pass to the engine as **`take_profit_pct = value / 100.0`**.
- **100 ⇒ 1.0 ⇒ hold to expiry.** The engine treats `>= 1.0` as "never take
  profit". No `None` needed from the dashboard, though the engine still accepts it.

Replaces the 4-option selectbox at `dashboard/views/wheel.py:57`.

---

## 3. Ticker dropdown — data path convention

Both pulls write deterministic per-ticker filenames. The dashboard can glob them;
no hardcoded paths.

```
data/options/{ticker_lower}_greeks_eod_all.parquet   # EOD greeks — always required
data/options/{ticker_lower}_ohlc_1h_all.parquet      # hourly OHLC — optional
```

Examples: `spy_greeks_eod_all.parquet`, `gdx_greeks_eod_all.parquet`,
`gdx_ohlc_1h_all.parquet`.

```python
def available_tickers(data_dir="data/options") -> list[str]:
    """Tickers with an EOD chain on disk. Engine ships this; dashboard calls it."""
```

Basket: GDX, SLV, XOP, XBI, EEM, EWZ, TLT, ARKK, QQQ (+ SPY).

The intraday checkbox should be **disabled** when the ticker has no
`_ohlc_1h_all.parquet` — the pull is still running and some tickers will land
before others.

---

## 4. Selection (engine-internal — dashboard does not call this)

`src/engine_v2/options/select.py`

```python
def select_contract(chain, date, right, target_delta, target_dte, root):
    """Expiry FIRST, then strike. Deterministic.

    1. Of the expiries visible on `date`, take the one nearest `target_dte`,
       subject to derived_band(target_dte). If none qualify -> None (sit in cash).
    2. Within that one expiry, take the strike whose |delta| is nearest
       `target_delta`.
    """
```

`root` fixes `select.py:17`, which hardcodes `Contract("SPY", ...)` on **every**
ticker. Cosmetic today, unreadable across nine tickers.

The band is computed from the expiries **visible on that date only** — never from
the full expiration history, which would use 2026 knowledge to make a 2019
decision.

---

## 5. Report — what comes back

`src/engine_v2/options/report.py`

### Two benchmarks, both real

`spy_buy_hold()` **currently derives the benchmark from whatever chain it is
handed** — so on GDX, "vs SPY" silently means "vs GDX". The strategy racing
itself. Accidentally correct on SPY runs only. Replaced by:

```python
def buy_hold_curve(chain, starting_capital) -> pd.Series:
    """Buy-hold the chain's OWN underlying. Was spy_buy_hold()."""

def spy_curve(starting_capital, index) -> pd.Series:
    """Buy-hold real SPY, loaded from spy_greeks_eod_all.parquet, reindexed to
    `index`. This is the honest cross-ticker benchmark."""
```

`WheelReport` gains:

```python
benchmark_underlying: dict   # vs buy-hold this ticker  (was: benchmark)
benchmark_spy:        dict   # vs buy-hold real SPY     (NEW)
```

Each is `{total_return, cagr, sharpe, max_drawdown}`, as today.

**`benchmark` is renamed** — `dashboard/views/wheel.py:83` and `:88` reference
`rep.benchmark` and `spy_buy_hold`; both need updating.

### New fields in `rep.stats`

```python
"realized_dte":  pd.Series   # value_counts of DTE at each sale — PROVES selection
                             # is deterministic now. Worth surfacing prominently.
"n_days_flat":   int         # days the bot sat in cash (no eligible expiry)
"pct_days_flat": float       # ^ as a fraction of the window
```

`pct_days_flat` matters: if a ticker is flat 40% of the time because weeklies
didn't exist yet, that changes what the benchmark comparison even *means*. Do not
bury it.

Already present, keep rendering: `premium_collected`, `net_premium`,
`n_assignments`, `n_called_away`, `n_take_profits`, `assignment_rate`.

### Headline is P&L, not CAGR

Owner's rule #3. `st.metric("P&L", ...)` stays primary; CAGR should not be the
headline anywhere.

---

## 6. `run_wheel` — unchanged signature

```python
run_wheel(chain, cfg, intraday=None) -> WheelResult
```

Still returns `.equity`, `.trades`, `.final_cash`, `.final_shares`. Nothing the
dashboard does with the result changes except the renamed benchmark fields.

---

## 7. Run-history schema

`dashboard/wheel_history.py` logs `dte_min` / `dte_max` (`views/wheel.py:117-118`).
Those keys die with the config. Log **`target_dte`** instead.

Old rows will lack it. The load-config-back path (`views/wheel.py:29-31`) must
tolerate missing keys rather than crash on historical runs — or the history is
worth discarding, since every pre-fix run was produced by the random-expiry bot
and its config is not reproducible anyway.

---

## Migration checklist — every dashboard line that breaks

| Line | Today | After |
|---|---|---|
| `wheel.py:9-10` | hardcoded `FIXTURE` / `FULL` paths | `available_tickers()` dropdown |
| `wheel.py:29-31` | loads `dte_min` / `dte_max` from history | `target_dte` |
| `wheel.py:55-56` | DTE min/max `number_input`s | **delete**; one `target_dte` input |
| `wheel.py:57` | 4-option TP selectbox | slider 1–100 |
| `wheel.py:67-69` | `WheelConfig(dte_min=, dte_max=)` | `WheelConfig(target_dte=)` |
| `wheel.py:61,70` | hardcoded SPY intraday fixture | per-ticker `_ohlc_1h_all.parquet` |
| `wheel.py:83,88` | `rep.benchmark`, `spy_buy_hold()` | `rep.benchmark_spy`, `spy_curve()` |
| `wheel.py:117-118` | logs `dte_min` / `dte_max` | logs `target_dte` |

---

## Order

1. Engine lands first (worktree, zero dashboard files touched). Then the contract
   is not a promise — it's importable, tested code.
2. Dashboard wires to it.
3. Runs: SPY audit (30-delta / target 30), SPY at basket config (20-delta /
   target 7), then the basket.
