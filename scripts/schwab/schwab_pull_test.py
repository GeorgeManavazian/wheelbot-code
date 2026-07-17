"""Smoke test: prove the Schwab pipe works end to end. Pulls SPY daily price
history + one option chain using the saved token, prints shapes. No files
written, no orders — just confirms auth + data access.

Run (after schwab_login.py):  .venv-live/bin/python scripts/schwab/schwab_pull_test.py
"""
import sys
import os
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
from schwab_client import get_client  # noqa: E402
from schwab.client import Client  # noqa: E402


def main():
    c = get_client()

    # 1) daily price history (feeds regime_series)
    r = c.get_price_history_every_day("SPY")
    print(f"price_history SPY -> HTTP {r.status_code}")
    if r.status_code == 200:
        candles = r.json().get("candles", [])
        print(f"  {len(candles)} daily candles; "
              f"latest close: {candles[-1]['close'] if candles else 'none'}")

    # 2) option chain (feeds the wheel). BOUNDED pull — the full SPY chain 502s
    # (gateway chokes on the payload), and the wheel only needs puts near the
    # target delta/DTE anyway, so we always bound by strike count + date window.
    r = c.get_option_chain(
        "SPY",
        contract_type=Client.Options.ContractType.PUT,
        strike_count=10,
        from_date=dt.date.today(),
        to_date=dt.date.today() + dt.timedelta(days=45),
    )
    print(f"option_chain SPY (bounded puts) -> HTTP {r.status_code}")
    if r.status_code == 200:
        j = r.json()
        puts = j.get("putExpDateMap", {})
        print(f"  {len(puts)} put expiries, "
              f"{sum(len(v) for v in puts.values())} strikes; "
              f"underlying {j.get('underlyingPrice')}")

    print("\nPipe OK — Schwab live data is flowing." if r.status_code == 200
          else "\nSomething returned non-200 — check app entitlements / token.")


if __name__ == "__main__":
    main()
