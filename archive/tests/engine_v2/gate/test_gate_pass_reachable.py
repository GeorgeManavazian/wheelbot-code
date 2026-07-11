"""The gate's success path. Every pre-existing verdict test asserts pass is False,
so `is_pass` shipped unreachable. These tests pin the passing branch.

Semantics per Lopez de Prado Ch.8 (see vault note "Lopez de Prado 08 Testing Set
Overfitting"): FWER is alpha_K = 1 - (1-alpha)^E[K] where alpha is the strategy's
OWN observed single-trial p-value, alpha = Z[-z_hat[0]] -- not a constant.
"""
import numpy as np
import pandas as pd
import pytest

from src.engine_v2.gate.dsr import zstat, single_trial_alpha
from src.engine_v2.gate.fwer import fwer
from src.engine_v2.gate.verdict import compute_verdict


def _regimes(idx):
    return pd.DataFrame(
        {"regime_trend": ["bull"] * len(idx), "regime_vol": ["low"] * len(idx)},
        index=idx,
    )


def _trials(n_trials=5, T=1250, daily_sr=0.08, seed=0):
    """n_trials near-identical profitable return series with daily Sharpe ~= daily_sr."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-01-01", periods=T, freq="B")
    sd = 0.004
    base = rng.normal(daily_sr * sd, sd, T)
    base = (base - base.mean()) / base.std(ddof=1) * sd + daily_sr * sd  # pin the SR
    cols = {f"t{i}": base + rng.normal(0, sd * 1e-4, T) for i in range(n_trials)}
    return pd.DataFrame(cols, index=idx), idx


def test_zstat_reproduces_lopez_worked_example():
    """LdP p.118: SR=0.0791, T=1250, skew=-3, kurtosis=10 -> single-test alpha ~= 0.0062."""
    z = zstat(sr=0.0791, T=1250, skew=-3.0, kurt_excess=7.0)  # excess = 10 - 3
    assert single_trial_alpha(z) == pytest.approx(0.0062, abs=5e-4)


def test_fwer_reproduces_lopez_worked_example():
    """LdP p.118: that same alpha across E[K]=10 effective trials -> alpha_K ~= 0.0608."""
    z = zstat(sr=0.0791, T=1250, skew=-3.0, kurt_excess=7.0)
    alpha = single_trial_alpha(z)
    assert fwer(10, alpha) == pytest.approx(0.0608, abs=1e-3)


def test_lopez_example_fails_the_gate_threshold():
    """LdP's own example lands just above 0.05 -- it must NOT pass. Guards against
    a fix that makes the gate permissive."""
    z = zstat(sr=0.0791, T=1250, skew=-3.0, kurt_excess=7.0)
    assert fwer(10, single_trial_alpha(z)) > 0.05


def test_oracle_strategy_passes_the_gate():
    """A genuinely strong strategy -- high DSR, small FWER, no regime kill, Calmar 3 --
    must return pass=True. This is the branch that never executed."""
    trials, idx = _trials()
    v = compute_verdict(trials, _regimes(idx), calmar_overall=3.0)
    assert v["pass"] is True, f"oracle shelved: {v}"
    assert v["shelf"] is False
    assert v["dsr"] > 0.95
    assert v["fwer"] < 0.05


def test_strong_dsr_but_weak_calmar_is_watch_not_shelf():
    """DSR above the pass line must never fall through to shelf. Shelf is for dead
    strategies (DSR < 0.80), not for great ones that missed one condition."""
    trials, idx = _trials()
    v = compute_verdict(trials, _regimes(idx), calmar_overall=0.5)
    assert v["pass"] is False
    assert v["watch"] is True, f"strong DSR landed in shelf: {v}"
    assert v["shelf"] is False


DEGENERATE = [0.009, 0.009, 0.011, 0.011]
"""Bimodal, n=4. The bias-corrected sample excess kurtosis (-6.0) falls below the
Pearson floor (skew^2 - 2), so the Mertens variance term goes negative. For realistic
n the term is a perfect square and this cannot happen."""


def test_deflated_sharpe_fails_closed_on_degenerate_moments():
    """A non-positive variance term must shelve, not certify. Old behaviour: 1.0."""
    from src.engine_v2.gate.dsr import deflated_sharpe

    assert deflated_sharpe(pd.Series(DEGENERATE), K_effective=2) == 0.0


def test_observed_alpha_fails_closed_on_degenerate_moments():
    """alpha=0.0 means 'certainly significant'. Degenerate moments must give alpha=1.0."""
    from src.engine_v2.gate.dsr import observed_alpha

    assert observed_alpha(pd.Series(DEGENERATE)) == 1.0


def test_verdict_shelves_degenerate_moments_with_a_note():
    idx = pd.date_range("2010-01-01", periods=4, freq="B")
    # scale each column slightly: SR/skew/kurt are scale-invariant, so every column stays
    # degenerate, but the columns are distinct points for the clusterer.
    trials = pd.DataFrame(
        {f"t{i}": [v * (1 + i * 1e-6) for v in DEGENERATE] for i in range(5)}, index=idx
    )
    v = compute_verdict(trials, _regimes(idx), calmar_overall=99.0)
    assert v["pass"] is False
    assert v["shelf"] is True
    assert v["notes"].get("degenerate_moments") is True


def _regime_split(bull_ret, bear_ret, bull_frac, noise, n=400, seed=3):
    """Two regime cells. The bear cell must hold >=20% of the sample and post a
    negative Sharpe to trip regime_kill (see gate/regime_eval.py)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    n_bull = int(n * bull_frac)
    trend = ["bull"] * n_bull + ["bear"] * (n - n_bull)
    ret = np.where(np.array(trend) == "bull", bull_ret, bear_ret)
    trials = pd.DataFrame(
        {f"t{i}": ret + rng.normal(0, noise, n) for i in range(6)}, index=idx
    )
    reg = pd.DataFrame(
        {"regime_trend": trend, "regime_vol": ["calm"] * n, "regime_rate": ["falling"] * n},
        index=idx,
    )
    return trials, reg


