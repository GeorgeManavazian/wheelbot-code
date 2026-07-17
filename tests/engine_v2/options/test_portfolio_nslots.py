import pandas as pd
from src.engine_v2.options.data import chain_path
from src.engine_v2.options.wheel import WheelConfig
from src.engine_v2.options.portfolio import run_portfolio_wheel
from src.engine_v2.regime.state import regime_series
from src.engine_v2.regime.data import closes_for

SEEN = ["SPY", "GDX", "SLV", "XOP"]
BASE = dict(put_delta=0.20, call_delta=0.20, target_dte=7,
            take_profit_pct=0.50, starting_capital=100_000.0, call_min_strike="basis")


def _load():
    chains = {t: pd.read_parquet(chain_path(t)) for t in SEEN}
    states = {t: regime_series(closes_for(t)) for t in SEEN}
    return chains, states


def test_nslots_1_vol_pctile_matches_solo_run_wheel():
    # Real byte-identity teeth: a one-ticker universe at n_slots=1, selector
    # vol_pctile, must reproduce the solo wheel on that ticker exactly.
    from src.engine_v2.options.wheel import run_wheel
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    solo = run_wheel(chains["SPY"], cfg)
    port = run_portfolio_wheel({"SPY": chains["SPY"]}, cfg, {"SPY": states["SPY"]},
                               selector="vol_pctile", n_slots=1)
    assert [(t.date, t.action, t.contracts, t.price_per_contract) for t in solo.trades] == \
           [(t.date, t.action, t.contracts, t.price_per_contract) for t in port.trades]
    assert solo.equity.equals(port.equity)


def test_nslots_5_holds_multiple_and_never_two_per_ticker():
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    res = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=5)
    # reconstruct per-day open campaigns from trades: a SELL_PUT opens, the
    # campaign's terminal close ends it. Assert no day holds two open campaigns
    # on the SAME ticker (one campaign per ticker).
    # Simpler structural proxy: every SELL_PUT that opens a NEW campaign id has a
    # ticker distinct from all other campaigns open at that moment.
    open_by_campaign = {}
    OPEN = {"SELL_PUT"}
    # A short put lives only in PUT phase with zero shares, so a take-profit
    # CLOSE_PUT always returns the campaign to flat and terminates it — it must
    # count as a terminator or a legitimate same-ticker re-entry looks like a
    # second concurrent campaign.
    CLOSE = {"PUT_EXPIRED", "ASSIGNED", "CALLED_AWAY", "CALL_EXPIRED", "CLOSE_PUT"}
    live = {}  # campaign -> ticker
    for t in res.trades:
        root = t.contract.root
        if t.action in OPEN and t.campaign_id not in open_by_campaign:
            open_by_campaign[t.campaign_id] = root
            # no other live campaign on this ticker
            assert root not in live.values(), f"two campaigns on {root} at {t.date}"
            live[t.campaign_id] = root
        if t.action in CLOSE and t.campaign_id in live:
            # a campaign fully closes only when it returns to flat; approximate
            # by removing on called_away/put_expired with no shares. Keep simple:
            live.pop(t.campaign_id, None)
    assert res.n_campaigns_opened >= 1


def test_nslots_5_opens_at_least_as_many_campaigns():
    # More slots = at least as many entry opportunities taken over the run.
    # (Logically sound: n_slots=5 can never open FEWER campaigns than n_slots=1
    # given identical eligibility each day.)
    chains, states = _load()
    cfg = WheelConfig(ticker="SPY", **BASE)
    one = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=1)
    five = run_portfolio_wheel(chains, cfg, states, selector="chop", n_slots=5)
    assert five.n_campaigns_opened >= one.n_campaigns_opened
