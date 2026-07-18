"""Integration regression: a full wheel lifecycle (sell put -> assigned ->
covered call -> called away -> flat) driven through the LIVE paper_step path,
RELOADING state.json from disk between every day. Proves the live loop + JSON
persistence carry a real position across days with correct money accounting."""
import pandas as pd
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import PortfolioState
from live.run_daily import paper_step
from live.state import load_state

_COLS = ["date", "expiry", "strike", "right", "dte", "delta", "bid", "ask", "mid", "underlying"]
CFG = WheelConfig(ticker="TST", put_delta=0.30, call_delta=0.50, target_dte=11,
                  take_profit_pct=0.60, starting_capital=100_000.0, call_min_strike="basis")


def _chain(obs, rows):
    data = [{"date": obs, "expiry": e, "strike": s, "right": r, "dte": (e - obs).days,
             "delta": d, "bid": b, "ask": a, "mid": (b + a) / 2, "underlying": u}
            for (e, s, r, d, b, a, u) in rows]
    return pd.DataFrame(data, columns=_COLS)


class _M:
    universe = ["TST"]
    def __init__(self, obs, spot, chain_df):
        self._obs = pd.Timestamp(obs); self._spot = spot; self._c = chain_df
    def chain(self, tk, d): return self._c
    def spot(self, tk, d, fb): return self._spot
    def settle_price(self, tk, e): return None
    def regime_row(self, tk, d): return {"trend": "chop", "vol": "normal", "vol_pctile": 0.5}
    def eligible(self, tk, d): return True


def test_full_lifecycle_with_persistence(tmp_path):
    sp = str(tmp_path / "state.json"); tp = str(tmp_path / "trades.jsonl")
    state = PortfolioState(cash=100_000.0, positions=[])

    def day(obs, spot, chain_df):
        r = paper_step(state, _M(obs, spot, chain_df), CFG, n_slots=1,
                       trades_path=tp, state_path=sp)
        reloaded = load_state(sp)   # persistence: reload must match in-memory
        assert reloaded is not None
        assert abs(reloaded.cash - state.cash) < 1e-6
        assert len(reloaded.positions) == len(state.positions)
        return [t.action for t in r.trades]

    d1, exp1 = pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-16")
    assert day(d1, 22.0, _chain(d1, [(exp1, 20.0, "P", -0.30, 1.00, 1.10, 22.0)])) == ["SELL_PUT"]
    assert state.positions[0]["short"]["contract"].strike == 20.0

    assert day(exp1, 18.0, _chain(exp1, [])) == ["ASSIGNED"]   # spot<strike -> assigned
    assert state.positions[0]["shares"] > 0 and state.positions[0]["phase"] == "CALL"

    d3, exp2 = pd.Timestamp("2026-01-20"), pd.Timestamp("2026-01-31")
    assert day(d3, 21.0, _chain(d3, [(exp2, 22.0, "C", 0.50, 0.80, 0.90, 21.0)])) == ["SELL_CALL"]

    assert day(exp2, 25.0, _chain(exp2, [])) == ["CALLED_AWAY"]  # spot>call strike
    assert len(state.positions) == 0                            # flat

    # a wheel cycle ending called-away above basis must net positive
    assert state.cash > 100_000
    lines = [x for x in open(tp) if x.strip()]
    assert len(lines) == 4      # SELL_PUT, ASSIGNED, SELL_CALL, CALLED_AWAY
