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
    
    Path("results").mkdir(exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(f"results/leaderboard_{stamp}_{sha}.csv")
    print(f"running {len(strategies)} backtests on playground…")
    print(f"streaming per-run results to {out}")
    lb = run_batch(strategies, long_df, out_csv=out)

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

    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
