"""Push data/live/ to a private GitHub repo so the Mac's dashboard can read
current state without the VPS being reachable.

Direction is one-way: VPS -> GitHub -> Mac. The Mac never writes, so the VPS is
the unambiguous source of truth and there is no merge case to reason about.

INVARIANT: no credential ever leaves this machine. secret_guard() runs against
the actual staged paths before every push and aborts on a match."""
from __future__ import annotations
import json
import os
import subprocess

GIT_CFG = os.path.expanduser("~/.wheelbot/git.json")

# substrings that must never appear in a staged path
_SECRET_MARKERS = (".schwab", ".wheelbot", "token.json", "oci_api_key",
                   ".pem", "id_rsa", "credentials")


def secret_guard(paths) -> list:
    """Every path that looks like a credential. Empty list == safe to push."""
    return [p for p in paths if any(m in p for m in _SECRET_MARKERS)]


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, timeout=120)


def _staged_paths(state_dir: str) -> list:
    r = _git(["diff", "--cached", "--name-only"], state_dir)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def load_git_config(path: str = GIT_CFG):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None
    return cfg if isinstance(cfg, dict) else None


def sync_state(state_dir: str, message: str, cfg_path: str = GIT_CFG) -> bool:
    """Commit and push state_dir. True on success. Never raises -- a failed
    push must not abort a trading run; the VPS keeps authoritative state and
    the Mac catches up on the next successful push."""
    cfg = load_git_config(cfg_path)
    if cfg is None:
        print(f"[sync skipped -- no config at {cfg_path}]")
        return False
    try:
        if not os.path.isdir(os.path.join(state_dir, ".git")):
            _git(["init", "-q", "-b", "main"], state_dir)
            _git(["config", "user.email", "wheelbot@localhost"], state_dir)
            _git(["config", "user.name", "wheelbot"], state_dir)

        _git(["add", "-A"], state_dir)

        offenders = secret_guard(_staged_paths(state_dir))
        if offenders:
            print(f"[sync ABORTED -- credential in staged paths: {offenders}]")
            _git(["reset"], state_dir)
            return False

        # Commit only when something is staged -- but ALWAYS push. A commit
        # whose push failed would otherwise strand the repo permanently: every
        # later sync sees an empty index, returns early, and never retries, so
        # the mirror silently stops updating while still reporting success.
        if _staged_paths(state_dir):
            c = _git(["commit", "-m", message], state_dir)
            if c.returncode != 0 and "nothing to commit" not in (c.stdout + c.stderr):
                print(f"[sync commit failed] {c.stderr.strip()[:300]}")
                return False

        url = (f"https://x-access-token:{cfg['token']}@github.com/"
               f"{cfg['repo']}.git")
        # --force is correct here, not a workaround: this repo is a one-way
        # MIRROR of VPS state. The VPS is the only writer by design (the Mac
        # clones it read-only for the dashboard), so there is no remote work to
        # preserve and no merge to resolve. Without it any divergence -- a
        # re-init, a stray commit, a rebuilt state dir -- wedges sync forever,
        # which is exactly the silent-degradation class this project exists to
        # eliminate.
        p = _git(["push", "--force", url, "main"], state_dir)
        if p.returncode != 0:
            # scrub the token out of any error text before it reaches a log
            err = p.stderr.replace(cfg["token"], "***")
            print(f"[sync push failed] {err.strip()[:300]}")
            return False
        return True
    except Exception as e:                     # noqa: BLE001 -- deliberate
        print(f"[sync FAILED {type(e).__name__}: {e}]")
        return False
