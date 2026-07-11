"""Thin REST client for the local ThetaData v3 terminal (CSV responses).
See docs/thetadata-v3-access.md. Isolated from the equity engine and the gate."""
from __future__ import annotations
import io
import urllib.parse, urllib.request
import pandas as pd

class ThetaError(RuntimeError):
    pass

def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")

class ThetaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:25503", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _open(self, url: str):  # seam for tests
        return urllib.request.urlopen(url, timeout=self.timeout)

    def get_csv(self, path: str, **params) -> pd.DataFrame:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}" + (f"?{qs}" if qs else "")
        with self._open(url) as r:
            code = getattr(r, "status", None) or r.getcode()
            body = r.read().decode("utf-8", "replace")
        if code != 200:
            first = body.splitlines()[0] if body else ""
            raise ThetaError(f"HTTP {code} for {path}: {first}")
        return pd.read_csv(io.StringIO(body))

    def is_up(self) -> bool:
        try:
            self.get_csv("/v3/option/list/expirations", symbol="SPY")
            return True
        except Exception:
            return False

    def list_expirations(self, symbol: str) -> list[pd.Timestamp]:
        df = self.get_csv("/v3/option/list/expirations", symbol=symbol)
        return [pd.Timestamp(x) for x in df["expiration"]]

    def chain_greeks_eod(self, symbol, expiration, start, end, strike_range) -> pd.DataFrame:
        return self.get_csv("/v3/option/history/greeks/eod", symbol=symbol,
                            expiration=_iso(expiration), start_date=_iso(start),
                            end_date=_iso(end), strike_range=strike_range)
