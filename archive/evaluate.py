"""Standard strategy evaluation (methodology decided 2026-07-10).

Judge on the RECENT window (deployment relevance); keep the FULL history for
context and decay detection; ALWAYS print the year-by-year Sharpe. Never headline
a single blended multi-year number -- it hides regime-dependent decay.

Usage: python scripts/evaluate.py gap
       python scripts/evaluate.py clenow
"""
import sys
import warnings

import numpy as np
import pandas as pd

from src.engine_v2.backtest.orchestrator import position_history, BacktestConfig
from src.engine_v2.strategy.gap_pattern import GapPatternTypeA
from src.engine_v2.strategy.counter_trend import CounterTrendDipBuy

warnings.filterwarnings("ignore")

UNIVERSE = "fixtures/bars_etf_universe_2010_2026.parquet"
RECENT_START = "2021-07-01"  # ~last 5 years of the 2010-2026 fixture
COSTS = dict(spread_bps_per_side=1.0, borrow_bps_annual=50.0)

STRATS = {
    "gap": (GapPatternTypeA, {"time_index": 20, "filter_lookback": 50}),
    "clenow": (CounterTrendDipBuy, {"dip_buy": -3.0}),
}


def _sharpe(r):
    return float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0


def _cagr(eq):
    yrs = len(eq) / 252
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else 0.0


def _maxdd(eq):
    return float((eq / eq.cummax() - 1).min())


def _report(label, ret, eq):
    full = ret
    recent = ret[ret.index >= RECENT_START]
    eq_recent = eq[eq.index >= RECENT_START]
    print(f"\n{label}")
    print(f"  FULL   2010-2026 : Sharpe {_sharpe(full):+.2f}  CAGR {_cagr(eq):+.2%}  maxDD {_maxdd(eq):+.2%}")
    print(f"  RECENT ~5y       : Sharpe {_sharpe(recent):+.2f}  CAGR {_cagr(eq_recent):+.2%}  maxDD {_maxdd(eq_recent):+.2%}   <- HEADLINE")
    by = full.groupby(full.index.year).apply(_sharpe)
    print("  year-by-year Sharpe (decay curve):")
    print("    " + "  ".join(f"{y}:{v:+.2f}" for y, v in by.items()))


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "gap"
    cls, params = STRATS[key]
    raw = pd.read_parquet(UNIVERSE)
    tickers = list(raw.columns.get_level_values(0).unique())
    cfg = BacktestConfig(**COSTS)

    curves = {}
    for t in tickers:
        b = raw[[t]].dropna()
        eq = position_history(cls, params, b, b.index, cfg)["equity"]
        curves[t] = eq / eq.iloc[0]
    port = pd.concat(curves, axis=1).mean(axis=1)
    _report(f"{key.upper()} portfolio (equal-weight {len(tickers)} ETFs, real costs)",
            port.pct_change().dropna(), port)

    spy = raw["SPY"]["Close"].dropna()
    bh = spy / spy.iloc[0]
    _report("BUY-HOLD SPY (benchmark)", bh.pct_change().dropna(), bh)


if __name__ == "__main__":
    main()
