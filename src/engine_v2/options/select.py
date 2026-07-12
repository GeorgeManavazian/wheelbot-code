"""Pure strike-selection and marking primitives over an OptionsChain frame.
No I/O, no engine state — consumed by the Wheel engine (sub-project 2)."""
from __future__ import annotations
from .chain import Contract, Mark

def derived_band(target_dte: int) -> tuple[int, int]:
    """DTE guard rail derived from the target. Floor rejects expiry stubs;
    ceiling rejects a monthly when the weekly is absent. Display-only upstream."""
    return max(5, target_dte - 2), target_dte + 3

def select_contract(chain, date, right, target_delta, target_dte, root):
    """Expiry FIRST (nearest target_dte within derived_band, from expiries visible
    on `date` only), THEN strike (nearest |delta| within that one expiry).
    Deterministic; returns None -> sit in cash."""
    lo, hi = derived_band(target_dte)
    cand = chain[(chain["date"] == date) & (chain["right"] == right)]
    if cand.empty:
        return None
    dtes = cand.groupby("expiry")["dte"].first()
    dtes = dtes[(dtes >= lo) & (dtes <= hi)]
    if dtes.empty:
        return None
    err = (dtes - target_dte).abs()
    best_exp = dtes[err == err.min()].index.max()   # tie -> longer-dated
    e = cand[cand["expiry"] == best_exp]
    row = e.loc[(e["delta"].abs() - abs(target_delta)).abs().idxmin()]
    return Contract(root, row["expiry"], float(row["strike"]), right)

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
