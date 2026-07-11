"""Pure strike-selection and marking primitives over an OptionsChain frame.
No I/O, no engine state — consumed by the Wheel engine (sub-project 2)."""
from __future__ import annotations
from .chain import Contract, Mark

def select_strike_by_delta(chain, date, right, target_delta, dte_min, dte_max):
    m = ((chain["date"] == date) & (chain["right"] == right)
         & (chain["dte"] >= dte_min) & (chain["dte"] <= dte_max))
    cand = chain[m]
    if cand.empty:
        return None
    err = (cand["delta"].abs() - abs(target_delta)).abs()
    # nearest target delta; tie -> further OTM = smaller |delta|
    order = cand.assign(_e=err).sort_values(["_e", "delta"], key=lambda s: s.abs()
                        if s.name == "delta" else s)
    row = order.iloc[0]
    return Contract("SPY", row["expiry"], float(row["strike"]), right)

def option_mark(chain, date, contract):
    m = ((chain["date"] == date) & (chain["expiry"] == contract.expiry)
         & (chain["strike"] == contract.strike) & (chain["right"] == contract.right))
    r = chain[m]
    if r.empty:
        return None
    row = r.iloc[0]
    return Mark(float(row["bid"]), float(row["ask"]), float(row["mid"]))

def intrinsic_value(right, strike, underlying):
    return max(strike - underlying, 0.0) if right == "P" else max(underlying - strike, 0.0)

def expiry_underlying(chain, contract):
    r = chain[chain["date"] == contract.expiry]
    if r.empty:
        return None
    return float(r.iloc[0]["underlying"])
