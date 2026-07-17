"""One-time Schwab OAuth login (manual flow). Run once; saves a token to the
path in ~/.schwab/config.json. Re-run when the 7-day refresh token lapses.

Run:  .venv-live/bin/python scripts/schwab/schwab_login.py

You'll be shown an authorize URL. Open it, log in to Schwab, approve. Your
browser redirects to https://127.0.0.1/... which shows a "can't connect" page —
that is expected. Copy the FULL url from the address bar and paste it back here.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from schwab_client import login_client  # noqa: E402


def main():
    print("Starting Schwab manual login flow...\n")
    c = login_client()
    # a cheap authenticated call to confirm the token works
    r = c.get_quote("SPY")
    ok = getattr(r, "status_code", None) == 200
    print(f"\nLogin OK. Token saved. Test quote SPY -> HTTP {getattr(r,'status_code','?')} "
          f"({'good' if ok else 'check credentials/entitlements'}).")


if __name__ == "__main__":
    main()
