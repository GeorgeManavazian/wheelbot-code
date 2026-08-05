"""Earnings blackout A/B (spec 2026-08-03-earnings-blackout-design).

Live frozen config (0.30 put / 0.50 call / 60% TP / DTE 11 / basis), chop
selector, EOD. One variable: earnings_blackout OFF vs ON. Everything else held.

The gate can only bite on names that HAVE earnings, so the default universe is
the equities in the live universe that also have a local chain parquet AND an
`ok` row in the earnings cache. ETFs are reported separately as a control: the
gate is provably inert on them, so any difference in their arm is a bug in this
harness, not a finding.

Pre-registered outputs, in the spec's order of importance:
  1. entries BLOCKED (and as a share of all entry attempts) -- if ~0, inert
  2. plain P&L both arms
  3. ASSIGNMENTS both arms -- the mechanism check; the gate's whole claim is
     that it avoids gap-down assignments
  4. flat days both arms -- the cost side

Raw. No interpretation, no kill rule -- owner judges (standing convention).

Run: PYTHONPATH=. .venv/bin/python scripts/run_earnings_ab.py [--max-tickers N]
"""
import argparse
import os
from pathlib import Path

import pandas as pd

from src.engine_v2.backtest import metrics_simple as m
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.earnings import EarningsCalendar
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.regime.data import closes_for
from src.engine_v2.regime.state import regime_series

LIVE = dict(put_delta=0.30, call_delta=0.50, target_dte=11,
            take_profit_pct=0.60, starting_capital=100_000.0,
            call_min_strike="basis", chop_max_fast_fall=0.01)
N_SLOTS = 5


def perf(eq):
    ppy = m.infer_periods_per_year(eq.index)
    rets = eq.pct_change().fillna(0.0)
    return (float(eq.iloc[-1] / eq.iloc[0] - 1), m.cagr(eq, ppy),
            m.sharpe(rets, ppy), m.max_drawdown(eq))


def counts(port):
    acts = [t.action for t in port.trades]
    w = port.warnings or []
    return {
        "pnl": float(port.equity.iloc[-1] - LIVE["starting_capital"]),
        "entries": acts.count("SELL_PUT"),
        "assigned": acts.count("ASSIGNED"),
        "called_away": acts.count("CALLED_AWAY"),
        "blocked": sum(1 for x in w if x[1] == "entry_gated_earnings"),
        "flat": port.days_flat,
        "camps": port.n_campaigns_opened,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-tickers", type=int, default=60,
                    help="cap the universe (chain parquets are large)")
    args = ap.parse_args()

    cal = EarningsCalendar.load()
    if len(cal) == 0:
        raise SystemExit("No earnings cache. Run scripts/pull_earnings.py first.")

    from live.universe import UNIVERSE

    with_chains = [t for t in UNIVERSE if os.path.exists(chain_path(t))]
    equities = [t for t in with_chains if cal.dates(t)]           # has prints
    funds = [t for t in with_chains if cal.dates(t) == []]        # known-none
    blind = [t for t in with_chains if cal.dates(t) is None]      # NOT counted as either

    picked = equities[:args.max_tickers]
    if not picked:
        raise SystemExit("No equities with both a chain and an earnings calendar.")

    L = ["Earnings blackout A/B — live config (0.30/0.50/60%/DTE11/basis, "
         f"down-only chop gate), N={N_SLOTS}, EOD, raw.",
         "",
         f"universe with local chains : {len(with_chains)}",
         f"  equities w/ earnings     : {len(equities)}  (running the first {len(picked)})",
         f"  funds (gate provably inert): {len(funds)}",
         f"  BLIND — no calendar      : {len(blind)}"
         + (f"  {blind[:15]}" if blind else ""),
         ""]
    if blind:
        # Loud: the gate cannot see these names, so an A/B that includes them
        # understates the gate's effect by an unknown amount.
        L.append("WARNING: blind tickers are ungated regardless of the flag. "
                 "Re-run scripts/pull_earnings.py before citing these numbers.")
        L.append("")

    chains = {t: pd.read_parquet(chain_path(t)) for t in picked}
    states = {t: regime_series(closes_for(t)) for t in picked}

    L.append(f"{'arm':<14} {'P&L':>11} {'total':>8} {'Sharpe':>7} {'maxDD':>7} "
             f"{'entries':>8} {'blocked':>8} {'assign':>7} {'called':>7} {'flat':>6}")

    rows = {}
    for name, gate in (("blackout OFF", False), ("blackout ON", True)):
        port = run_portfolio_wheel(
            chains, WheelConfig(ticker=picked[0], **LIVE, earnings_blackout=gate),
            states, selector="chop", n_slots=N_SLOTS, universe=picked,
            earnings=cal if gate else None)
        c = counts(port)
        rows[name] = c
        tot, _, sh, dd = perf(port.equity)
        L.append(f"{name:<14} {c['pnl']:>11,.0f} {tot:>8.1%} {sh:>7.2f} {dd:>7.1%} "
                 f"{c['entries']:>8} {c['blocked']:>8} {c['assigned']:>7} "
                 f"{c['called_away']:>7} {c['flat']:>6}")

    off, on = rows["blackout OFF"], rows["blackout ON"]
    attempts = on["entries"] + on["blocked"]
    L += ["",
          "Pre-registered readouts:",
          f"  1. blocked            {on['blocked']} of {attempts} entry attempts "
          f"({(on['blocked'] / attempts if attempts else 0):.1%})",
          f"  2. P&L delta          {on['pnl'] - off['pnl']:+,.0f}",
          f"  3. assignments        {off['assigned']} -> {on['assigned']} "
          f"({on['assigned'] - off['assigned']:+d})   <- the mechanism check",
          f"  4. flat days          {off['flat']} -> {on['flat']} "
          f"({on['flat'] - off['flat']:+d})   <- the cost side",
          "",
          "In-sample, one config, no sweep. Look-ahead disclosure (rescheduled "
          "prints) in the spec — repeat it wherever these numbers are cited."]

    txt = "\n".join(L)
    Path("data/options/reports").mkdir(parents=True, exist_ok=True)
    Path("data/options/reports/earnings_ab.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
