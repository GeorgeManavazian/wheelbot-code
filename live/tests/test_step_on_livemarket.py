import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from src.engine_v2.options.portfolio import PortfolioState, step_one_day
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def test_step_runs_on_livemarket_end_to_end():
    m = LiveMarket(["GDX"], set(), OBS,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: chain_from_json(OC, OBS))
    cfg = WheelConfig(ticker="GDX", put_delta=0.30, call_delta=0.50,
                      target_dte=11, take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    r = step_one_day(state, m, OBS, cfg, selector="chop", n_slots=1)
    # it ran without error and marked equity; state advanced its date
    assert isinstance(r.trades, list)
    assert isinstance(r.equity, float)
    assert state.prev_d == OBS
