"""Siege-exit A/B (pre-registered, spec 2026-07-14): two paired arms at the
frozen basket config, EOD fills. Baseline vs +siege exit, raw, no selection.

XOP starts 2020-07-01 in BOTH arms: its chain is unadjusted through the
2020-03-31 1:4 reverse split (see _STATUS) — spanning it books phantom gains.

DISCIPLINE: unseen tickers refused without --after-basket-run.

Run: PYTHONPATH=. .venv/bin/python scripts/run_siege_exit.py [TICKER ...]
"""
import sys
import pandas as pd
from pathlib import Path
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import run_wheel, WheelConfig
from src.engine_v2.options.report import wheel_report, position_log, buy_hold_curve
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0)
SEEN = ["SPY", "GDX", "SLV", "XOP"]
UNSEEN = {"XBI", "EEM", "EWZ", "TLT", "ARKK"}
START_OVERRIDE = {"XOP": pd.Timestamp("2020-07-01")}   # split-broken before
ARMS = [
    ("SE",   {"call_min_strike": "basis"},
             {"call_min_strike": "basis", "regime_siege_exit": True}),
    ("SE+E", {"call_min_strike": "basis", "regime_entry_gate": True},
             {"call_min_strike": "basis", "regime_entry_gate": True,
              "regime_siege_exit": True}),
]

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = [t.upper() for t in args] or SEEN
    blocked = [t for t in tickers if t in UNSEEN]
    if blocked and "--after-basket-run" not in sys.argv:
        sys.exit(f"REFUSED: {blocked} are pre-registered unseen tickers. "
                 f"Run the basket first, then pass --after-basket-run.")
    lines = ["Siege-exit A/B — frozen basket config, EOD fills, raw.",
             "shares P&L = realized SHARES-leg total from the position log."]
    for t in tickers:
        ch = pd.read_parquet(chain_path(t))
        ch["date"] = pd.to_datetime(ch["date"])
        if t in START_OVERRIDE:
            ch = ch[ch["date"] >= START_OVERRIDE[t]].reset_index(drop=True)
        states = regime_series(closes_for(t))
        lines.append(f"\n=== {t}  ({ch['date'].min().date()} → {ch['date'].max().date()}) ===")
        lines.append(f"{'arm':<18} {'P&L':>10} {'Sharpe':>7} {'maxDD':>8} "
                     f"{'exits':>6} {'uncovered':>10} {'shares P&L':>11}")
        for arm, base_ov, siege_ov in ARMS:
            for label, ov in ((f"{arm}: baseline", base_ov), (f"{arm}: +siege", siege_ov)):
                cfg = WheelConfig(ticker=t, **BASE, **ov)
                res = run_wheel(ch, cfg, regime_states=states)
                rep = wheel_report(res, ch, cfg)
                log = position_log(res, cfg).dropna(subset=["realized_pnl"])
                shares_pnl = log[log.instrument == "SHARES"]["realized_pnl"].sum()
                lines.append(f"{label:<18} {res.equity.iloc[-1]-cfg.starting_capital:>10,.0f} "
                             f"{rep.metrics['sharpe']:>7.2f} "
                             f"{rep.metrics['max_drawdown']:>8.1%} "
                             f"{rep.stats['n_siege_exits']:>6} "
                             f"{rep.stats['days_shares_uncovered']:>10} "
                             f"{shares_pnl:>11,.0f}")
        cap = BASE["starting_capital"]
        bh = buy_hold_curve(ch, cap)
        lines.append(f"{'buy-hold ' + t:<18} {bh.iloc[-1] - cap:>10,.0f}")
    txt = "\n".join(lines)
    outd = Path("data/options/reports"); outd.mkdir(parents=True, exist_ok=True)
    fname = ("siege_exit.txt" if tickers == SEEN
             else f"siege_exit_{'_'.join(t.lower() for t in tickers)}.txt")
    (outd / fname).write_text(txt)
    print(txt)

if __name__ == "__main__":
    main()
