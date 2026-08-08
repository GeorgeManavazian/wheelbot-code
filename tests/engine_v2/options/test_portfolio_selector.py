import os
import pandas as pd
import pytest
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.path.exists(chain_path("SPY")),
                       reason="chain data not pulled (gitignored) -- runs locally, skipped in CI"),
]

SEEN =["SPY", "GDX", "SLV", "XOP"]
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _load():
    chains = {t: pd.read_parquet(chain_path(t)) for t in SEEN}
    states = {t: regime_series(closes_for(t)) for t in SEEN}
    return chains, states


def test_default_selector_is_vol_pctile_and_unchanged():
    # The default (vol_pctile) run must equal an explicit vol_pctile run.
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    a = run_portfolio_wheel(chains, cfg, states)
    b = run_portfolio_wheel(chains, cfg, states, selector="vol_pctile")
    assert list(a.equity) == list(b.equity)
    assert len(a.trades) == len(b.trades)


def test_chop_selector_changes_selection():
    # The chop selector routes differently (it excludes uptrends/downtrends),
    # so its equity path is not identical to the vol_pctile path.
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    vp = run_portfolio_wheel(chains, cfg, states, selector="vol_pctile")
    chop = run_portfolio_wheel(chains, cfg, states, selector="chop")
    assert list(vp.equity) != list(chop.equity)


def test_bad_selector_raises():
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    try:
        run_portfolio_wheel(chains, cfg, states, selector="nope")
        assert False, "expected ValueError"
    except ValueError:
        pass
