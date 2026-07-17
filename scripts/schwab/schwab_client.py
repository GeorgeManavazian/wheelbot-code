"""Schwab live-data client (data-only; NO order placement by design).

Runs on the .venv-live py3.12 environment (schwab-py needs 3.10+), separate from
the 3.9 backtest venv. Talks to the engine through parquet files, not imports —
same boundary as the ThetaData pull.

Config lives OUTSIDE the repo at ~/.schwab/config.json (app_key, app_secret,
callback_url, token_path). Never committed.
"""
from __future__ import annotations
import json
import os

CONFIG_PATH = os.path.expanduser("~/.schwab/config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(f"No config at {CONFIG_PATH}. Create it with app_key, "
                         f"app_secret, callback_url, token_path.")
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for k in ("app_key", "app_secret", "callback_url", "token_path"):
        if not cfg.get(k) or "PASTE_YOUR" in str(cfg.get(k)):
            raise SystemExit(f"config.json: '{k}' is missing or still the placeholder. "
                             f"Fill in your real value.")
    cfg["token_path"] = os.path.expanduser(cfg["token_path"])
    return cfg


def login_client():
    """One-time interactive login (manual flow — works with the https://127.0.0.1
    callback, no local server / privileged port needed). Prints an authorize URL;
    you log in, authorize, then paste the final redirected URL back."""
    from schwab.auth import client_from_manual_flow
    cfg = load_config()
    return client_from_manual_flow(cfg["app_key"], cfg["app_secret"],
                                   cfg["callback_url"], cfg["token_path"])


def get_client():
    """Non-interactive client from the saved token (auto-refreshes the access
    token; the 7-day refresh token requires re-running the login when it lapses)."""
    from schwab.auth import client_from_token_file
    cfg = load_config()
    if not os.path.exists(cfg["token_path"]):
        raise SystemExit(f"No token at {cfg['token_path']}. Run schwab_login.py first.")
    return client_from_token_file(cfg["token_path"], cfg["app_key"], cfg["app_secret"])