def test_regime_kill_with_weak_dsr_shelves():
    """Owner decision 2026-07-09: shelf is a floor on DSR. regime_kill cannot rescue noise."""
    trials, reg = _regime_split(bull_ret=0.002, bear_ret=-0.002, bull_frac=0.5, noise=5e-4)
    v = compute_verdict(trials, reg, calmar_overall=1.2)
    assert v["regime_kill"] is True
    assert v["dsr"] < 0.80
    assert v["shelf"] is True, f"weak regime-killed strategy escaped shelf: {v}"
    assert v["watch"] is False


def test_regime_kill_with_strong_dsr_watches():
    """...but regime_kill on a genuinely strong strategy still lands in WATCH, not shelf."""
    trials, reg = _regime_split(bull_ret=0.004, bear_ret=-0.0003, bull_frac=0.75, noise=6e-4)
    v = compute_verdict(trials, reg, calmar_overall=1.2)
    assert v["regime_kill"] is True
    assert v["dsr"] >= 0.80
    assert v["pass"] is False, "regime_kill must block promotion"
    assert v["watch"] is True, f"strong regime-killed strategy fell to shelf: {v}"


def test_fwer_requires_an_explicit_alpha():
    """The original bug was a caller silently taking a default alpha=0.05, which made
    fwer() a function of K alone. alpha must be passed deliberately."""
    with pytest.raises(TypeError):
        fwer(5)  # type: ignore[call-arg]


def test_coinflip_strategy_still_shelves():
    """Regression guard: a no-edge strategy must stay in shelf."""
    rng = np.random.default_rng(7)
    idx = pd.date_range("2010-01-01", periods=1250, freq="B")
    base = rng.normal(0.0, 0.004, 1250)
    trials = pd.DataFrame(
        {f"t{i}": base + rng.normal(0, 4e-7, 1250) for i in range(5)}, index=idx
    )
    v = compute_verdict(trials, _regimes(idx), calmar_overall=0.1)
    assert v["shelf"] is True
    assert v["pass"] is False
