"""Step 1: does an IV-based entry signal predict short-put outcome?

Measures, per ticker-day, the two candidate signals from the 2026-08-04 discussion:

  A  iv_rank   -- today's sold-contract IV ranked against its own trailing 252
                  observations (Natenberg relative volatility rank)
  B  iv_rv     -- today's sold-contract IV minus trailing 20-day realized vol
                  (the variance risk premium: pay above risk)

...then buckets realised trade outcome by decile of each, and reports the
correlation between them. No threshold is fitted; this only asks whether a
relationship exists at all.

Selection mirrors the engine (select.py): expiry FIRST (nearest target_dte
inside derived_band), THEN strike (|delta| nearest target). Entry fills at the
bid, take-profit buys back at the ask -- crossing the spread both sides, the
convention the engine uses.

LOOK-AHEAD DISCIPLINE
  - iv_rank at date d uses only signal values at dates <= d.
  - rv20 at date d uses only underlying closes at dates <= d.
  - Nothing but the outcome itself reads a date after d.

KNOWN LIMITATIONS (stated, not hidden)
  - Outcome is a single short put run to take-profit or expiry. No assignment
    -> covered call -> basis floor continuation. This is deliberate: an entry
    filter's job is to pick better entries, so the raw put leg is the right unit.
  - EOD granularity: take-profit is evaluated on daily closes, so intraday
    touches are missed. The engine's intraday layer would fire slightly more.
  - Every trading day is an entry, so holdings overlap and consecutive trades on
    one ticker are correlated. That inflates the apparent precision of any
    single decile. It does NOT bias the decile means themselves. A weekly
    sample was tried first and rejected: which expiry falls inside the 5-10 DTE
    band depends on weekday, so fixed-weekday sampling silently shifted the sold
    DTE to 10. Equal-weighting tickers (second table) is the robustness check.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

DATA = "data/options"

TARGET_DELTA = 0.40
TARGET_DTE = 7
DTE_LO, DTE_HI = max(5, TARGET_DTE - 2), TARGET_DTE + 3  # derived_band(7) = (5, 10)
TAKE_PROFIT = 0.25          # winning arm from the sweep
FRICTION = 0.70             # $0.65 commission + $0.05 fees, per contract per side
MULT = 100
RV_WINDOW = 20
RANK_WINDOW = 252
MIN_RANK_OBS = 150          # refuse to rank against a thin history

START = pd.Timestamp("2024-01-15")
END = pd.Timestamp("2026-07-01")


def realized_vol(underlying_by_date, window=RV_WINDOW):
    """Trailing annualised realized vol, indexed by date. Value at d uses only
    closes at dates <= d."""
    logret = np.log(underlying_by_date / underlying_by_date.shift(1))
    return logret.rolling(window).std() * np.sqrt(252)


def iv_rank(series, window=RANK_WINDOW, min_obs=MIN_RANK_OBS):
    """Percentile of each value within the trailing `window` observations,
    inclusive of today. Value at d never reads a date after d."""
    return series.rolling(window, min_periods=min_obs).apply(
        lambda w: (w[:-1] < w[-1]).mean(), raw=True
    )


def pick_put(day, target_delta=TARGET_DELTA, target_dte=TARGET_DTE):
    """Engine order: nearest expiry inside the band first, then nearest |delta|
    within that one expiry. Returns a row or None."""
    band = day[(day["dte"] >= DTE_LO) & (day["dte"] <= DTE_HI) & (day["right"] == "P")]
    if band.empty:
        return None
    expiries = band["expiry"].unique()
    best_exp = min(expiries, key=lambda e: abs(band.loc[band["expiry"] == e, "dte"].iloc[0] - target_dte))
    exp_rows = band[band["expiry"] == best_exp]
    idx = (exp_rows["delta"].abs() - target_delta).abs().idxmin()
    return exp_rows.loc[idx]


def outcome(legs, und_by_date, entry_row, entry_date):
    """Run one short put forward to take-profit or expiry.

    `legs` maps (expiry, strike) -> that contract's put rows, date-sorted, so
    the forward walk is a dict hit rather than a mask over the whole ticker.

    Returns (pnl_dollars, exit_kind) or None if the contract cannot be followed.
    """
    strike = float(entry_row["strike"])
    expiry = entry_row["expiry"]
    credit = float(entry_row["bid"])
    if credit <= 0 or strike <= 0:
        return None

    leg = legs.get((expiry, strike))
    if leg is None:
        return None
    fwd = leg[leg["date"] > entry_date]

    tp_price = (1.0 - TAKE_PROFIT) * credit
    asks = fwd["ask"].to_numpy()
    hit = np.flatnonzero((asks > 0) & (asks <= tp_price))
    if hit.size:
        return (credit - asks[hit[0]]) * MULT - 2 * FRICTION, "take_profit"

    # No take-profit: settle at expiry against the underlying.
    under = und_by_date.get(expiry)
    if under is None:
        if fwd.empty:
            return None
        under = float(fwd["underlying"].iloc[-1])  # last seen before a data gap
    under = float(under)

    if under >= strike:
        return credit * MULT - FRICTION, "expired_worthless"
    intrinsic = (strike - under) * MULT
    return credit * MULT - intrinsic - FRICTION, "assigned"


def run_ticker(path, skips):
    tk = os.path.basename(path).split("_")[0].upper()
    df = pd.read_parquet(path)
    if df.empty:
        skips["empty_file"] += 1
        return None
    df = df.sort_values(["date", "expiry", "strike"])

    # Underlying close per date -> trailing realized vol.
    und = df.groupby("date")["underlying"].first().sort_index()
    rv = realized_vol(und)

    # Constant-target IV series: the 0.40-delta put in the 5-10 DTE band, every
    # available date. This is literally the price we are paid, so it is the
    # right thing to both rank and compare against realized vol.
    picks = {}
    for d, day in df.groupby("date"):
        row = pick_put(day)
        if row is not None:
            picks[d] = row
    if len(picks) < MIN_RANK_OBS:
        skips["thin_history"] += 1
        return None

    sig = pd.Series({d: float(r["iv"]) for d, r in picks.items()}).sort_index()
    rank = iv_rank(sig)

    # Every trading day in the window is an entry. See the header note on why
    # weekly sampling was rejected.
    dates = pd.Series(sorted(picks.keys()))
    entries = dates[(dates >= START) & (dates <= END)]
    if entries.empty:
        skips["no_dates_in_window"] += 1
        return None

    puts = df[df["right"] == "P"]
    legs = {k: v.sort_values("date") for k, v in puts.groupby(["expiry", "strike"])}
    und_map = und.to_dict()

    rows = []
    for d in entries:
        row = picks[d]
        r_rank = rank.get(d, np.nan)
        r_rv = rv.get(d, np.nan)
        if not np.isfinite(r_rank) or not np.isfinite(r_rv):
            skips["no_signal"] += 1
            continue
        res = outcome(legs, und_map, row, d)
        if res is None:
            skips["unfollowable_leg"] += 1
            continue
        pnl, kind = res
        strike = float(row["strike"])
        rows.append({
            "ticker": tk,
            "date": d,
            "strike": strike,
            "dte": int(row["dte"]),
            "delta": float(row["delta"]),
            "credit": float(row["bid"]),
            "iv": float(row["iv"]),
            "iv_rank": float(r_rank),
            "rv20": float(r_rv),
            "iv_rv": float(row["iv"]) - float(r_rv),
            "pnl": pnl,
            "ret_on_collateral": pnl / (strike * MULT),
            "exit": kind,
        })
    return pd.DataFrame(rows) if rows else None


def decile_table(df, col):
    """Trade-weighted deciles, plus an equal-weight-by-ticker column so a single
    heavily-traded name cannot carry a bucket."""
    d = df.dropna(subset=[col]).copy()
    d["bucket"] = pd.qcut(d[col], 10, labels=False, duplicates="drop")
    g = d.groupby("bucket").agg(
        n=("pnl", "size"),
        tickers=("ticker", "nunique"),
        signal_lo=(col, "min"),
        signal_hi=(col, "max"),
        mean_ret_bps=("ret_on_collateral", lambda s: s.mean() * 10000),
        win_rate=("pnl", lambda s: (s > 0).mean() * 100),
        assign_rate=("exit", lambda s: (s == "assigned").mean() * 100),
    )
    # Equal-weight: average within ticker first, then across tickers.
    eq = (d.groupby(["bucket", "ticker"])["ret_on_collateral"].mean()
            .groupby("bucket").mean() * 10000)
    g["eqw_ret_bps"] = eq
    return g


def combo_table(df):
    """Does stacking A and B beat either alone? Terciles of each, jointly."""
    d = df.dropna(subset=["iv_rank", "iv_rv"]).copy()
    d["A"] = pd.qcut(d["iv_rank"], 3, labels=["A-low", "A-mid", "A-high"], duplicates="drop")
    d["B"] = pd.qcut(d["iv_rv"], 3, labels=["B-low", "B-mid", "B-high"], duplicates="drop")
    mean = (d.pivot_table(index="A", columns="B", values="ret_on_collateral",
                          aggfunc="mean", observed=True) * 10000).round(1)
    n = d.pivot_table(index="A", columns="B", values="pnl",
                      aggfunc="size", observed=True)
    return mean, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="pilot on N tickers, 0 = all")
    ap.add_argument("--out", default="iv_signal_trades.parquet")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(DATA, "*_greeks_eod_all.parquet")))
    if args.limit:
        paths = paths[: args.limit]
    print("tickers: %d" % len(paths), flush=True)

    from collections import Counter
    skips = Counter()

    frames = []
    for i, p in enumerate(paths, 1):
        try:
            r = run_ticker(p, skips)
        except Exception as e:
            skips["exception"] += 1
            print("  skip %s: %s" % (os.path.basename(p), e), flush=True)
            continue
        if r is not None:
            frames.append(r)
        if i % 25 == 0:
            print("  %d/%d  trades so far: %d" % (i, len(paths), sum(len(f) for f in frames)), flush=True)

    if not frames:
        print("no trades produced")
        return 1

    trades = pd.concat(frames, ignore_index=True)
    trades.to_parquet(args.out)

    print("\n=== SAMPLE ===")
    print("trades: %d   tickers: %d" % (len(trades), trades["ticker"].nunique()))
    print("window: %s -> %s" % (trades["date"].min().date(), trades["date"].max().date()))
    print("median |delta| sold: %.3f   median dte: %.0f"
          % (trades["delta"].abs().median(), trades["dte"].median()))
    print("\nexit mix:")
    print((trades["exit"].value_counts(normalize=True) * 100).round(1).to_string())
    print("\noverall mean return on collateral: %.1f bps   win rate %.1f%%"
          % (trades["ret_on_collateral"].mean() * 10000, (trades["pnl"] > 0).mean() * 100))

    for col, name in [("iv_rank", "A: IV RANK (vs own trailing 252)"),
                      ("iv_rv", "B: IV - RV20 (variance risk premium)")]:
        print("\n=== %s ===" % name)
        print(decile_table(trades, col).round(2).to_string())

    sub = trades.dropna(subset=["iv_rank", "iv_rv"])
    print("\n=== SIGNAL OVERLAP ===")
    print("pearson  A vs B: %.3f" % sub["iv_rank"].corr(sub["iv_rv"]))
    print("spearman A vs B: %.3f" % sub["iv_rank"].corr(sub["iv_rv"], method="spearman"))

    mean, n = combo_table(trades)
    print("\n=== A x B, mean return on collateral (bps) ===")
    print(mean.to_string())
    print("\n--- trade counts ---")
    print(n.to_string())

    print("\n=== SKIPS ===")
    for k, v in sorted(skips.items(), key=lambda kv: -kv[1]):
        print("  %-20s %d" % (k, v))
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
