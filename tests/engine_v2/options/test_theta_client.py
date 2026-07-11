import pandas as pd
import pytest
from src.engine_v2.options.theta_client import ThetaClient, ThetaError

class _Resp:
    def __init__(self, code, body): self.status = code; self._b = body.encode()
    def read(self): return self._b
    def getcode(self): return self.status
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_get_csv_parses(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open", lambda url: _Resp(200, "a,b\n1,2\n3,4\n"))
    df = c.get_csv("/v3/x", symbol="SPY")
    assert list(df.columns) == ["a", "b"]
    assert df.iloc[1]["a"] == 3

def test_get_csv_raises_on_error(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open", lambda url: _Resp(404, "Not Found\n..."))
    with pytest.raises(ThetaError):
        c.get_csv("/v3/x")

def test_list_expirations_parses(monkeypatch):
    c = ThetaClient()
    monkeypatch.setattr(c, "_open",
        lambda url: _Resp(200, 'symbol,expiration\n"SPY","2024-01-19"\n"SPY","2024-02-16"\n'))
    exps = c.list_expirations("SPY")
    assert exps == [pd.Timestamp("2024-01-19"), pd.Timestamp("2024-02-16")]
