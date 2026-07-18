import json
import pandas as pd
from live.data import closes_from_json, chain_from_json
from live.market_live import LiveMarket
from live.run_daily import paper_step
from live.state import load_state
from src.engine_v2.options.portfolio import PortfolioState
from src.engine_v2.options.wheel import WheelConfig

OBS = pd.Timestamp("2026-07-17")
PH = json.load(open("live/fixtures/price_history_gdx.json"))
OC = json.load(open("live/fixtures/option_chain_gdx_puts.json"))


def test_paper_step_persists_state_and_trades(tmp_path):
    m = LiveMarket(["GDX"], set(), OBS,
                   closes_fn=lambda tk: closes_from_json(PH),
                   chain_fn=lambda tk: chain_from_json(OC, OBS))
    cfg = WheelConfig(ticker="GDX", put_delta=0.30, call_delta=0.50, target_dte=11,
                      take_profit_pct=0.60, starting_capital=100_000.0,
                      call_min_strike="basis")
    state = PortfolioState(cash=100_000.0, positions=[])
    sp = str(tmp_path / "state.json"); tp = str(tmp_path / "trades.jsonl")
    r = paper_step(state, m, cfg, n_slots=1, trades_path=tp, state_path=sp)
    # state.json written and reloadable
    reloaded = load_state(sp)
    assert reloaded is not None and reloaded.prev_d == OBS
    # each trade is one JSON line in trades.jsonl
    with open(tp) as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == len(r.trades)
    if lines:
        assert {"date", "action", "ticker"} <= set(lines[0])
