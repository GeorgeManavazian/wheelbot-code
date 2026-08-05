"""Step 1, part 2: re-tabulate the IV signal AFTER applying the engine's real
entry gates and dropping physically impossible data.

The raw pass deliberately gated nothing, which meant it measured a strategy the
bot would refuse to trade (minimum credit in the sample: $0.01). It also carried
IV values up to 21474, which wrecked the tails where the deciles matter.

Gates mirrored from src/engine_v2/options/fills.py at the sweep's winning arm
(delta 0.40 / DTE 7 / TP 25%):
  tp_exit_feasible  credit >= max(0.01/(1-tp), 2*friction/(tp*mult)) = $0.056
  credit_ok         credit <= 0.03 * strike        (intrinsic filter)
  yield_ok          (credit/strike)*(365/dte) >= 0.08

Sanity bounds are data-quality, not tuning: an IV of 21474 or a 960% realized
vol is a bad print, not a trade.

The iv_rank history is intentionally NOT gate-filtered -- a contract's rank
against its own past should use every observation, not only tradeable ones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADES = "/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/1b0bf6a9-903a-4045-9aef-3169be0d6c98/scratchpad/trades_full.parquet"

TP = 0.25
FRICTION = 0.70
MULT = 100
MIN_TICK = 0.01
CREDIT_FLOOR = max(MIN_TICK / (1.0 - TP), 2.0 * FRICTION / (TP * MULT))
MAX_CREDIT_PCT_OF_STRIKE = 0.03
MIN_ANN_YIELD = 0.08

IV_LO, IV_HI = 0.03, 3.0
RV_HI = 3.0


def decile_table(df, col):
    d = df.dropna(subset=[col]).copy()
    d["bucket"] = pd.qcut(d[col], 10, labels=False, duplicates="drop")
    g = d.groupby("bucket").agg(
        n=("pnl", "size"),
        tickers=("ticker", "nunique"),
        lo=(col, "min"),
        hi=(col, "max"),
        ret_bps=("ret_on_collateral", lambda s: s.mean() * 10000),
        win=("pnl", lambda s: (s > 0).mean() * 100),
        assign=("exit", lambda s: (s == "assigned").mean() * 100),
    )
    eq = (d.groupby(["bucket", "ticker"])["ret_on_collateral"].mean()
            .groupby("bucket").mean() * 10000)
    g["eqw_bps"] = eq
    # Ticker-clustered standard error: one mean per ticker, SE across tickers.
    # Overlapping daily holdings make a naive per-trade SE far too small.
    per_tk = d.groupby(["bucket", "ticker"])["ret_on_collateral"].mean() * 10000
    g["eqw_se"] = per_tk.groupby("bucket").sem()
    return g


def by_year(df, col, top_q=0.9):
    """Does the top decile beat the rest in EVERY year, or is it one regime?"""
    d = df.dropna(subset=[col]).copy()
    cut = d[col].quantile(top_q)
    d["top"] = d[col] >= cut
    d["year"] = d["date"].dt.year
    t = d.pivot_table(index="year", columns="top", values="ret_on_collateral",
                      aggfunc="mean") * 10000
    t.columns = ["rest_bps", "top_decile_bps"]
    t["edge_bps"] = t["top_decile_bps"] - t["rest_bps"]
    t["n_top"] = d[d["top"]].groupby("year").size()
    return t.round(1)


def main():
    raw = pd.read_parquet(TRADES)
    n0 = len(raw)

    sane = raw[(raw["iv"].between(IV_LO, IV_HI)) & (raw["rv20"] <= RV_HI)]
    yld = (sane["credit"] / sane["strike"]) * (365.0 / sane["dte"])
    t = sane[
        (sane["credit"] >= CREDIT_FLOOR)
        & (sane["credit"] <= MAX_CREDIT_PCT_OF_STRIKE * sane["strike"])
        & (yld >= MIN_ANN_YIELD)
    ].copy()

    print("=== FILTERING ===")
    print("raw trades            %6d" % n0)
    print("after sanity bounds   %6d  (-%d)" % (len(sane), n0 - len(sane)))
    print("after engine gates    %6d  (-%d)" % (len(t), len(sane) - len(t)))
    print("tickers %d   window %s -> %s"
          % (t["ticker"].nunique(), t["date"].min().date(), t["date"].max().date()))
    print("median |delta| %.3f   median dte %.0f"
          % (t["delta"].abs().median(), t["dte"].median()))
    print("\nexit mix:")
    print((t["exit"].value_counts(normalize=True) * 100).round(1).to_string())
    print("\nBASELINE all gated trades: %.1f bps   win %.1f%%"
          % (t["ret_on_collateral"].mean() * 10000, (t["pnl"] > 0).mean() * 100))

    for col, name in [("iv_rank", "A: IV RANK"), ("iv_rv", "B: IV - RV20")]:
        print("\n=== %s ===" % name)
        print(decile_table(t, col).round(2).to_string())
        print("\n-- top decile by year --")
        print(by_year(t, col).to_string())

    print("\n=== SIGNAL OVERLAP (post-clean) ===")
    print("pearson  %.3f" % t["iv_rank"].corr(t["iv_rv"]))
    print("spearman %.3f" % t["iv_rank"].corr(t["iv_rv"], method="spearman"))

    d = t.copy()
    d["A"] = pd.qcut(d["iv_rank"], 3, labels=["A-lo", "A-mid", "A-hi"], duplicates="drop")
    d["B"] = pd.qcut(d["iv_rv"], 3, labels=["B-lo", "B-mid", "B-hi"], duplicates="drop")
    print("\n=== A x B mean ret (bps) ===")
    print((d.pivot_table(index="A", columns="B", values="ret_on_collateral",
                         aggfunc="mean", observed=True) * 10000).round(1).to_string())
    print("\n--- counts ---")
    print(d.pivot_table(index="A", columns="B", values="pnl",
                        aggfunc="size", observed=True).to_string())


if __name__ == "__main__":
    main()
