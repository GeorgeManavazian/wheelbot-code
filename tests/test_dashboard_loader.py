import pandas as pd

from dashboard import loader

CSV = """label,name,lookback,cagr,max_dd,sharpe,n_trades,turnover,exposure,positive_years,total_years,sample_flag,error
ts_trend(lookback=63),ts_trend,63,0.08,-0.15,0.9,120,1.5,0.7,8,10,OK,
ts_trend(lookback=125),ts_trend,125,,,,,,,,,,"ValueError: boom"
"""


def _write(tmp_path, name="leaderboard_20260705-120000_abc1234.csv"):
    p = tmp_path / name
    p.write_text(CSV)
    return p


def test_list_leaderboards_empty_and_missing(tmp_path):
    assert loader.list_leaderboards(tmp_path) == []
    assert loader.list_leaderboards(tmp_path / "nope") == []


def test_list_leaderboards_newest_first(tmp_path):
    old = _write(tmp_path, "leaderboard_20260701-000000_aaa.csv")
    new = _write(tmp_path, "leaderboard_20260705-000000_bbb.csv")
    assert loader.list_leaderboards(tmp_path) == [new, old]


def test_load_and_split(tmp_path):
    lb = loader.load_leaderboard(_write(tmp_path))
    assert list(lb["error"]) == ["", "ValueError: boom"]  # NaN became ""
    ok, bad = loader.split_errors(lb)
    assert len(ok) == 1 and ok.iloc[0]["label"] == "ts_trend(lookback=63)"
    assert len(bad) == 1 and "boom" in bad.iloc[0]["error"]
