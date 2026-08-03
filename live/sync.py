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
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"x-access-token:[^@\s*]{8,}@"),
    re.compile(r'"refresh_token"\s*:\s*"[^"]{8,}"'),
    re.compile(r'"(?:access_token|app_key)"\s*:\s*"[^"]{8,}"'),
    re.compile(r'"(?:app_secret|client_secret)"\s*:\s*"[^"]{6,}"'),
    re.compile(r'"password"\s*:\s*"[^"]{6,}"'),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

# D8: PAT shapes are also run against a whitespace-stripped copy of the body,
# so a line-wrapped token cannot defeat the single-string regexes.
_WRAP_DEFEATING = (_SECRET_CONTENT[0], _SECRET_CONTENT[1])

_SCAN_MAX_BYTES = 2_000_000     # state files are tiny; see oversize handling


def secret_guard(paths, state_dir: str = None, literals=(), read=None) -> list:
    """Every staged path that is, or CONTAINS, a credential -- or that CANNOT
    BE PROVEN CLEAN. Empty == safe.

    D8 hardening (audit 2026-07-31): an oversize or unreadable file is an
    OFFENDER, not a skip -- "could not scan" must abort the push, because a
    silent skip is exactly how a 2.1MB log carrying a token would leak.
    `literals` are the exact secret strings this process already holds (the
    PAT, the mail password, the Schwab tokens); any staged body containing
    one is an offender regardless of shape.

    F4a (audit 2026-08-03): `read` supplies the bytes to scan. sync_state
    passes an INDEX reader, because `git add -A` snapshots content into the
    index and the commit ships the index -- reading the worktree meant the
    guard never inspected what was actually pushed. Default (None) keeps the
    worktree read for direct callers. A reader that raises is still an
    offender; only paths that push NO content (deletions) are exempt, and
    those are filtered out before they reach here.
    """
    literals = tuple(l for l in literals if l and len(str(l)) >= 6)
    bad = [p for p in paths if any(m in p for m in _SECRET_MARKERS)]
    if state_dir is None:
        return bad
    for p in paths:
        if p in bad:
            continue
        try:
            if read is not None:
                body = read(p)
            else:
                full = os.path.join(state_dir, p)
                if os.path.getsize(full) > _SCAN_MAX_BYTES:
                    print(f"[secret_guard: {p} exceeds {_SCAN_MAX_BYTES}B -- "
                          f"unscannable, treated as an offender]")
                    bad.append(p)
                    continue
                with open(full, "r", errors="ignore") as f:
                    body = f.read()
            if len(body) > _SCAN_MAX_BYTES:
                print(f"[secret_guard: {p} exceeds {_SCAN_MAX_BYTES}B -- "
                      f"unscannable, treated as an offender]")
                bad.append(p)
                continue
        except OSError as e:
            print(f"[secret_guard: {p} unreadable ({type(e).__name__}) -- "
                  f"treated as an offender]")
            bad.append(p)
            continue
        stripped = re.sub(r"\s+", "", body)
        if (any(rx.search(body) for rx in _SECRET_CONTENT)
                or any(rx.search(stripped) for rx in _WRAP_DEFEATING)
                or any(str(l) in body for l in literals)):
            bad.append(p)
    return bad


def _known_literals(cfg) -> tuple:
    """The exact secret strings this process can already read: the PAT, the
    alert-mail password, and the Schwab token strings. Best-effort -- an
    absent/unreadable source contributes nothing (never raises)."""
    lits = [(cfg or {}).get("token")]
    try:
        from live.alerts import load_alert_config
        lits.append((load_alert_config() or {}).get("password"))
    except Exception:                          # noqa: BLE001 -- deliberate
        pass
    try:
        with open(os.path.expanduser("~/.schwab/token.json")) as f:
            tok = json.load(f)
        inner = tok.get("token", tok) if isinstance(tok, dict) else {}
        if isinstance(inner, dict):
            lits += [inner.get("refresh_token"), inner.get("access_token")]
    except Exception:                          # noqa: BLE001 -- deliberate
        pass
    return tuple(l for l in lits if l)


def _scrub(text: str, cfg) -> str:
    """Remove the PAT from anything bound for a log. The state logs are synced
    to GitHub, so a leak here is a leak to the remote."""
    tok = (cfg or {}).get("token")
    out = str(text)
    if tok:
        out = out.replace(tok, "***")
    return re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", out)


def _git(args, cwd) -> subprocess.CompletedProcess:
    # errors="replace": a staged binary blob read out of the index must never
    # raise UnicodeDecodeError out of the guard -- an uncaught decode error
    # aborts every tick identically, which is the F4a wedge by another door.
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, errors="replace", timeout=120)


