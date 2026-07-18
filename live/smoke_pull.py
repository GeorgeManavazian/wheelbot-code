"""Manual end-to-end eyeball (NOT a pytest): pulls live GDX from Schwab, maps it,
prints the head of the close series + the mapped chain. Run:
  PYTHONPATH=. .venv-live/bin/python live/smoke_pull.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "schwab"))
from schwab_client import get_client  # noqa: E402
from live.data import daily_closes, chain_frame  # noqa: E402


def main():
    c = get_client()
    s = daily_closes(c, "GDX")
    print(f"daily_closes GDX: {len(s)} rows; last: {s.index[-1].date()} = {s.iloc[-1]}")
    df = chain_frame(c, "GDX", target_dte=11, strike_count=12)
    print(f"chain_frame GDX: {len(df)} rows; cols {list(df.columns)}")
    if len(df):
        print(df.sort_values("strike").tail(5).to_string(index=False))
    print("\nLive adapter OK." if len(s) and len(df) else "\nNo data — check the token.")


if __name__ == "__main__":
    main()
