"""Pure strike-selection and marking primitives over an OptionsChain frame.
No I/O, no engine state — consumed by the Wheel engine (sub-project 2)."""
from __future__ import annotations
import pandas as pd
from .chain import Contract, Mark

def derived_band(target_dte: int) -> tuple[int, int]:
    """DTE guard rail derived from the target. Floor rejects expiry stubs;
    ceiling rejects a monthly when the weekly is absent. Display-only upstream."""
    return max(5, target_dte - 2), target_dte + 3

def select_contract(chain, date, right, target_delta, target_dte, root, min_strike=None):
    """Expiry FIRST (nearest target_dte within derived_band, from expiries visible
    on `date` only), THEN strike (nearest |delta| within that one expiry).
    With `min_strike`, expiries are tried in nearest-DTE order (tie -> longer-
    dated) and the first one containing a strike >= min_strike is used; only
    when no in-band expiry qualifies -> None. Deterministic; None -> sit in cash."""
    lo, hi = derived_band(target_dte)
    cand = chain[(chain["date"] == date) & (chain["right"] == right)]
    if cand.empty:
        return None
    dtes = cand.groupby("expiry")["dte"].first()
    dtes = dtes[(dtes >= lo) & (dtes <= hi)]
    if dtes.empty:
        return None
    err = (dtes - target_dte).abs()
    for exp in sorted(dtes.index, key=lambda e: (err[e], -dtes[e])):
        e = cand[cand["expiry"] == exp]
        if min_strike is not None:
            e = e[e["strike"] >= min_strike]
            if e.empty:
                continue
        row = e.loc[(e["delta"].abs() - abs(target_delta)).abs().idxmin()]
        return Contract(root, row["expiry"], float(row["strike"]), right)
    return None

def select_roll_contract(chain, date, right, strike, current_expiry, target_dte, root):
    """Roll destination (repair spec amendment 2026-07-13b): the canonical
    credit roll is SAME STRIKE, out in time. Candidates are expiries STRICTLY
    beyond the held leg's expiry that carry the held strike, nearest to
    (current_expiry + target_dte) — one config cycle further out; tie ->
    longer-dated. Deterministic; None -> no roll today."""
    cand = chain[(chain["date"] == date) & (chain["right"] == right)
                 & (chain["strike"] == strike)]
    if cand.empty:
        return None
    exps = cand.groupby("expiry")["dte"].first()
    exps = exps[exps.index > pd.Timestamp(current_expiry)]
    if exps.empty:
        return None
    anchor = pd.Timestamp(current_expiry) + pd.Timedelta(days=target_dte)
    best = sorted(exps.index, key=lambda e: (abs((e - anchor).days), -exps[e]))[0]
    return Contract(root, best, float(strike), right)

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
