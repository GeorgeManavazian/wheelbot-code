"""Per-day market regime from daily closes alone. Fixed ex-ante thresholds —
never swept. All windows trailing: the state on day d uses closes <= d only.

NOTE: a legacy classifier exists at src/engine_v2/data/regime.py (bull/bear +
absolute-vol calm/high, consumed by backtest metrics). The two are DIFFERENT
taxonomies for different consumers; if you change thresholds here, check
whether that one needs the same intent. Consolidation deferred deliberately
(regime-advisor spec, declined items).

Effective warmup: the first emitted state needs the 200d SMA (WARMUP) AND a
vol percentile, which needs VOL_WINDOW returns + VOL_MIN observations —
in practice the first row lands ~273 trading days in, not 200."""
from __future__ import annotations
import numpy as np
import pandas as pd

WARMUP = 200          # SMA200 horizon; real first-state day is later (see above)
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
    sma9, sma20 = c.rolling(9).mean(), c.rolling(20).mean()   # short-horizon (~2wk)
    logret = np.log(c / c.shift(1))
    rv = logret.rolling(VOL_WINDOW).std() * np.sqrt(252)
    # percentile of today's realized vol within its own trailing window
    pct = rv.rolling(VOL_LOOKBACK, min_periods=VOL_MIN).rank(pct=True)
    high = c.rolling(HIGH_WINDOW, min_periods=1).max()
    df = pd.DataFrame({
        "px_vs_200": c / sma200 - 1,
        "px_vs_50": c / sma50 - 1,
        "ma50_vs_200": sma50 / sma200 - 1,
        "fast_spread": sma9 / sma20 - 1,   # 9d/20d gap: ~0 = flat over our hold
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
    return df[["trend","vol","px_vs_200","px_vs_50","ma50_vs_200","fast_spread",
               "drawdown","realized_vol","vol_pctile"]]

def describe(row) -> str:
    side = "above" if row["px_vs_200"] >= 0 else "below"
    cross = "50>200" if row["ma50_vs_200"] >= 0 else "50<200"
    return (f"{row['trend'].capitalize()} ({abs(row['px_vs_200']):.1%} {side} 200d, {cross}), "
            f"{row['vol']} vol ({row['vol_pctile']:.0%} pctile), "
            f"{abs(row['drawdown']):.1%} off 252d high.")

def is_good_renting_weather(row, max_ma_spread=None, max_fast_spread=None,
                            max_fast_fall=None) -> bool:
    """Good-to-rent weather for the chop scanner: range-bound (chop) and not
    violently volatile (not stressed). Uptrends (hold instead) and downtrends
    (falling knife) are excluded; a None/unknown row is not good-to-rent.

    Opt-in guards (default None = off so the backtest stays byte-identical):
      max_ma_spread  -- |50d/200d - 1| <= this. STRUCTURAL (months): the bare
        crossover labels a fast move "chop" until the 50d catches through the
        200d, so a knife mid-fall (MAs pulling apart) slips through. Tight = flat.
      max_fast_spread -- |9d/20d - 1| <= this. TACTICAL, SYMMETRIC (~2wk): rejects
        any short-horizon leg, up or down.
      max_fast_fall  -- reject only if 9d/20d - 1 < -this. TACTICAL, DOWN-ONLY: a
        put seller only loses on a FALL, so drop a down-leg but KEEP an up-leg
        (which expires the put worthless). Prefer this over max_fast_spread."""
    if row is None:
        return False
    if row["trend"] != "chop" or row["vol"] == "stressed":
        return False
    if max_ma_spread is not None and abs(row["ma50_vs_200"]) > max_ma_spread:
        return False
    if max_fast_spread is not None and abs(row["fast_spread"]) > max_fast_spread:
        return False
    if max_fast_fall is not None and row["fast_spread"] < -max_fast_fall:
        return False
    return True
