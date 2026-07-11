"""Pull SPY option greeks/eod chains from the local ThetaData terminal, normalize,
write a gitignored cache + a small committed fixture. Terminal must be running
(see docs/thetadata-v3-access.md)."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from src.engine_v2.options.theta_client import ThetaClient, ThetaError
from src.engine_v2.options.chain import normalize_greeks_eod

CACHE_PATH = "data/options/spy_greeks_eod.parquet"
FIXTURE_PATH = "fixtures/spy_options_small.parquet"

def build(client: ThetaClient, symbol: str, start, end, strike_range: int,
          dte_max: int) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    frames, skipped = [], 0
    for exp in client.list_expirations(symbol):
        win_start = max(start, exp - pd.Timedelta(days=dte_max))
        if exp < start or win_start > end or exp < win_start:
            continue
        try:
            raw = client.chain_greeks_eod(symbol, exp, win_start, min(exp, end), strike_range)
        except ThetaError as e:
            # a single expiration with no data (e.g. 472) must not kill the whole pull
            skipped += 1
            print(f"  skip {exp.date()}: {e}")
            continue
        if len(raw):
            frames.append(normalize_greeks_eod(raw))
    if skipped:
        print(f"skipped {skipped} expirations with no data")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["date","expiry","strike","right"]).reset_index(drop=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--strike-range", type=int, default=30)
    ap.add_argument("--dte-max", type=int, default=50)
    ap.add_argument("--fixture", action="store_true", help="also (re)write the committed fixture")
    args = ap.parse_args()

    # NOTE: default ThetaClient timeout (30s) is too short once a per-expiration
    # window spans more than a few trading days against the live terminal
    # (empirically ~14s base + a few seconds per trading day); bump it so
    # multi-week pulls don't spuriously abort mid-run.
    client = ThetaClient(timeout=90)
    if not client.is_up():
        raise SystemExit("ThetaData terminal not running — see docs/thetadata-v3-access.md")
    df = build(client, args.symbol, args.start, args.end, args.strike_range, args.dte_max)
    Path("data/options").mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_PATH)
    print(f"cache {CACHE_PATH}: {df.shape}")
    if args.fixture:
        keep = df["expiry"].drop_duplicates().sort_values().head(2)
        fx = df[df["expiry"].isin(keep)]
        fx.to_parquet(FIXTURE_PATH)
        print(f"fixture {FIXTURE_PATH}: {fx.shape}")

if __name__ == "__main__":
    main()
