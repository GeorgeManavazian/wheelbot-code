"""End-to-end backtest orchestrator: composes loop + execution + guardrails
+ CPCV + gate. This is the single entrypoint that wires v2's modules together
so every strategy sees the same honesty pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd
from ..strategy.protocol import validate_plugin
from ..sizing.carver import size_position, DEFAULT_TARGET_RISK
from ..sizing.guardrails import (
    check_speed_limit, enforce_account_cap, SpeedLimitViolation,
    HARD_ACCOUNT_CAP,
)
from ..execution.sim import Order, fill_order
from ..execution.shorting import HTBRegistry, apply_short_guard
from .cpcv import make_folds, cpcv_combos, purged_train_index
from .loop import expand_trials
from ..gate.verdict import compute_verdict
from ..data.regime import tag_regime

STARTING_EQUITY = 100_000.0


@dataclass
class BacktestConfig:
    target_risk: float = DEFAULT_TARGET_RISK
    n_folds: int = 10
    cpcv_k: int = 2
    starting_equity: float = STARTING_EQUITY
    htb: HTBRegistry = field(default_factory=HTBRegistry)


def _fill_at_close_via_sim(ticker: str, side: str, qty: float,
                           close: float, adv: float,
                           date: pd.Timestamp, htb: HTBRegistry):
    """Route through execution/sim. Uses close as both bid and ask
    (single price bar limitation) but the same-code-path invariant holds."""
    o = Order(ticker=ticker, side=side, kind="market", qty=qty)
    o = apply_short_guard(o, date, htb)
    if o is None:
        return None
    return fill_order(o, bid=close, ask=close, adv=adv)


def _fold_trial_returns(strategy_cls, params, bars, test_index, cfg: BacktestConfig):
    """Run one CPCV fold. Applies sizing via size_position, then fills through
    execution/sim, then rolls equity forward. Returns per-date returns series."""
    strat = strategy_cls(**params)
    equity = cfg.starting_equity
    equity_series = []
    prev_equity = equity
    for asof in test_index:
        visible = bars.loc[:asof]
        fc = strat.forecast(visible, asof)
        for tkr, f in fc.items():
            if tkr not in bars.columns.get_level_values(0):
                continue
            close = float(bars[tkr]["Close"].loc[asof])
            past = bars[tkr]["Close"].loc[:asof].pct_change().dropna().tail(60)
            sigma = float(past.std() * (252 ** 0.5)) if len(past) >= 20 else 0.20
            notional = size_position(float(f), equity, sigma,
                                     target_risk=cfg.target_risk)
            side = "buy" if notional > 0 else "sell"
            qty = abs(notional) / close if close > 0 else 0.0
            adv = 1e9  # fixture proxy; real ingest supplies per-ticker ADV
            fill = _fill_at_close_via_sim(tkr, side, qty, close, adv, asof, cfg.htb)
            if fill is not None:
                equity -= fill.slippage_bps / 1e4 * abs(notional)
        equity_series.append((asof, equity))
        prev_equity = equity
    df = pd.DataFrame(equity_series, columns=["asof", "equity"]).set_index("asof")
    df["ret"] = df["equity"].pct_change().fillna(0.0)
    return df["ret"]


def run_backtest(strategy_cls, bars: pd.DataFrame,
                 regime_labels: pd.DataFrame | None = None,
                 config: BacktestConfig | None = None) -> dict:
    """End-to-end: validate plugin -> CPCV folds -> per-fold sized fills ->
    aggregate trial returns -> compute_verdict.

    Returns the verdict dict from src.engine_v2.gate.verdict.compute_verdict.
    """
    validate_plugin(strategy_cls)
    cfg = config or BacktestConfig()
    enforce_account_cap(cfg.target_risk)

    if regime_labels is None:
        regime_labels = tag_regime(bars)

    parameter_grid = getattr(strategy_cls, "parameter_grid", {}) or {}
    folds = make_folds(bars.index, n_folds=cfg.n_folds)
    combos = cpcv_combos(cfg.n_folds, cfg.cpcv_k)
    fold_ids = list(range(len(combos)))
    trials = expand_trials(strategy_cls, parameter_grid, fold_ids=fold_ids)

    holding = getattr(strategy_cls, "holding_period_cap", 5)
    trial_returns: dict[str, pd.Series] = {}
    for t in trials:
        combo = combos[t["fold_id"]]
        test_folds = [folds[c] for c in combo]
        test_index = pd.DatetimeIndex(sorted(set().union(*test_folds)))
        rets = _fold_trial_returns(strategy_cls, t["params"], bars, test_index, cfg)
        trial_returns[t["trial_id"]] = rets

    trm = pd.DataFrame(trial_returns).reindex(bars.index).fillna(0.0)
    aligned_regime = regime_labels.reindex(trm.index).ffill()
    # crude calmar approximation from best trial
    best = trm.mean().idxmax()
    best_series = trm[best]
    equity_curve = (1 + best_series).cumprod()
    if len(equity_curve) > 1:
        cagr = float(equity_curve.iloc[-1] ** (252.0 / len(equity_curve)) - 1)
        drawdown = float((equity_curve / equity_curve.cummax() - 1).min())
        calmar = cagr / abs(drawdown) if drawdown < 0 else float("inf")
    else:
        calmar = 0.0
    return compute_verdict(trm, aligned_regime, calmar_overall=calmar)
