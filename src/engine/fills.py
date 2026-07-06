"""Rebalance accounting: targets -> whole-share trades at today's open.

Sells first (frees cash), then buys in sorted-ticker order (deterministic).
Buys are capped at available cash — never borrow (no leverage, house rule).
"""
import math

import pandas as pd


def execute_rebalance(positions, cash, targets, open_prices, slippage_bps):
    if sum(targets.values()) > 1.0 + 1e-9:
        raise ValueError(f"target weights sum to {sum(targets.values()):.4f} > 1.0")
    if any(w < 0 for w in targets.values()):
        raise ValueError("negative weight = shorting, not allowed in v1")
    for t in set(targets) | set(positions):
        if t not in open_prices.index or pd.isna(open_prices[t]):
            raise ValueError(f"no open price for {t}")

    positions = dict(positions)
    slip = slippage_bps / 10_000.0
    value = cash + sum(sh * open_prices[t] for t, sh in positions.items())

    target_shares = {
        t: math.floor(w * value / open_prices[t]) for t, w in targets.items()
    }
    # tickers held but absent from targets -> implicit target 0
    for t in list(positions):
        target_shares.setdefault(t, 0)

    trades = []
    # sells first
    for t in sorted(target_shares):
        diff = target_shares[t] - positions.get(t, 0)
        if diff < 0:
            price = open_prices[t] * (1 - slip)
            cash += -diff * price
            trades.append({"ticker": t, "side": "sell", "shares": -diff, "price": price})
            positions[t] = target_shares[t]
            if positions[t] == 0:
                del positions[t]
    # then buys, capped at cash
    for t in sorted(target_shares):
        # For buys, recalculate target using slipped price to get correct share count
        target_at_slip = math.floor(targets.get(t, 0) * value / (open_prices[t] * (1 + slip)))
        diff = target_at_slip - positions.get(t, 0)
        if diff > 0:
            price = open_prices[t] * (1 + slip)
            affordable = math.floor(cash / price)
            if affordable < diff:
                print(f"WARNING: {t} buy reduced {diff} -> {affordable} (cash)")
                diff = affordable
            if diff <= 0:
                continue
            cash -= diff * price
            trades.append({"ticker": t, "side": "buy", "shares": diff, "price": price})
            positions[t] = positions.get(t, 0) + diff
    return positions, cash, trades
