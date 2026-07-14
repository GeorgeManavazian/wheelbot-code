"""Wheel-campaign autopsy by regime: the campaign ledger grouped by the market
state (and the traded ticker's state) each campaign opened into."""
from __future__ import annotations
import pandas as pd
from ..options.report import campaign_table
from .state import regime_series

def _tag(dates, states: pd.DataFrame, col: str) -> list:
    out = []
    for d in dates:
        d = pd.Timestamp(d)
        prior = states.loc[:d]
        out.append(prior.iloc[-1][col] if len(prior) else "unknown")
    return out

def campaign_regimes(result, cfg, market_closes: pd.Series,
                     ticker_closes: pd.Series) -> pd.DataFrame:
    ct = campaign_table(result, cfg)
    mkt, tkr = regime_series(market_closes), regime_series(ticker_closes)
    ct = ct.copy()
    for prefix, states in (("market", mkt), ("ticker", tkr)):
        ct[f"{prefix}_trend"] = _tag(ct["opened"], states, "trend")
        ct[f"{prefix}_vol"] = _tag(ct["opened"], states, "vol")
    return ct

def autopsy_table(campaigns: pd.DataFrame, by: str = "market") -> pd.DataFrame:
    closed = campaigns[~campaigns["open_at_end"]]
    rows = []
    for (trend, vol), g in closed.groupby([f"{by}_trend", f"{by}_vol"]):
        rows.append(dict(trend=trend, vol=vol, n_campaigns=len(g),
                         win_rate=float((g["pnl"] > 0).mean()),
                         total_pnl=float(g["pnl"].sum()),
                         mean_pnl=float(g["pnl"].mean()),
                         n_rolls=int(g["n_rolls"].sum())))
    out = pd.DataFrame(rows, columns=["trend","vol","n_campaigns","win_rate",
                                      "total_pnl","mean_pnl","n_rolls"])
    out.attrs["n_open_excluded"] = int(campaigns["open_at_end"].sum())
    return out