def _staged_entries(state_dir: str) -> list:
    """(status, path) for every staged change.

    -z, so git never applies core.quotePath escaping. An escaped path such as
    `"logs/na\\303\\257ve run.log"` resolves to nothing on disk, and the
    fail-closed guard read that as an unreadable offender -- a permanent
    sync wedge triggered by nothing worse than an accented filename.
    """
    r = _git(["diff", "--cached", "--name-status", "-z"], state_dir)
    toks = [t for t in r.stdout.split("\0") if t]
    out, i = [], 0
    while i < len(toks):
        status, i = toks[i], i + 1
        # rename/copy emit TWO paths (source, destination); the destination is
        # the one carrying content into the commit.
        want = 2 if status[:1] in ("R", "C") else 1
        if i + want > len(toks):
            break
        out.append((status[:1], toks[i + want - 1]))
        i += want
    return out


def _staged_paths(state_dir: str) -> list:
    """Staged paths whose CONTENT will be pushed.

    Deletions are excluded: they carry no bytes into the commit, so there is
    nothing to scan. Treating them as unscannable is precisely the F4a wedge --
    D11's 7-day prune of a tracked *.bak-* file staged a deletion, the guard
    called it a credential, and the sync aborted on every tick thereafter.
    """
    return [p for status, p in _staged_entries(state_dir) if status != "D"]


def _index_bytes(state_dir: str, path: str) -> str:
    """The staged bytes for `path` -- what the commit will actually ship."""
    r = _git(["show", f":{path}"], state_dir)
    if r.returncode != 0:
        raise OSError(f"index read failed: {r.stderr.strip()[:120]}")
    return r.stdout


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

        # D13: self-healing .gitignore (ONLY *.tmp -- alerts-failed.jsonl and
        # *.bak-* are recovery artifacts and MUST stay synced), plus cleanup
        # of day-old orphaned tmp files (an in-flight writer's tmp is seconds
        # old; a day-old tmp is an orphan by construction).
        gi = os.path.join(state_dir, ".gitignore")
        try:
            existing = open(gi).read() if os.path.exists(gi) else ""
            if "*.tmp" not in existing:
                with open(gi, "a") as f:
                    f.write(("" if existing.endswith("\n") or not existing
                             else "\n") + "*.tmp\n")
            import time as _time
            cutoff = _time.time() - 86400
            for root, _dirs, files in os.walk(state_dir):
                if ".git" in root.split(os.sep):
                    continue
                for name in files:
                    if name.endswith(".tmp"):
                        full = os.path.join(root, name)
                        try:
                            if os.path.getmtime(full) < cutoff:
                                os.remove(full)
                        except OSError:
                            pass
        except OSError:
            pass

        # D14: self-install the mirror-side freshness watcher (workflow +
        # checker) so the GitHub robot exists wherever the mirror lives --
        # it rides the first Phase-F sync onto the VPS's store with no
        # separate deploy step. NOTE (declared): pushing .github/workflows/
        # requires the PAT to carry the `workflow` scope; verify at Phase F.
        try:
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            wf_dir = os.path.join(state_dir, ".github", "workflows")
            os.makedirs(wf_dir, exist_ok=True)
            for src, dst in (
                    (os.path.join(repo_root, "deploy", "mirror-freshness.yml"),
                     os.path.join(wf_dir, "mirror-freshness.yml")),
                    (os.path.join(repo_root, "deploy", "check_mirror_freshness.py"),
                     os.path.join(state_dir, ".github", "check_mirror_freshness.py"))):
                if os.path.exists(src):
                    with open(src) as f:
                        body = f.read()
                    cur = open(dst).read() if os.path.exists(dst) else None
                    if cur != body:
                        with open(dst, "w") as f:
                            f.write(body)
        except OSError:
            pass

        _git(["add", "-A"], state_dir)

        offenders = secret_guard(_staged_paths(state_dir), state_dir,
                                 literals=_known_literals(cfg),
                                 read=lambda p: _index_bytes(state_dir, p))
        if offenders:
            print(f"[sync ABORTED -- credential in staged paths: {offenders}]")
            _git(["reset"], state_dir)
            return False

        # Commit only when something is staged -- but ALWAYS push. A commit
        # whose push failed would otherwise strand the repo permanently: every
        # later sync sees an empty index, returns early, and never retries, so
        # the mirror silently stops updating while still reporting success.
        # _staged_entries, not _staged_paths: a tick whose ONLY change is a
        # deletion must still commit, or the pruned file is re-staged forever
        # and never actually leaves the mirror.
        if _staged_entries(state_dir):
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
