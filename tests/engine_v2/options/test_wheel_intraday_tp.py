import pandas as pd, pytest
from src.engine_v2.options.wheel import WheelConfig, run_wheel

COLS = ["date","expiry","dte","strike","right","bid","ask","mid","close","delta","iv","underlying"]
def _chain(rows):
    ch = pd.DataFrame(rows, columns=COLS)
    ch["date"]=pd.to_datetime(ch["date"]); ch["expiry"]=pd.to_datetime(ch["expiry"]); return ch

CFG = dict(starting_capital=50_000.0, put_delta=0.30, target_dte=17, take_profit_pct=0.50, commission_per_contract=0.0)

def test_intraday_tp_fires_before_eod():
    # sell 470 put @2.00 credit on d0; on d1 EOD ask is 1.80 (NO EOD TP: >1.00), but
    # intraday close dips to 0.90 at 11:30 -> intraday TP fires there at 0.90.
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",1.75,1.80,1.775,1.775,-0.25,0.1,473.0],
    ]
    intraday = {(pd.Timestamp("2024-01-19"),470.0,"P"): pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-03 10:30","2024-01-03 11:30","2024-01-03 12:30"]),
        "close": [1.60, 0.90, 1.10]})}
    res = run_wheel(_chain(rows), WheelConfig(**CFG), intraday=intraday)
    acts = [t.action for t in res.trades]
    assert acts[:2] == ["SELL_PUT","CLOSE_PUT"]
    close_tr = [t for t in res.trades if t.action=="CLOSE_PUT"][0]
    assert close_tr.price_per_contract == pytest.approx(0.90)          # filled at intraday close
    assert close_tr.date == pd.Timestamp("2024-01-03 11:30")           # stamped intraday
    assert res.final_cash == pytest.approx(50_000 + 200 - 90)          # +credit -90 buyback

def test_intraday_none_matches_eod():
    rows = [
        ["2024-01-02","2024-01-19",17,470,"P",2.00,2.10,2.05,2.05,-0.30,0.1,472.0],
        ["2024-01-03","2024-01-19",16,470,"P",0.80,0.90,0.85,0.85,-0.15,0.1,476.0],
    ]
    ch = _chain(rows)
    a = run_wheel(ch, WheelConfig(**CFG))
    b = run_wheel(ch, WheelConfig(**CFG), intraday=None)
    assert [t.action for t in a.trades] == [t.action for t in b.trades]
    assert a.final_cash == b.final_cash
