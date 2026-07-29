"""Push data/live/ to a private GitHub repo so the Mac's dashboard can read
current state without the VPS being reachable.

Direction is one-way: VPS -> GitHub -> Mac. The Mac never writes, so the VPS is
the unambiguous source of truth and there is no merge case to reason about.

INVARIANT: no credential ever leaves this machine. secret_guard() runs against
the actual staged paths before every push and aborts on a match."""
from __future__ import annotations
import json
import os
import re
import subprocess

GIT_CFG = os.path.expanduser("~/.wheelbot/git.json")

# substrings that must never appear in a staged path
_SECRET_MARKERS = (".schwab", ".wheelbot", "token.json", "oci_api_key",
                   ".pem", "id_rsa", "credentials")


# Credential shapes that must never appear in a pushed file's CONTENT. Checking
# only the path was insufficient: the paths in _SECRET_MARKERS live outside the
# synced tree and can never be staged, so the path check alone could not fire.
# A secret embedded in a legitimately-named file (a state.json, a run log) is the
# realistic leak, and that is what this catches.
_SECRET_CONTENT = (
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"x-access-token:[^@\s*]{8,}@"),
    re.compile(r'"refresh_token"\s*:\s*"[^"]{8,}"'),
    re.compile(r'"(?:app_secret|client_secret)"\s*:\s*"[^"]{6,}"'),
    re.compile(r'"password"\s*:\s*"[^"]{6,}"'),
)

_SCAN_MAX_BYTES = 2_000_000     # skip pathological files; state files are tiny


def secret_guard(paths, state_dir: str = None) -> list:
    """Every staged path that is, or CONTAINS, a credential. Empty == safe.

    Two independent checks, because either alone is insufficient:
      1. the path itself looks like a credential file, and
      2. the file's bytes match a known credential shape.
    """
    bad = [p for p in paths if any(m in p for m in _SECRET_MARKERS)]
    if state_dir is None:
        return bad
    for p in paths:
        if p in bad:
            continue
        full = os.path.join(state_dir, p)
        try:
            if os.path.getsize(full) > _SCAN_MAX_BYTES:
                continue
            with open(full, "r", errors="ignore") as f:
                body = f.read()
        except OSError:
            continue
        if any(rx.search(body) for rx in _SECRET_CONTENT):
            bad.append(p)
    return bad


def _scrub(text: str, cfg) -> str:
    """Remove the PAT from anything bound for a log. The state logs are synced
    to GitHub, so a leak here is a leak to the remote."""
    tok = (cfg or {}).get("token")
    out = str(text)
    if tok:
        out = out.replace(tok, "***")
    return re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", out)


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

        offenders = secret_guard(_staged_paths(state_dir), state_dir)
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
            print(f"[sync push failed] {_scrub(p.stderr, cfg).strip()[:300]}")
            return False
        return True
    except Exception as e:                     # noqa: BLE001 -- deliberate
        # NEVER interpolate a raw exception here. subprocess.TimeoutExpired
        # stringifies the FULL argv, and the push URL embeds the PAT -- a 120s
        # push timeout would print the token into a log that lives inside
        # data/live and is itself force-pushed to GitHub. (audit 2026-07-29)
        print(f"[sync FAILED {type(e).__name__}: {_scrub(str(e), cfg)}]")
        return False
