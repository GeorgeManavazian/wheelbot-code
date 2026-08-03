# F4 audit — RAW finder output (2026-08-03)

> UNVERIFIED unless listed below. These are finder claims; the adversarial verifier pass had
> not run when the workflow was stopped for cost. Nothing here counts until independently
> re-executed.
> Domains still missing entirely: **money-path, alerting, test-integrity, declared-honesty**.

## Verification log (main session, independently re-executed)

| Finding | Verdict | Receipt |
|---|---|---|
| `security:sync-deletion-wedge` (= `security:01`, found twice by two independent finders) | **CONFIRMED — DEPLOY BLOCKER** | Re-ran the repro in a fresh scratch store: 3 consecutive `sync_state()` calls each printed `[secret_guard: ...bak-2026-07-20 unreadable (FileNotFoundError) -- treated as an offender]` → `[sync ABORTED -- credential in staged paths: [...]]` → `False`. Self-repeating, never self-heals. Trigger confirmed real: `live/state.py:141-151` `_day_boundary_backup` prunes `state.json.bak-<date>` older than `BACKUP_KEEP_DAYS = 7` with `os.remove()`, and `live/sync.py:150-152` deliberately keeps `*.bak-*` OUT of `.gitignore` ("recovery artifacts and MUST stay synced") — so the pruned file is tracked, the deletion stages, and the guard misreads it as a credential. Dormant today only because D11 backups have never run on the VPS (`git -C data/live-synced ls-files \| grep -c bak- == 0`); it arms itself on the first post-deploy run and fires ~8 days later, killing the mirror permanently. All 14 sync/secret-guard tests pass — none covers a deletion. |

**Cross-domain corroboration worth noting:** three independent finders (`reporting:gap-banner-renders-false-no-activity`,
`state-persistence:2`, `state-persistence:3`) plus the main session's own F3 dashboard read all landed on the same
defect — the authoritative `gaps.jsonl` is UNCORRECTED (2026-07-23 labelled `no_run` while 22 real trades were booked
that day; 2026-07-24 still plain `pull_failure`; 2026-07-27 absent). The C2/C13/C14/C15 correction *code* shipped;
the correction *data* never reached the VPS ledger. Filed as F3a in the repair plan.

## domain: security  (6 findings)

**Finder summary:** Audited the security surface of the repaired live bot: PAT handling in live/sync.py (secret_guard, _scrub, _known_literals, the force-push URL), token.json / ~/.schwab handling and on-disk permissions, subprocess usage (no shell=True, no argv from data anywhere in live/, src/, deploy/, dashboard/), the mirror-freshness GitHub workflow (no ${{ }} interpolation of mirror content — the issue body is a JS variable, so no injection), the email path, and eval/exec/pickle (grep across live/, src/, deploy/, scripts/, dashboard/ returned zero non-test hits). I executed probes rather than reading: replayed the TimeoutExpired leak class against the current code (scrubbed clean, including percent-encoded and cfg-missing-token variants), walked the entire git history of the read-only mirror clone data/live-synced for every credential shape (clean), and ran mutation-style probes against secret_guard in a sandbox. The two strongest results are new: (1) the D8 "unreadable == offender" hardening turns any staged file DELETION into a permanent, self-repeating sync abort, and D11's 7-day .bak-* prune guarantees one within ~8 days of resume; (2) secret_guard inspects worktree bytes while the commit ships index bytes, so the guard never validates what is actually pushed. Also found the PAT is passed on the git command line (visible in ps / /proc), the live Schwab token.json is mode 0644 with no code-level enforcement, and the alert spool that is deliberately pushed to the mirror carries the VPS IP, ssh user and key path. Nothing was written under the repo or data/live*; all probes ran in the sandbox and /private/tmp/f4sec.

### `security:sync-deletion-wedge` — HIGH — Any staged file DELETION is treated as a credential offender, permanently wedging the mirror sync (D11's 7-day .bak prune guarantees it)
**Where:** `live/sync.py`:74

**Claim:** secret_guard() resolves each staged path against the WORKTREE (os.path.join(state_dir, p)). A staged deletion has no worktree file, so open() raises FileNotFoundError and the D8 hardening classifies it as an OFFENDER. sync_state() then prints '[sync ABORTED -- credential in staged paths: ...]', runs `git reset`, and returns False. The file stays deleted, so the identical deletion is re-staged on every subsequent tick and the abort repeats forever — the mirror never updates again without manual intervention. Not hypothetical: live/state.py:141-151 (_day_boundary_backup, D11 marked DONE at repair-plan line 282) prunes state.json.bak-<date> older than BACKUP_KEEP_DAYS=7 with os.remove(), and live/sync.py:152-153 explicitly states those .bak-* files MUST stay synced (i.e. are tracked). So ~8 days after the repaired code resumes, the first aged-backup prune permanently kills the sync. All 14 sync/secret-guard tests pass — no test covers a deletion. The same fail-closed wedge fires for any path git escapes under core.quotePath (verified separately: staged path '"logs/na\303\257ve run.log"' -> offender).

**Repro:**
```
mkdir -p /private/tmp/f4sec && cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && WHEELBOT_STATE_DIR=/private/tmp/f4sec/scratch PYTHONPATH=. .venv-live/bin/python - <<'EOF'
import os, subprocess, tempfile
from live.sync import sync_state
from live.state import BACKUP_KEEP_DAYS
w = tempfile.mkdtemp(prefix="/private/tmp/f4sec/wedge-")
sd = os.path.join(w, "live"); os.makedirs(os.path.join(sd, "accounts", "100k_N1"))
g = lambda *a: subprocess.run(["git"]+list(a), cwd=sd, capture_output=True, text=True)
st = os.path.join(sd, "accounts", "100k_N1", "state.json"); bak = st + ".bak-2026-07-20"
open(st,"w").write('{"cash":100000}'); open(bak,"w").write('{"cash":99000}')
g("init","-q","-b","main"); g("config","user.email","x@x"); g("config","user.name","x")
g("add","-A"); g("commit","-q","-m","day1")
os.remove(bak)   # == live/state.py:149 pruning a backup older than BACKUP_KEEP_DAYS
cfg = os.path.join(w,"git.json"); open(cfg,"w").write('{"token":"github_pat_FAKE0000000000000000000","repo":"o/r"}')
for t in (1,2,3): print("tick",t,"sync_state ->", sync_state(sd, f"eod {t}", cfg_path=cfg))
print("BACKUP_KEEP_DAYS =", BACKUP_KEEP_DAYS)
EOF
```

**Finder evidence:**
```
[secret_guard: accounts/100k_N1/state.json.bak-2026-07-20 unreadable (FileNotFoundError) -- treated as an offender]
[sync ABORTED -- credential in staged paths: ['accounts/100k_N1/state.json.bak-2026-07-20']]
tick 1 sync_state -> False
... identical output for tick 2 and tick 3 ...
BACKUP_KEEP_DAYS = 7

# the tests that should have caught it:
$ PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_sync.py live/tests/test_secret_guard_hardening.py live/tests/test_sync_retry.py -q
14 passed in 0.41s
```

### `security:guard-scans-worktree-not-index` — MED — secret_guard validates worktree bytes, but the commit/push ships INDEX bytes — the guard never inspects what is actually pushed
**Where:** `live/sync.py`:65

**Claim:** sync_state() runs `git add -A` (snapshotting contents into the index), then secret_guard() re-opens each path from the WORKTREE. The commit that follows ships the index, not the worktree. Any divergence in the add->guard window is invisible to the guard: a body that held a credential at `git add` time and was overwritten with clean content before the guard read it is committed and force-pushed with the secret intact, while the guard reports zero offenders. The literals check (the exact PAT string) does not help, because the guard never sees the staged bytes. The correct source is `git diff --cached` / `git show :path`. Stated honestly: triggering requires a shrinking/overwriting write to a staged file inside that window; the tick is single-instance (systemd Type=oneshot), so the realistic writer is another process writing under data/live (logs/dashboard.log in the mirror proves a dashboard has written there). The defect is that the guard's guarantee is structurally unsound, not merely unlucky.

**Repro:**
```
mkdir -p /private/tmp/f4sec && cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && WHEELBOT_STATE_DIR=/private/tmp/f4sec/scratch PYTHONPATH=. .venv-live/bin/python - <<'EOF'
import os, subprocess, tempfile
from live.sync import secret_guard
w = tempfile.mkdtemp(prefix="/private/tmp/f4sec/toctou-")
g = lambda *a: subprocess.run(["git"]+list(a), cwd=w, capture_output=True, text=True)
g("init","-q","-b","main"); g("config","user.email","x@x"); g("config","user.name","x")
SEC="github_pat_REALLOOKINGSECRET012345678"
open(w+"/run.log","w").write(f"https://x-access-token:{SEC}@github.com/o/r\n")
g("add","-A")                                   # secret now in the INDEX
open(w+"/run.log","w").write("clean line\n")    # worktree overwritten before the guard reads it
staged=[l.strip() for l in g("diff","--cached","--name-only").stdout.split()]
print("guard verdict:", secret_guard(staged, w, literals=(SEC,)))
g("commit","-q","-m","tick")
print("secret present in the committed blob that push ships:", SEC in g("show","HEAD:run.log").stdout)
EOF
```

**Finder evidence:**
```
guard verdict: []
secret present in the committed blob that push ships: True
```

### `security:pat-in-argv` — MED — The GitHub PAT is passed on the git command line, exposing it in ps / /proc/<pid>/cmdline for the whole push
**Where:** `live/sync.py`:230

**Claim:** sync_state() builds `https://x-access-token:<PAT>@github.com/<repo>.git` and passes it as an argv element to `git push --force`. The full credential is therefore readable by any local process for the duration of every push (up to the 120s _git timeout) via `ps -ww` on macOS or /proc/<pid>/cmdline on the Linux VPS (world-readable by default; no unit in deploy/ sets hidepid). This is the OS-level twin of the TimeoutExpired leak the repair fixed in Python — the argv the repair is careful never to stringify into a log is nevertheless published by the kernel. Safer forms exist (GIT_ASKPASS, a credential helper, or `git credential approve` on stdin). Confirmed separately that the Python-side scrubbing itself is sound: TimeoutExpired, percent-encoded and cfg-missing-token variants all come out redacted.

**Repro:**
```
S=/private/tmp/f4sec/argv; mkdir -p $S; cd $S; git init -q -b main .; echo x > a.txt; git add -A; git -c user.email=x@x -c user.name=x commit -qm c; TOK="github_pat_ARGVLEAKARGVLEAKARGVLEAK1"; git push --force "https://x-access-token:${TOK}@10.255.255.1/o/r.git" main >/dev/null 2>&1 & PID=$!; sleep 1; ps -ww -o command= -p $PID; kill $PID 2>/dev/null
```

**Finder evidence:**
```
/Library/Developer/CommandLineTools/usr/bin/git push --force https://x-access-token:github_pat_ARGVLEAKARGVLEAKARGVLEAK1@10.255.255.1/o/r.git main

# for contrast, the Python-side scrub IS clean (probe against live/sync.py:_scrub):
scrubbed TimeoutExpired: Command '['git', 'push', '--force', 'https://x-access-token:***@github.com/o/r.git', 'main']' timed out after 120 seconds
LEAK1: False   LEAK2 (cfg without token): False   LEAK3 (pct-encoded): False
```

### `security:schwab-token-file-mode` — MED — Live Schwab token.json is mode 0644 (world-readable); the documented 'chmod 600' is a one-time manual step no code enforces or checks
**Where:** `scripts/schwab/schwab_client.py`:48

**Claim:** docs/superpowers/specs/2026-07-27-live-bot-vps-migration-design.md:52-53 and the migration runbook (line 943) declare ~/.schwab/{config.json,token.json} are chmod 600. Nothing in the repo enforces or verifies it: `grep -rn 'chmod|0o600|S_IRUSR' live/*.py scripts/schwab/*.py deploy/*` returns only a prose comment in live/alerts.py. schwab-py's writer (.venv-live/.../schwab/auth.py:34) is a bare `open(token_path,'w')`, so the mode is umask-derived (0644) whenever the file is CREATED — i.e. on a fresh install and on any re-login that recreates it, which is exactly the operation the D5 nag tells the owner to perform every 7 days. Observable now on this machine: config.json (hand-chmod'd once, never rewritten) is 0600 while token.json — rewritten by schwab-py on 2026-08-02, holding a 140-char refresh_token, a 76-char access_token and a 404-char id_token — is 0644 inside a 0755 directory. The control silently does not hold and no guard reports it.

**Repro:**
```
stat -f "%Sp %N %Sm" ~/.schwab/config.json ~/.schwab/token.json; stat -f "%Sp %N" ~/.schwab; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn "chmod\|0o600\|S_IRUSR" live/*.py scripts/schwab/*.py deploy/* 2>/dev/null; sed -n '29,36p' .venv-live/lib/python3.12/site-packages/schwab/auth.py; mkdir -p /private/tmp/f4sec && cd /private/tmp/f4sec && python3 -c "
import os, json, stat
p='tok_probe.json'
if os.path.exists(p): os.remove(p)
with open(p,'w') as f: json.dump({'t':1}, f)
print('fresh create ->', oct(stat.S_IMODE(os.stat(p).st_mode)))
os.chmod(p, 0o600)
with open(p,'w') as f: json.dump({'t':2}, f)
print('rewrite of a 600 file ->', oct(stat.S_IMODE(os.stat(p).st_mode)))
os.remove(p)"
```

**Finder evidence:**
```
-rw------- /Users/georgiemanavazian/.schwab/config.json Jul 17 19:26:18 2026
-rw-r--r-- /Users/georgiemanavazian/.schwab/token.json Aug  2 22:37:21 2026
drwxr-xr-x /Users/georgiemanavazian/.schwab

# only hit for the chmod grep is a comment:
live/alerts.py:2:(chmod 600, outside the repo -- same pattern as ~/.schwab/config.json).

# schwab-py writer:
        with open(token_path, 'w') as f:
            json.dump(t, f)

fresh create -> 0o644
rewrite of a 600 file -> 0o600

# token.json shape (values not printed):
creation_timestamp: int; token: {refresh_token len=140, access_token len=76, id_token len=404, ...}
```

### `security:spool-infra-disclosure-to-mirror` — MED — The alert spool that is deliberately force-pushed to the GitHub mirror carries the VPS public IP, ssh user and private-key path; secret_guard passes it clean
**Where:** `live/run_notify.py`:31

**Claim:** send_alert() spools every UNDELIVERED alert body verbatim to in_state('alerts-failed.jsonl') = data/live/alerts-failed.jsonl, and live/sync.py:152-153 states that file MUST stay synced — so it is committed and `git push --force`ed to the mirror. Every token-age alert body embeds run_notify.SSH_HINT, which contains the VPS public IP 129.80.185.142, the login user `ubuntu`, and the private-key path ~/.ssh/wheelbot.key. intraday_errors() likewise spools the last 1500 bytes of the intraday log. secret_guard() returns no offenders for such a file (no shape or literal matches), so the module's stated INVARIANT ('no credential ever leaves this machine') holds only for credential SHAPES — targeting information for the machine that holds the credentials is exported with no check at all. Impact is bounded by the mirror repo being private, which is an assumption no code verifies; a visibility flip or a leaked read grant turns this into a ready-made attack target list.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && S=/private/tmp/f4sec/spool && rm -rf $S && mkdir -p $S/logs && touch $S/logs/.dailyran-2026-07-30 && WHEELBOT_STATE_DIR=$S PYTHONPATH=. .venv-live/bin/python -c "
from live.run_notify import token_age
from live.sync import secret_guard
from live.paths import in_state
token_age('/nonexistent/token.json', logs_dir='$S/logs')
print(open(in_state('alerts-failed.jsonl')).read())
print('secret_guard offenders:', secret_guard(['alerts-failed.jsonl'], '$S', literals=()))"
```

**Finder evidence:**
```
{"ts": "2026-08-03T15:08:25.938596+00:00", "subject": "Schwab token file MISSING -- bot will go blind", "body": "/nonexistent/token.json does not exist, ... Re-run the login on the VPS:\n  ssh -i ~/.ssh/wheelbot.key ubuntu@129.80.185.142\n  cd /home/ubuntu/etf-bot && PYTHONPATH=. .venv-live/bin/python scripts/schwab/schwab_login.py"}
secret_guard offenders: []
```

### `security:git-json-perms-unspecified` — LOW — ~/.wheelbot/git.json (the PAT at rest) has no permission requirement in code or docs, unlike every other secret file
**Where:** `live/sync.py`:128

**Claim:** load_git_config() opens ~/.wheelbot/git.json with no mode check and no chmod. Every other secret store in the project carries an explicit 'chmod 600' instruction somewhere (alerts.py:2 docstring; design spec lines 52-53 for ~/.schwab and ~/.wheelbot/alerts.json; the runbook's `chmod 600 token.json config.json` at migration-doc line 943). git.json — which holds the PAT granting write to the mirror repo — is the one secret file named in the docs only as a path, with no permission guidance and no runtime verification. Combined with security:schwab-token-file-mode, there is no place in the system where at-rest secret permissions are asserted.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn "git.json" docs/ live/ README.md 2>/dev/null | grep -v '\.pyc'; echo '--- chmod guidance in docs:'; grep -rn "chmod 600" docs/
```

**Finder evidence:**
```
live/sync.py:15:GIT_CFG = os.path.expanduser("~/.wheelbot/git.json")
docs/.../2026-07-28-live-bot-vps-migration.md:608:- Consumes: nothing (reads `~/.wheelbot/git.json`)
(no chmod/permission statement for git.json anywhere)
--- chmod 600 guidance covers only the OTHER files:
docs/.../2026-07-28-live-bot-vps-migration.md:943:chmod 600 /home/ubuntu/.schwab/token.json /home/ubuntu/.schwab/config.json
docs/.../2026-07-27-live-bot-vps-migration-design.md:53:~/.wheelbot/          alerts.json                (chmod 600, never committed)
```

## domain: security  (4 findings)

**Finder summary:** Audited the security domain of the repaired wheel bot: PAT handling in live/sync.py, the D8 secret_guard, the D14 self-installed GitHub workflow, alert/email credential handling, subprocess and injection primitives, and everything that actually reaches the force-pushed mirror. Positive results first: the TimeoutExpired leak class is genuinely fixed (an injected push timeout carrying a PAT in argv printed `x-access-token:***@`, token absent from output); git itself redacts credentials in its `unable to access` stderr; there is no shell=True, os.system, eval, exec, pickle or yaml.load anywhere in live/dashboard/src/scripts/deploy; scanning all 125 files in data/live, all 142 in data/live-synced and all 397 blobs in the mirror's entire git history with the project's own secret regexes produced zero hits; the mirror clone's remote URL carries no token; the systemd units and .streamlit config hold no secrets; and all 33 existing sync/guard tests pass. The four defects found are new. The headline is a guaranteed permanent sync wedge where D11's 7-day backup prune collides with D8's "unreadable = offender" rule, proven end-to-end with the real _day_boundary_backup and the real sync_state. The others are a world-readable Schwab token file that no code ever chmods, an email path that ships verbatim exactly the log content the mirror guard blocks, and mirror-derived strings rendered as raw HTML in the dashboard. All probes ran in /private/tmp/.../f4/security; nothing under the repo or data/live* was written.

### `security:01` — HIGH — D11's 7-day backup prune permanently wedges the mirror sync via D8's "unreadable = offender" rule, and reports it as a credential leak
**Where:** `live/sync.py`:75

**Claim:** secret_guard scans `git diff --cached --name-only`, which includes STAGED DELETIONS. A deleted path cannot be opened, so the D8 hardening (`except OSError -> bad.append(p)`, live/sync.py:71-79) classifies every deletion as a credential offender. live/state.py:149 (`_day_boundary_backup`, BACKUP_KEEP_DAYS=7) deletes `state.json.bak-<date>` files older than 7 days, and those backups are deliberately tracked and synced (repair plan D11: "pruned at 7 days, synced -- recovery artifacts"). Therefore ~8 days after the bot resumes, the first prune stages a deletion, sync_state aborts with `[sync ABORTED -- credential in staged paths: ...]`, and calls `git reset`. The reset unstages the deletion but the file is still gone from the working tree, so the next tick's `git add -A` re-stages the identical deletion and aborts again. The wedge is PERMANENT and self-perpetuating: the mirror stops updating forever, the Mac dashboard silently shows frozen numbers, and the operator is told a credential is in the staged paths -- a false leak alarm that sends them hunting for a secret that does not exist. No existing test covers a staged deletion; all 33 sync/guard tests pass. Same class also fires for any path git escapes in diff --cached (core.quotePath) and for the D13 *.tmp cleanup if a .tmp was ever committed.

**Repro:**
```
S=$(mktemp -d); cat > $S/run.py <<'EOF'
import sys, os, json, subprocess, datetime, glob
sys.path.insert(0,"/Users/georgiemanavazian/Documents/Trading/code/etf-bot")
S=os.environ["S"]; st=os.path.join(S,"state"); acc=os.path.join(st,"accounts","A1")
os.makedirs(acc, exist_ok=True)
sp=os.path.join(acc,"state.json")
open(sp,"w").write('{"cash":100,"positions":[]}\n')
today=datetime.date.today()
open(f"{sp}.bak-{today - datetime.timedelta(days=9)}","w").write('{"cash":90,"positions":[]}\n')
for c in (["init","-q","-b","main","."],["config","user.email","t@t"],["config","user.name","t"],["add","-A"],["commit","-qm","day1"]):
    subprocess.run(["git"]+c, cwd=st, capture_output=True)
print("tracked before:", subprocess.run(["git","ls-files"],cwd=st,capture_output=True,text=True).stdout.split())
from live.state import _day_boundary_backup, BACKUP_KEEP_DAYS
print("BACKUP_KEEP_DAYS =", BACKUP_KEEP_DAYS)
_day_boundary_backup(sp, acc)   # the REAL production prune
print("on disk after prune:", sorted(os.path.basename(p) for p in glob.glob(acc+"/*")))
json.dump({"token":"github_pat_FAKE1234567890123456789","repo":"o/r"}, open(os.path.join(S,"git.json"),"w"))
from live.sync import sync_state
for i in (1,2):
    print(f"--- sync {i} ->", sync_state(st, "eod", cfg_path=os.path.join(S,"git.json")))
EOF
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && S=$S WHEELBOT_STATE_DIR=$S/scratch PYTHONPATH=. .venv-live/bin/python $S/run.py
```

**Finder evidence:**
```
tracked before: ['accounts/A1/state.json', 'accounts/A1/state.json.bak-2026-07-25']
BACKUP_KEEP_DAYS = 7
on disk after prune: ['state.json', 'state.json.bak-2026-08-03']
[secret_guard: accounts/A1/state.json.bak-2026-07-25 unreadable (FileNotFoundError) -- treated as an offender]
[sync ABORTED -- credential in staged paths: ['accounts/A1/state.json.bak-2026-07-25']]
--- sync 1 -> False
[secret_guard: accounts/A1/state.json.bak-2026-07-25 unreadable (FileNotFoundError) -- treated as an offender]
[sync ABORTED -- credential in staged paths: ['accounts/A1/state.json.bak-2026-07-25']]
--- sync 2 -> False

Context receipt (no existing test covers it): cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && PYTHONPATH=. .venv-live/bin/python -m pytest live/tests/test_secret_guard_hardening.py live/tests/test_sync_retry.py live/tests/test_audit_fixes.py -q  ->  33 passed in 0.75s
```

### `security:02` — MED — ~/.schwab/token.json (Schwab refresh + access tokens) is mode 0644 and no code ever chmods it
**Where:** `scripts/schwab/schwab_client.py`:37

**Claim:** The Schwab token file holds refresh_token, access_token and id_token under a top-level `token` key. It is written by schwab-py (client_from_manual_flow / client_from_token_file) with the default umask, producing mode 0644 -- group- and world-readable. Nothing in live/, scripts/, deploy/, dashboard/ or src/ ever chmods it: a grep for chmod|0o600|umask over those trees returns only a docstring in live/alerts.py, two test fixtures, and an unrelated splits.py seal. The sibling ~/.schwab/config.json (app_key/app_secret) IS 0600, so the pair of credentials that actually grants live brokerage API access is the one left world-readable. The same code path runs on the VPS as User=ubuntu (deploy/wheelbot.service), so any other local account or process can read the tokens. The repair plan's D8/alerts hardening never addresses file permissions.

**Repro:**
```
stat -f "%Sp %N" ~/.schwab/token.json ~/.schwab/config.json; python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.schwab/token.json')));print(sorted(d.keys()));print(sorted(d['token'].keys()))"; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn "chmod\|0o600\|umask" --include="*.py" --include="*.sh" live scripts deploy dashboard src
```

**Finder evidence:**
```
-rw-r--r-- /Users/georgiemanavazian/.schwab/token.json
-rw------- /Users/georgiemanavazian/.schwab/config.json
['creation_timestamp', 'token']
['access_token', 'expires_at', 'expires_in', 'id_token', 'refresh_token', 'scope', 'token_type']

grep (no chmod of any credential file anywhere):
live/alerts.py:2:(chmod 600, outside the repo -- same pattern as ~/.schwab/config.json).
live/tests/test_secret_guard_hardening.py:52:    os.chmod(p, 0)
live/tests/test_secret_guard_hardening.py:56:        os.chmod(p, 0o644)
live/tests/test_tick_script.py:41:        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
src/engine_v2/data/splits.py:62:    sealed_file.chmod(_stat.S_IRUSR | _stat.S_IRGRP | _stat.S_IROTH)
```

### `security:03` — MED — The intraday-errors email exfiltrates verbatim log content that secret_guard blocks from the mirror -- egress is guarded on one channel only
**Where:** `live/run_notify.py`:112

**Claim:** intraday_errors() reads the last 1500 bytes of logs/intraday-<date>.log and puts them unmodified into an email body (live/run_notify.py:110-121). There is no _scrub() call and no secret_guard on this path, unlike the mirror push which runs the D8 literal+shape scan over that same file before it can leave the box. For one and the same file, secret_guard returns it as an offender and aborts the push while intraday_errors mails its contents out over SMTP and exits 0 (which also makes the tick write its once-per-day suppression marker, so the send is recorded as a success). The tick invokes this automatically whenever the intraday log matches grep -qiE "ERROR|Traceback" (scripts/wheelbot_tick.sh), i.e. exactly on the failure days when unusual diagnostic text lands in the log. The INVARIANT at the top of live/sync.py -- "no credential ever leaves this machine" -- is enforced only on the git channel.

**Repro:**
```
S=$(mktemp -d); cat > $S/mailleak.py <<'EOF'
import sys, os
sys.path.insert(0,"/Users/georgiemanavazian/Documents/Trading/code/etf-bot")
S = os.environ["S"]
TOK = "github_pat_11ZZZZZZZZZZZZZZZZZZZZ0123456789abcdefghij"
log = os.path.join(S, "intraday-2026-08-05.log")
open(log,"w").write("ERROR: push helper crashed\n"
                    "fatal: unable to access 'https://x-access-token:%s@github.com/o/r.git'\n" % TOK)
from live.sync import secret_guard
print("secret_guard ->", secret_guard([os.path.basename(log)], state_dir=S, literals=()))
import live.alerts as alerts
captured = {}
def fake_deliver(subject, body, path=alerts.ALERTS_PATH):
    captured["subject"], captured["body"] = subject, body
    return True
alerts._deliver_now = fake_deliver          # stub: no mail is actually sent
import live.run_notify as rn
rn.send_alert = alerts.send_alert
print("intraday_errors rc =", rn.intraday_errors("2026-08-05", log))
print("EMAIL SUBJECT:", captured.get("subject"))
print("TOKEN IN EMAIL BODY:", TOK in captured.get("body",""))
print(captured.get("body","")[-200:])
EOF
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && S=$S WHEELBOT_STATE_DIR=$S/scratch PYTHONPATH=. .venv-live/bin/python $S/mailleak.py
```

**Finder evidence:**
```
secret_guard -> ['intraday-2026-08-05.log']
intraday_errors rc = 0
EMAIL SUBJECT: intraday exit manager erroring 2026-08-05
TOKEN IN EMAIL BODY: True
---- body tail ----
ot be firing, which looks IDENTICAL to a quiet day in the logs -- that is why this alert exists.

Recent log:

ERROR: push helper crashed
fatal: unable to access 'https://x-access-token:github_pat_11ZZZZZZZZZZZZZZZZZZZZ0123456789abcdefghij@github.com/o/r.git'
```

### `security:04` — LOW — Dashboard renders mirror-derived strings as raw HTML (to_html(escape=False) + unsafe_allow_html=True)
**Where:** `dashboard/monitor.py`:396

**Claim:** dashboard/monitor.py:396 and :502 call disp.to_html(escape=False, index=False) and pass the result to st.markdown(..., unsafe_allow_html=True). Three of those columns are data-derived, not code-derived: Ticker (p["ticker"]), Phase (p.get("phase")) and Right (c_["right"]) are read straight out of accounts/*/state.json by _rows_and_equity (dashboard/monitor.py:86, 120, 131). escape=False is needed only for the code-generated <span class=...> badges, but it disables escaping for the entire frame, so any HTML sitting in a state field is emitted into the page verbatim. A corrupt or tampered state.json in the synced mirror -- the one input the Mac dashboard trusts and never validates -- becomes markup in the operator's browser. Verified: an <img src=x onerror=...> value placed in positions[0].ticker survives the exact production render call byte-for-byte.

**Repro:**
```
S=$(mktemp -d); cat > $S/xss.py <<'EOF'
import sys; sys.path.insert(0,"/Users/georgiemanavazian/Documents/Trading/code/etf-bot")
import pandas as pd
from dashboard.monitor import _rows_and_equity
PAY = '<img src=x onerror=alert(1)>'
state = {"cash": 0.0, "positions": [
    {"ticker": PAY, "phase": "PUT", "shares": 100, "basis": 10.0, "last_spot": 11.0}]}
rows, eq, unreal = _rows_and_equity(state, {})
df = pd.DataFrame(rows)
disp = pd.DataFrame({"Ticker": df["Ticker"], "Phase": df["Phase"]})
html = disp.to_html(escape=False, index=False)   # exact call from monitor.py:396
print(html)
print("PAYLOAD RENDERED RAW:", PAY in html)
EOF
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && WHEELBOT_STATE_DIR=$S/scratch PYTHONPATH=. .venv-live/bin/python $S/xss.py 2>&1 | tail -8
```

**Finder evidence:**
```
    <tr>
      <td><img src=x onerror=alert(1)></td>
      <td>PUT</td>
    </tr>
  </tbody>
</table>
PAYLOAD RENDERED RAW: True
```

## domain: reporting  (10 findings)

**Finder summary:** I audited the owner-facing reporting surface end to end by importing dashboard/monitor.py and live/compare.py headless (live venv, streamlit 1.59.2 / pandas 3.0.3) with WHEELBOT_STATE_DIR pointed at a sandbox copy of data/live-synced — no writes to the repo or to either live store — and executed every C-item computation on the real 25-account book: gap banner split (C2/C3), dedupe (C12), day-0 anchoring (C10), Sharpe/SE/rf (C4), campaign win rate (C5), benchmark (C6), concentration header (C7), mark-basis line (C11), covered-share value (C9), and the Mark as-of column (C1). I independently recomputed equity, drawdown, realized-cash campaign P&L and lifetime premium for four accounts from raw snapshots/trades with my own pandas; the dedupe, day-0 drawdown (100k_N1 -0.4476%), returns, and the C5 win/loss attribution all reconcile exactly with the dashboard functions, and the C7 header (N_eff 2.09, rho-bar 0.458, DOW 49%) reproduces. The live suite is green (376 passed), so everything below survives it. The nine findings cluster on things the repaired code says it fixed but which are inert or false against the actual store: the C1 carried-mark column renders "clean" for all 31 legs including nine expired ones holding $10,218 of liability inside Equity; the "Premium collected" KPI reads $0.00 on eight accounts that sold up to $22,006; the C2/C3 banner still prints "no bot activity" for a day that moved -$12,748.30; a whole trading day (07-31, -$18,415.80) is missing from both the curves and the gap ledger; and one truncated JSONL line anywhere takes both pages down.

### `reporting:premium-collected-kpi` — HIGH — Board KPI "Premium collected" shows only OPEN legs' credit — reads $0.00 on 8 of 25 accounts that collected up to $22,006
**Where:** `dashboard/monitor.py`:361

**Claim:** `total_premium = sum(r["_premium"] for r in rows)` sums only currently-OPEN positions (short branch: `credit*100*contracts`), yet the metric is labelled "Premium collected" with no qualifier anywhere on the board (the caption at monitor.py:338 says only "$X capital · up to N positions · K open · updates every 15s"). Any account that is flat at render time displays "Premium collected $0.00" while its ledger shows thousands of dollars of premium actually sold. On the real synced store 8/25 accounts (50k_N1, 50k_N2, 100k_N1, 100k_N2, 250k_N1, 250k_N2, 500k_N1, 500k_N2) render $0.00; 500k_N1 has sold $22,006 of premium. The same wrong number is repeated in the positions table's bold TOTAL footer row (monitor.py:393).

**Repro:**
```
set -e; R=/tmp/f4rep1; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from dashboard.monitor import _rows_and_equity,_load_json,_load_jsonl,_fmt
from live.accounts import account_paths
for cap,n in [(100000,1),(250000,1),(500000,1),(500000,4)]:
    p=account_paths(cap,n)
    st=_load_json(Path(p["state"]),{"cash":float(cap),"positions":[]})
    rows,eq,unreal=_rows_and_equity(st,{})
    kpi=sum(r["_premium"] for r in rows)
    life=sum(float(t["price"])*100*float(t["contracts"]) for t in _load_jsonl(Path(p["trades"])) if str(t.get("action","")).startswith("SELL_"))
    print(f'{cap//1000}k_N{n}: KPI "Premium collected"={_fmt(kpi)}  lifetime premium sold={_fmt(life)}')
EOF
rm -rf $R
```

**Finder evidence:**
```
$ cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python <script>
100k_N1: KPI "Premium collected"=$0.00  lifetime premium sold=$4,329.00
250k_N1: KPI "Premium collected"=$0.00  lifetime premium sold=$10,929.00
500k_N1: KPI "Premium collected"=$0.00  lifetime premium sold=$22,006.00
500k_N4: KPI "Premium collected"=$2,120.00  lifetime premium sold=$12,871.00
```

### `reporting:c1-mark-asof-inert-on-real-book` — HIGH — C1 "Mark as-of" column renders "—" (clean) for 31/31 real legs — including 9 legs that expired 3 days ago carrying $10,218 of liability inside the Equity KPI
**Where:** `dashboard/monitor.py`:381

**Claim:** The C1 carried-mark discriminator reads `short.get("mark_asof")` (monitor.py:124) and renders "—" when it is None (monitor.py:383-384). NOT ONE of the 31 open legs in the real synced book has a `mark_asof` key (the field is written by src/engine_v2/options/portfolio.py:503, added after the last live run), so the column renders "—" — the same cell a leg marked TODAY gets — for every position, while the bot has been paused since 2026-07-31. Nine of those legs (TMO 512.5P x6 accounts, RIG 4.5P x3) expired 2026-07-31: they render DTE = -3, Mark = 11.30 / 0.06, "vs strike" = +12.5% in green, "Mark as-of" = "—", and their $10,218.00 of carried-mark liability is subtracted inside the board's Equity and Total P&L metrics with no staleness marker. The adjacent "" column shows "stale" for every row unconditionally (it is `_live`, i.e. "no live quote pulled"), so it carries zero information about carried marks. TMO 512.5P @ 11.30 is verbatim the "$11.30-TMO class" the C1 docstring says this column exists to flag. The logic itself is correct when a stamp is present (mark_asof='2026-07-31' -> 'carried 2026-07-31'); it is inert on 100% of the real book.

**Repro:**
```
set -e; R=/tmp/f4rep3; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
import pandas as pd
from dashboard.monitor import _rows_and_equity,_load_json
from live.accounts import account_paths,all_accounts,account_label
T=pd.Timestamp.now().normalize(); tot=0.0
print("today",T.date())
for cap,n in all_accounts():
    st=_load_json(Path(account_paths(cap,n)["state"]),{"cash":float(cap),"positions":[]})
    rows,eq,unreal=_rows_and_equity(st,{})
    for r,pos in zip(rows,st.get("positions",[])):
        sh=pos.get("short")
        if not sh: continue
        exp=pd.Timestamp(sh["contract"]["expiry"]).normalize()
        if exp<T:
            liab=r["_mark"]*100*sh["contracts"]; tot+=liab
            asof=r["_asof"]
            cell=("live" if r["_live"] else "—" if asof in (None,"") or str(asof)>=str(T.date()) else f"carried {asof}")
            print(f'{account_label(cap,n):9} {r["Ticker"]:5} DTE={r["DTE"]:3} Mark={r["_mark"]} "Mark as-of"={cell!r} vs-strike={r["_dist"]:+.2%} liability_in_Equity=${liab:,.2f}')
print(f"TOTAL expired-leg liability inside board Equity/Total P&L: ${tot:,.2f}")
EOF
rm -rf $R
# NOTE: this repro is date-sensitive only in that 'expired' means expiry < today; it was run 2026-08-03.
```

**Finder evidence:**
```
$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python <script>
today 2026-08-03
5k_N3     RIG   DTE= -3 Mark=0.06 "Mark as-of"='—' vs-strike=+12.89% liability_in_Equity=$24.00
250k_N3   TMO   DTE= -3 Mark=11.3 "Mark as-of"='—' vs-strike=+12.54% liability_in_Equity=$1,130.00
500k_N3   TMO   DTE= -3 Mark=11.3 "Mark as-of"='—' vs-strike=+12.54% liability_in_Equity=$3,390.00
500k_N4   TMO   DTE= -3 Mark=11.3 "Mark as-of"='—' vs-strike=+12.54% liability_in_Equity=$2,260.00
TOTAL expired-leg liability inside board Equity/Total P&L: $10,218.00

$ ... state.json scan: positions 31  bare 0  with mark_asof 0
  short keys ['contract', 'contracts', 'credit', 'last_mid']
```

### `reporting:gap-banner-renders-false-no-activity` — HIGH — Gap banner renders "2 day(s) with no bot activity / Nothing ran" for 2026-07-23, a day on which 22 trades across 22 accounts moved -$12,748.30
**Where:** `dashboard/monitor.py`:192

**Claim:** `split_gap_records` classifies a record as a full miss iff `reason == "no_run"` and `correction is not True`. The authoritative synced ledger (data/live-synced/gaps.jsonl) contains exactly three plain records and ZERO corrections, so 2026-07-23 lands in `full_miss` and the banner renders the headline "⚠ 2 day(s) with no bot activity — 2026-07-21, 2026-07-23" followed by "Nothing ran; these cannot be backfilled ... The equity curve has holes on these dates." That is false for 2026-07-23: the trade ledgers hold 22 CLOSE_PUT rows dated 2026-07-23T10:00:15 across 22 of 25 accounts, moving -$12,748.30 of realized cash (the DOW campaign close). This is the exact date the C2 docstring names as the reason the fix exists; the fix is a no-op because it depends on a `correction: True` record that was never written to the real ledger. (Root cause is logged as F3a in the repair plan, but the rendered banner text on the live store is still a false claim, and it is the first thing on both pages.)

**Repro:**
```
set -e; R=/tmp/f4rep2; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from dashboard.monitor import split_gap_records,_load_jsonl
from live.gaps import gap_summary
from live.accounts import account_paths,all_accounts
fm,dg=split_gap_records(gap_summary()["records"])
print("banner FULL-MISS ('no bot activity'/'Nothing ran'):",fm)
for d,_ in fm:
    rows=0;cash=0.0
    for cap,n in all_accounts():
        tr=_load_jsonl(Path(account_paths(cap,n)["trades"]))
        for i,t in enumerate(tr):
            if str(t.get("date"))[:10]==d:
                rows+=1; cash+=t["cash_after"]-(tr[i-1]["cash_after"] if i else float(cap))
    print(f"  {d}: {rows} trade rows, realized cash delta ${cash:,.2f}")
EOF
rm -rf $R
```

**Finder evidence:**
```
$ cat data/live-synced/gaps.jsonl
{"date": "2026-07-21", "reason": "no_run", "detected": "reconstructed-from-logs"}
{"date": "2026-07-23", "reason": "no_run", "detected": "reconstructed-from-logs"}
{"date": "2026-07-24", "reason": "pull_failure", "skipped": 547, ...}

$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python <script>
banner FULL-MISS ('no bot activity'/'Nothing ran'): [('2026-07-21', 'no_run'), ('2026-07-23', 'no_run')]
  2026-07-21: 0 trade rows, realized cash delta $0.00
  2026-07-23: 22 trade rows, realized cash delta $-12,748.30
```

### `reporting:undisclosed-0731-equity-hole` — MED — 2026-07-31 is a full trading day of activity (-$18,415.80 realized) that is absent from every equity curve AND absent from the gap ledger — no page discloses it
**Where:** `dashboard/monitor.py`:177

**Claim:** Snapshot dates present across all 25 accounts are 07-20, 07-22, 07-24, 07-27, 07-28, 07-29, 07-30. Trade dates present are 07-20, 07-23, 07-24, 07-27, 07-28, 07-29, 07-31. 2026-07-31 has 36 trade rows in 20 accounts moving -$18,415.80 of realized cash, has NO snapshot in any account, and is NOT in gaps.jsonl. Consequence: (a) `gap_banner()` says nothing about it — the disclosure surface only shows what the ledger holds; (b) every snapshot-derived Compare column (Equity, Return, Max DD, Sharpe, last_snap_date) stops at 07-30 and silently omits the day; 100k_N1 shows Compare Equity $102,521.70 while its actual cash is $102,714.30. The only hint is the soft caption "board pages read state.json and may be newer", which frames the difference as a clock skew rather than a missing session. Same class as 07-23, which at least got a (mislabelled) ledger row.

**Repro:**
```
set -e; R=/tmp/f4rep4; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from dashboard.monitor import _load_jsonl
from live.accounts import account_paths,all_accounts
from live.gaps import gap_summary
tot=0.0;n31=0;snapd=set();trad=set()
for cap,n in all_accounts():
    tr=_load_jsonl(Path(account_paths(cap,n)["trades"]))
    for i,t in enumerate(tr):
        trad.add(str(t.get("date"))[:10])
        if str(t.get("date"))[:10]=="2026-07-31":
            tot+=t["cash_after"]-(tr[i-1]["cash_after"] if i else float(cap)); n31+=1
    for s in _load_jsonl(Path(account_paths(cap,n)["snapshots"])): snapd.add(str(s["date"])[:10])
print(f"2026-07-31: {n31} trade rows, realized cash delta ${tot:,.2f} | in gap ledger? {'2026-07-31' in gap_summary()['dates']} | in any snapshot? {'2026-07-31' in snapd}")
print("  snapshot dates:",sorted(snapd))
print("  trade dates absent from snapshots (undisclosed curve holes):",sorted(trad-snapd))
print("  gap-ledger dates:",gap_summary()['dates'])
EOF
rm -rf $R
```

**Finder evidence:**
```
$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python <script>
2026-07-31: 36 trade rows, realized cash delta across 25 accounts = $-18,415.80  |  in gap ledger? False  |  in any snapshot? False
  snapshot dates present: ['2026-07-20','2026-07-22','2026-07-24','2026-07-27','2026-07-28','2026-07-29','2026-07-30']
  trade dates absent from snapshots (undisclosed curve holes): ['2026-07-23', '2026-07-31']
  gap-ledger dates: ['2026-07-21', '2026-07-23', '2026-07-24']

(compare vs board, same account) 100k_N1 compare Equity=102,521.70 ; board Equity/state cash=102,714.30 ; last cash_after=102,714.30
```

### `reporting:one-truncated-line-blanks-whole-dashboard` — MED — A single truncated append in any one account's snapshots.jsonl/trades.jsonl raises JSONDecodeError and takes down BOTH dashboard pages
**Where:** `live/compare.py`:63

**Claim:** `live.compare._read_jsonl` (compare.py:59-65) and `dashboard.monitor._load_jsonl` (monitor.py:44-47) both call `json.loads` on every non-blank line with no guard. snapshots.jsonl and trades.jsonl are written with a plain non-atomic append (`open(path,"a")`, live/snapshots.py:58), so a crash/disk-full mid-append leaves a partial line. One such line in ONE of the 25 accounts propagates out of `account_metrics()` and kills the entire Compare page, out of `grid_concentration()` (so even the C7 honesty header dies), and out of `_load_jsonl` in `board()` so every capital page dies too. By contrast live/gaps.py deliberately skips corrupt lines and explicitly documents why ("a half-written line must not blind the idempotency check") — the far more load-bearing per-account ledgers do not get that treatment. state.json is safe (atomic temp+os.replace, live/state.py:180-185), but monitor._load_json only catches FileNotFoundError, not JSONDecodeError, so it is one class less robust than compare._read_json.

**Repro:**
```
set -e; R=/tmp/f4rep5; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; printf '{"date": "2026-07-31T00:00:00", "equity": 1027' >> $R/store/accounts/100k_N1/snapshots.jsonl; printf '{"date": "2026-08-0' >> $R/store/gaps.jsonl; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from live.compare import account_metrics,grid_concentration
from dashboard.monitor import _load_jsonl
from live.gaps import gap_summary
from live.accounts import account_paths
for name,fn in [("account_metrics",account_metrics),("grid_concentration",grid_concentration)]:
    try: fn(); print(name,"OK")
    except Exception as e: print(name,"RAISED",type(e).__name__,e)
try: _load_jsonl(Path(account_paths(100000,1)["snapshots"])); print("monitor._load_jsonl OK")
except Exception as e: print("monitor._load_jsonl (board page) RAISED",type(e).__name__,e)
print("gap_summary on corrupt ledger ->",gap_summary()['dates'],"(no exception)")
EOF
rm -rf $R
```

**Finder evidence:**
```
$ printf '{"date": "2026-07-31T00:00:00", "equity": 1027' >> $R/store/accounts/100k_N1/snapshots.jsonl
$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python -c "..."
account_metrics RAISED JSONDecodeError Expecting ',' delimiter: line 1 column 47 (char 46)
grid_concentration: RAISED JSONDecodeError: Expecting ',' delimiter: line 1 column 47 (char 46)
monitor._load_jsonl (board page): RAISED JSONDecodeError: Expecting ',' delimiter: line 1 column 47 (char 46)
gap_summary on corrupt ledger -> ['2026-07-21', '2026-07-23', '2026-07-24'] (no exception)
```

### `reporting:board-equity-marked-at-mid-not-ask` — MED — Board Equity / Open unrealized are marked at last_mid for 31/31 real legs — the basis the code's own comment says "would print a friendlier number than the account it is describing" — with no board-level disclosure
**Where:** `dashboard/monitor.py`:100

**Claim:** `mark = marks.get(tk, short.get("last_ask", short.get("last_mid", credit)))`. The comment above it declares the ASK is the standard (owner decision B, 2026-07-31) and warns that "falling back to last_mid here would print a friendlier number than the account it is describing." Every one of the 31 open legs in the real synced book has only `last_mid` and no `last_ask` (the field is written by src/engine_v2/options/portfolio.py:497, added after the last live run), so the fallback fires on 100% of the book and the headline Equity / Total P&L / Open unrealized are all mid-based. For a short leg ask > mid, so the liability is understated and Equity is flattered — exactly the failure the comment names. Nothing on the board says so: `mark_basis_line` (C11) is rendered in the same caption but is scoped to the SNAPSHOT curve (`mark_basis` on snapshot rows), not to the live KPI row, and it does not mention the position marks at all.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && python3 -c "
import json,glob,os
k=set(); n=0; ask=0
for f in sorted(glob.glob('data/live-synced/accounts/*/state.json')):
    for p in json.load(open(f)).get('positions',[]):
        s=p.get('short')
        if s: n+=1; k|=set(s); ask += 1 if 'last_ask' in s else 0
print('short legs:',n,'with last_ask:',ask,'short keys seen:',sorted(k))" && grep -n 'last_ask' dashboard/monitor.py
```

**Finder evidence:**
```
$ python3 -c "scan of all data/live-synced/accounts/*/state.json"
positions 31 bare 0 with mark_asof 0
short keys ['contract', 'contracts', 'credit', 'last_mid']      <-- no 'last_ask' anywhere

$ grep -n 'last_ask' dashboard/monitor.py src/engine_v2/options/portfolio.py
dashboard/monitor.py:100:            mark = marks.get(tk, short.get("last_ask", short.get("last_mid", credit)))
src/engine_v2/options/portfolio.py:497:                pos["short"]["last_ask"] = mk.ask

$ board render 500k_N4: KPI Equity=$502,648.75 | Open unrealized=-$4,040.00   (TMO mark 11.30 = last_mid; DECK mark 3.00 = last_mid)
```

### `reporting:markbasis-caveat-false-on-real-store` — LOW — C11 mark-basis line asserts "early and late equity are not like-for-like" on a store whose every snapshot predates the changeover the same sentence names
**Where:** `dashboard/monitor.py`:283

**Claim:** When all snapshot rows are unstamped, `mark_basis_line` returns "<n> pre-stamp row(s) of unknown mark basis (the mid → ask changeover 2026-07-31 predates stamping) — early and late equity are not like-for-like". On the real store all 7 deduped snapshots are dated 2026-07-20..2026-07-30, i.e. every one strictly before the 2026-07-31 changeover the same string names, so they are provably all one basis (mid) and ARE like-for-like. The function hardcodes the changeover date in its own message but never compares it to the row dates, so it emits a not-comparable warning about a uniformly-comparable series. Rendered identically on all 25 board pages.

**Repro:**
```
set -e; R=/tmp/f4rep6; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from dashboard.monitor import mark_basis_line,dedupe_snaps,_load_jsonl
from live.accounts import account_paths
s=dedupe_snaps(_load_jsonl(Path(account_paths(100000,1)["snapshots"])))
print("snapshot dates:",[str(x["date"])[:10] for x in s])
print("mark_basis_line:",mark_basis_line(s))
print("all snapshot dates < 2026-07-31 (the changeover the line names)? ",all(str(x["date"])[:10]<"2026-07-31" for x in s))
EOF
rm -rf $R
```

**Finder evidence:**
```
$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python <script>
snapshot dates: ['2026-07-20','2026-07-22','2026-07-24','2026-07-27','2026-07-28','2026-07-29','2026-07-30']
mark_basis_line: 7 pre-stamp row(s) of unknown mark basis (the mid → ask changeover 2026-07-31 predates stamping) — early and late equity are not like-for-like
all snapshot dates < 2026-07-31 (the changeover the line names)?  True
```

### `reporting:concentration-line-none-crash` — LOW — concentration_line() raises TypeError when total premium is $0, contradicting its "Always rendered" contract and killing the Compare page
**Where:** `dashboard/monitor.py`:225

**Claim:** `grid_concentration` returns `top_premium_share = premium[top]/total_prem if top and total_prem else None`. When at least one SELL_ row exists but the summed premium is 0.0 (all rows priced at 0.0, or contracts 0), `top_ticker` is set while `top_premium_share` is None. `concentration_line` then evaluates `{g['top_premium_share']*100:.0f}%` -> TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'. This is raised at the top of `compare_page()` (monitor.py:463), so the whole Compare page dies. The C7 docstring explicitly promises "Always rendered; a young store says so instead of going silently blank" — the None-guard covers only the `n_eff is None` branch, not this one. Same shape exists for `replication` at monitor.py:226 (`{g['replication']:.1f}` on None when `decisions` is empty).

**Repro:**
```
set -e; R=/tmp/f4rep7; rm -rf $R; mkdir -p $R/accounts; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; PYTHONPATH=. .venv-live/bin/python - <<'EOF'
import json,os
from live.accounts import all_accounts,account_label
import live.compare as lc, dashboard.monitor as dm
root="/tmp/f4rep7/accounts"
for cap,n in all_accounts():
    d=os.path.join(root,account_label(cap,n)); os.makedirs(d,exist_ok=True)
    open(os.path.join(d,"trades.jsonl"),"w").write(json.dumps({"date":"2026-08-01","action":"SELL_PUT","ticker":"ZZZ","strike":1.0,"expiry":"2026-08-21","contracts":1,"price":0.0,"cash_after":cap,"campaign":1})+"\n")
    with open(os.path.join(d,"snapshots.jsonl"),"w") as f:
        for k,e in enumerate([cap,cap*1.01,cap*0.99,cap*1.02]):
            f.write(json.dumps({"date":f"2026-08-0{k+1}","equity":e})+"\n")
g=lc.grid_concentration(root); print("grid_concentration:",g)
lc.grid_concentration=lambda: g
try: print(dm.concentration_line())
except Exception as e: print("concentration_line RAISED",type(e).__name__,e)
EOF
rm -rf $R
```

**Finder evidence:**
```
$ synthetic 25-account store, every SELL_PUT priced 0.0:
grid_concentration on $0-price SELL store: {'n_decisions': 1, 'n_sell_rows': 25, 'replication': 25.0, 'rho_bar': 1.0, 'n_eff': 1.0, 'top_ticker': 'ZZZ', 'top_premium_share': None}
concentration_line RAISED TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'
```

### `reporting:sharpe-n-counts-synthetic-day0` — LOW — "— (n=7)" Sharpe suppression count includes the synthetic C10 day-0 return; the store holds only 6 real snapshot-to-snapshot returns
**Where:** `live/compare.py`:51

**Claim:** `_equity_series(..., capital=...)` prepends a synthetic day-0 row at starting capital (compare.py:91-94), then `_sharpe_stats` reports `sharpe_n = len(eq.pct_change().dropna())`. For 100k_N1 that is 8 points -> n=7, but the account has only 7 deduped snapshots and therefore 6 real between-snapshot returns; the 7th is capital->first-snapshot. The Compare cell renders "— (n=7)" beside the caption "Sharpe hidden below 30 observations", so the displayed observation count is one higher than the number of recorded sessions on the deduped index. The same synthetic point sets the annualisation window: the day-0 index is 2026-07-19, a Sunday, giving years=11/365.25 and ppy=232.4 rather than the trading-day frequency the caption calls "actual observation frequency".

**Repro:**
```
set -e; R=/tmp/f4rep8; rm -rf $R; mkdir -p $R; cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot; cp -R data/live-synced $R/store; rm -rf $R/store/.git; WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python - <<'EOF'
from pathlib import Path
from dashboard.monitor import dedupe_snaps,_load_jsonl,fmt_sharpe
from live.accounts import account_paths
from live.compare import _equity_series,_sharpe_stats
import pandas as pd
s=_load_jsonl(Path(account_paths(100000,1)["snapshots"])); d=dedupe_snaps(s)
eq=_equity_series(s,capital=100000)
print("raw snaps",len(s),"deduped",len(d),"series pts (w/ day0)",len(eq))
print("index",[str(i.date()) for i in eq.index])
print("sharpe_stats",_sharpe_stats(eq))
print("real snapshot-to-snapshot returns =",len(d)-1)
EOF
rm -rf $R
```

**Finder evidence:**
```
$ WHEELBOT_STATE_DIR=$R/store PYTHONPATH=. .venv-live/bin/python -c "..."
raw snaps 8 deduped 7 series pts (w/ day0) 8
index ['2026-07-19', '2026-07-20', '2026-07-22', '2026-07-24', '2026-07-27', '2026-07-28', '2026-07-29', '2026-07-30']
sharpe_stats {'sharpe': 8.279583850874966, 'sharpe_se': 6.172607373985405, 'sharpe_n': 7, 'sharpe_suppressed': True}
real snapshot-to-snapshot returns = 6
(compare grid cell renders: 100k_N1  sharpe=— (n=7))
```

### `reporting:dead-day-pnl` — LOW — board() computes prev_equity/day_pnl and renders neither — there is no day-over-day P&L anywhere on the dashboard
**Where:** `dashboard/monitor.py`:331

**Claim:** monitor.py:329-331 compute `prev_equity = snaps[-1].get("equity", baseline)` and `day_pnl = equity - prev_equity`, then neither name is referenced again anywhere in the file. The five KPI columns are Equity / Total P&L / Premium collected / Open unrealized / Cash. Dead code, and a functional gap: the owner has no session-over-session P&L on any page (the intended reading of the state-vs-snapshot delta, e.g. +$192.60 for 100k_N1, is computed and thrown away).

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -n 'day_pnl\|prev_equity' dashboard/monitor.py && echo '--- occurrences above; both assigned once, never read ---'
```

**Finder evidence:**
```
$ grep -n 'day_pnl\|prev_equity' dashboard/monitor.py
329:    prev_equity = snaps[-1].get("equity", baseline) if snaps else baseline
331:    day_pnl = equity - prev_equity
(no other occurrences — both are write-only)
```

## domain: state-persistence  (8 findings)

**Finder summary:** I audited the live state/persistence surface by executing probes in an isolated scratch store (WHEELBOT_STATE_DIR set before interpreter start; data/live and data/live-synced were read-only — verified no writes). Round-trip fidelity is clean: a PortfolioState with every optional field populated (working_order, ca_frozen, ca_watch, recon_frozen, assigned_d, last_ask, opened_contracts, mark_asof, mark_quote_time, intraday_closed) saves→loads→saves byte-identical, and all 101 real state.json/.prev files under data/live + data/live-synced round-trip to an identical dict — no A21b-class field drop survives. save_state's atomicity/durability (tmp+fsync+os.replace+dir fsync) is sound. The live suite is green (376 passed). The defects that survive are in the APPEND-ONLY files and the ledger→disclosure mapping, not in state.py's serializer: a failed append to snapshots.jsonl permanently disables the double-step guard and silently eats the next record; the gaps ledger mislabels days in BOTH directions (a day with 22 real TP closes is currently displayed as "Nothing ran", and a day where nothing stepped is displayed as "the bot DID act"); the documented manual state edit is silently reverted by a concurrent runner save; and D11's day-boundary backup can be permanently torn with no detection.

### `state-persistence:1` — HIGH — A failed append to snapshots.jsonl permanently disables the already_stepped double-step guard and silently destroys the next record
**Where:** `live/snapshots.py`:55

**Claim:** append_snapshot (live/snapshots.py:55) writes with a bare `open(path,"a"); f.write(json.dumps(snap)+"\n")` — no atomicity, no fsync, no trailing-newline repair. Any write that fails partway (disk full / ENOSPC, quota, EFBIG, kill) leaves a torn line with no terminating newline. From that moment: (a) load_snapshots (live/snapshots.py:62) raises JSONDecodeError; (b) already_stepped (live/run_daily.py:115) catches ValueError at line 126 and returns False for EVERY date — the guard that exists solely to stop the 17:00 retry window double-stepping a day is dead, permanently and silently, not just for the corrupt date; (c) the NEXT successful append concatenates onto the torn line, so that snapshot row is destroyed too (file grows but line count does not). Nothing detects, repairs, or alerts on this. compare.py:_read_jsonl has the identical shape (json.loads per line, no guard), so the dashboard Compare page breaks on the same file.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && .venv/bin/python /private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/state-persistence/p9_enospc.py   # (simulates disk-full via RLIMIT_FSIZE); then .venv/bin/python .../f4/state-persistence/p2_jsonl.py  # (shows the next append merging into the torn line)
```

**Finder evidence:**
```
p9_enospc.py (RLIMIT_FSIZE capped mid-append, SIGXFSZ ignored so it surfaces as EFBIG exactly like ENOSPC):
  append FAILED as a disk-full would: OSError [Errno 27] File too large
  file now: '{"date": "2026-08-03T00:00:00", "equity": 100000.0, "cash": 1.0}\n{"date": "2026-08-04T00:00:00", "equity"'
  already_stepped('2026-08-03') for the day that DID complete: False
  load_snapshots RAISED: JSONDecodeError Expecting ':' delimiter: line 1 column 41 (char 40)

p2_jsonl.py (next append merges into the torn line — 3 records written, 2 lines on disk):
  C) after next append, lines = 2
  '{"date": "2026-08-03T00:00:00", "equity": 100.0, "cash": 1.0}\n{"date": "2026-08-04T00:00:00", "equ{"date": "2026-08-04T00:00:00", "equity": 200.0}\n'
     already_stepped('2026-08-04'): False
     already_stepped('2026-08-03'): False
```

### `state-persistence:2` — HIGH — gaps.jsonl currently labels 2026-07-23 `no_run` and the dashboard prints "Nothing ran" — 22 real TP closes were booked that day
**Where:** `live/health.py`:98

**Claim:** check_day (live/health.py:98) decides a day was missed purely from the absence of a `.dailyran-<date>` marker and writes reason `no_run`. It never looks at trades.jsonl or the intraday log. split_gap_records (dashboard/monitor.py:170) then puts every plain `no_run` in the full_miss class, which the banner renders as "day(s) with no bot activity … Nothing ran; these cannot be backfilled … The equity curve has holes on these dates." On a day where the intraday exit manager ran and booked take-profit closes but the 17:00 EOD run never completed, that statement is false — real cash moved. This is live right now: the real ledger at data/live-synced/gaps.jsonl carries `{"date":"2026-07-23","reason":"no_run"}`, while data/live-synced/accounts/*/trades.jsonl carries 22 CLOSE_PUT fills stamped 2026-07-23T10:00-10:15 with a net cash delta of -$12,748.30. split_gap_records' own docstring names this exact case ("07-23 closed the DOW campaign … while labelled no_run") as the defect it was written to fix, and the correction machinery (append_correction) exists — but no correction record was ever filed and nothing in the running system ever files one, so the false claim is still on screen.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && .venv/bin/python /private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/state-persistence/p5_gaps.py   # real-data proof; then .venv/bin/python .../f4/state-persistence/p6_norun.py   # from-scratch systemic repro in a scratch store
```

**Finder evidence:**
```
p5_gaps.py (against the REAL data/live-synced/gaps.jsonl):
  full_miss  ('Nothing ran; the equity curve has holes'): [('2026-07-21', 'no_run'), ('2026-07-23', 'no_run')]
  degraded   ('The bot DID act on these days'): [('2026-07-24', 'pull_failure')]
  trades booked per date (all 25 accounts): {'2026-07-20': 68, '2026-07-23': 22, '2026-07-24': 22, '2026-07-27': 35, '2026-07-28': 74, '2026-07-29': 2, '2026-07-31': 36}

Direct ledger read: 2026-07-23 CLOSE_PUT rows: 22  net cash delta across accounts: -12748.30

p6_norun.py (scratch store: intraday log + one booked trade on 2026-07-23, no .dailyran marker):
  check_day -> ('gap_recorded', ['2026-07-23'])
  ledger: {"date": "2026-07-23", "reason": "no_run"}
  dashboard full_miss  (banner: 'Nothing ran'): [('2026-07-23', 'no_run')]
  dashboard degraded   (banner: 'The bot DID act'): []
```

### `state-persistence:3` — HIGH — A totally-dead day recorded as pull_failure/all_accounts_failed is displayed as "the bot DID act … do not read them as holes" AND is excluded from the missed-day alert
**Where:** `dashboard/monitor.py`:170

**Claim:** run_daily records `pull_failure` (live/run_daily.py:658) and `all_accounts_failed` (live/run_daily.py:817) for days on which, by its own message, "No state was touched" / "all accounts failed to step" — i.e. genuine holes in the equity curve. Because append_gap is idempotent PER DATE (live/gaps.py:65), health's 23:45 dead-man's-switch call `append_gap(day,"no_run")` (live/health.py:98) returns False and no `no_run` record is ever written. Two consequences: (1) split_gap_records (dashboard/monitor.py:170) classifies any reason != "no_run" as *degraded*, so the banner tells the operator "The bot DID act on these days -- … Do not read them as holes" about a day on which nothing ran; (2) unalerted_gaps (live/health.py:139) filters on `reason != "no_run"` and skips it, so the D3/D12 missed-day email — the one with the .gapalerted delivered-marker retry that exists precisely so a failed send cannot lose the alarm — never fires for that date.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && .venv/bin/python /private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/state-persistence/p7_pullfail.py
```

**Finder evidence:**
```
p7_pullfail.py (scratch store; simulates a lapsed token at 17:05 that is never restored, then the 23:50 health pass):
  run_daily 17:05 -> True
  run_daily 17:10 -> False
  check_day 23:50 -> ('already_recorded', ['2026-07-23'])
  ledger: {"date": "2026-07-23", "reason": "pull_failure", "skipped_closes": 547, "universe": 547, "chain_attempts": 0, "chains_ok": 0}
  dashboard full_miss ('Nothing ran; equity curve has holes'): []
  dashboard degraded  ('The bot DID act on these days ... Do not read them as holes'): [('2026-07-23', 'pull_failure')]
  unalerted_gaps (health's missed-day email list): []
```

### `state-persistence:4` — MED — Manual state.json edits (the only documented way to clear an A10 freeze or apply an A8 restatement) are silently reverted by a concurrent runner save
**Where:** `live/state.py`:165

**Claim:** The CORPORATE ACTION alert (live/run_daily.py:760) instructs the operator to "clear ca_frozen in the state file" by hand, and live/reconcile.py's A8 lifecycle is explicitly MANUAL clearing ("a human applies the restatement to the notebook by hand"). save_state (live/state.py:165) writes the caller's whole in-memory PortfolioState over the file with no lock, no mtime/version check, and no read-modify-write. Both runners hold a loaded state across a long window before saving — run_daily loads every account at live/run_daily.py:508 and saves minutes later at :269, after a 547-name market pull; run_intraday loads at :69 and saves at :78. An operator edit landing inside that window is silently discarded, including the money-bearing part of the restatement (share count / basis), with no error and no diff.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && .venv/bin/python /private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/state-persistence/p10_lostupdate.py
```

**Finder evidence:**
```
p10_lostupdate.py (runner loads state -> operator clears ca_frozen and restates XOP 400 shares -> 100 on disk -> runner books a TP on the OTHER position and saves):
  runner loaded; ca_frozen present: True
  operator edit on disk; ca_frozen present: False
  after runner save -> ca_frozen present: True | shares: 400 (operator set 100)
```

### `state-persistence:5` — MED — D11 day-boundary backup: a torn copy is never re-taken, never validated, never read
**Where:** `live/state.py`:138

**Claim:** _day_boundary_backup (live/state.py:138) takes the dated recovery copy only when `not os.path.exists(bak)`. shutil.copy2 is not atomic, so a crash/ENOSPC during that copy leaves state.json.bak-<date> present but truncated — and the existence check then guarantees it is never re-taken for the rest of that ET day. The artifact is also never read back by any code (grep: no reader of `.bak-` or `.prev` anywhere outside state.py itself) and never validated, so the whole D11 guarantee ("yesterday's day-boundary state survives the whole day") can be silently false with zero signal. By contrast the same function's sibling write path (save_state) is fully atomic — the recovery artifact is the one write that is not.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && .venv/bin/python /private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/state-persistence/p8_bak.py; grep -rn '\.prev\|bak-' --include='*.py' /Users/georgiemanavazian/Documents/Trading/code/etf-bot | grep -v tests | grep -v state.py
```

**Finder evidence:**
```
p8_bak.py:
  torn bak exists: True len 270 of 540
  after 5 more saves, bak size: 270
  bak parses: NO -> JSONDecodeError Unterminated string starting at: line 15 column 7 (char 262)

grep for any reader of the artifacts (outside live/state.py's own writer):
  live/sync.py:152:        # *.bak-* are recovery artifacts and MUST stay synced), plus cleanup
  (no other hit — nothing loads .prev or .bak-*)
```

### `state-persistence:6` — MED — No gap record is ever retracted when a later retry tick in the same window succeeds
**Where:** `live/run_daily.py`:658

**Claim:** run_daily writes `pull_failure` (line 658) and `all_accounts_failed` (line 817) on conditions the 5-minute retry window is explicitly designed to recover from — the message itself says "the next tick will retry" and the tick script says "The window runs late on purpose (a 20:30 re-login evening stays recoverable)". If a later tick in the 17:00-23:30 window then completes the day cleanly, nothing files a correction: append_correction is called from exactly one site in the whole codebase (live/run_daily.py:836, the PARTIAL-failure branch); the success path files nothing. gap_summary therefore reports that fully-traded day as a gap forever and the dashboard banner attaches a permanent false caveat to it. Same for `chain_snapshot_missing`/`chain_snapshot_incomplete` if the situation is later resolved.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn 'append_correction' --include='*.py' . | grep -v '\.venv' | grep -v '/tests/'   # only gaps.py (def) + run_daily.py:25 (import) + run_daily.py:472 (bind) + run_daily.py:836 (partial-failure branch); then: grep -n '_gap(\|_correction(' live/run_daily.py
```

**Finder evidence:**
```
$ grep -n '_gap(\|_correction(' live/run_daily.py
  467:        def _gap(date, reason, **kw):
  606:            _gap(day, "chain_snapshot_missing")
  640:            _gap(day, "chain_snapshot_incomplete", missing=missing,
  658:        _gap(day, "pull_failure",
  817:        _gap(day, "all_accounts_failed", accounts=len(accounts))
  835:        if not _gap(day, "accounts_failed", accounts=failed):
  836:            _correction(day, "accounts_failed", accounts=failed)

-> four gap-writing sites, one correction site, and it is inside the partial-failure branch only. The success return path (run_daily.py:838 `return 0`) writes nothing.

Supporting: p7_pullfail.py shows append_gap('2026-07-23','pull_failure') -> True at 17:05 and the record persisting unmodified in the ledger afterwards.
```

### `state-persistence:7` — LOW — working_order round-trip machinery in state.py has no producer anywhere in the system
**Where:** `live/state.py`:26

**Claim:** live/state.py:26-30 and :69-70 serialize and restore a position's `working_order` field (documented as A17 "an order a fill model has working"). No code anywhere in src/ or live/ ever sets `working_order` on a position — the only occurrences of the string in the entire non-test codebase are the four lines in state.py itself. The field can therefore never be non-None in practice; the persistence contract it advertises is untested-in-anger dead code.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn 'working' --include='*.py' src live | grep -v tests
```

**Finder evidence:**
```
$ grep -rn 'working' --include='*.py' src live | grep -v tests
  live/state.py:26:    # A17: an order a fill model has working (JSON-plain dict). Absent == none;
  live/state.py:29:    if p.get("working_order") is not None:
  live/state.py:30:        d["working_order"] = p["working_order"]
  live/state.py:69:    if d.get("working_order") is not None:
  live/state.py:70:        p["working_order"] = d["working_order"]

(4 hits, all inside the serializer. Zero producers.)
```

### `state-persistence:8` — LOW — Snapshot `written_at` (C8 self-dating) is a naive local timestamp and has zero readers
**Where:** `live/snapshots.py`:50

**Claim:** live/snapshots.py:50 stamps `"written_at": pd.Timestamp.now().isoformat()` — naive, no timezone suffix, taken from the box clock, which the tick script states is deliberately left at UTC ("All market-hours gating is done here in ET, so the system clock can stay UTC"). The row's `date` field is an ET trading day, so the two fields are in different, unlabelled time bases; a reader off-box cannot tell which. More to the point, nothing reads it: `written_at` appears exactly once in the whole non-test codebase. C8's stated purpose ("a snapshot row consumed off-box can say when it was actually written") is not achieved — it is the write-only-disclosure pattern the audit exists to remove.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn 'written_at' --include='*.py' . | grep -v '\.venv' | grep -v '/tests/'
```

**Finder evidence:**
```
$ grep -rn 'written_at' --include='*.py' . | grep -v '/tests/'
  live/snapshots.py:50:        "written_at": pd.Timestamp.now().isoformat(),

(single hit: one writer, no reader. Also confirmed naive: pd.Timestamp.now() with no tz argument.)
```

## domain: data-quality  (7 findings)

**Finder summary:** I audited the data-ingestion surface of the repaired live bot — live/data.py (chain/quote/close parsing), live/held_legs.py (held-leg pull + merge), live/chain_store.py (RTH snapshot round-trip), live/market_live.py (LiveMarket + add_chain_rows), live/universe.py, and the consuming gates in live/run_daily.py and live/run_chain_snapshot.py — by feeding adversarial payloads to the real parsers in a scratch sandbox with WHEELBOT_STATE_DIR pointed away from the frozen archive (verified: no repo file written, `find live src data/live -newermt today` empty). Baseline was green (376 live tests pass in 9.21s), so none of this is caught by the existing suite. Two HIGH defects survive the repair: pull_held_quotes still raises on a malformed quote payload despite the plan's explicit "never raises" claim, and I proved through the real run_chain_snapshot.main() that this destroys the whole day's RTH chain snapshot for all 25 accounts before any save; and a held leg whose ticker's chain came back present-but-EMPTY (which add_chain_rows' own docstring calls "routine for a thin name" — RIG is held in 3 accounts today) is dropped from the merge and appears in NO stat, so its take-profit is silently suspended while the run prints a success-shaped line. Three MED/LOW items follow: no ask>=bid or mid-in-spread sanity anywhere on the mark/close path (a crossed book books a phantom $0.01 take-profit fill), snapshot_pulled_at is the one unvalidated field in chain_store and its TypeError escapes the 17:00 run's only guard, select_roll_contract is the sole selection site missing the held_only security filter (latent — the live engine refuses rolls), plus B1's ticker-less drop message and B10's fail-open multiplier check. Universe/_RETIRED integrity, ET-vs-UTC candle stamping, and load_chain_snapshot/load_held_rows row validation all checked clean.

### `data-quality:1` — HIGH — pull_held_quotes violates its documented "never raises" contract: payload parsing sits outside the guard, and one malformed quote node destroys the whole day's RTH chain snapshot for all 25 accounts
**Where:** `live/held_legs.py`:128

**Claim:** live/held_legs.py:pull_held_quotes is documented (and the repair plan line 324 asserts) "pull half, never raises — incl. the contract derivation, skeptic F1". The try/except only wraps client.get_quotes()/.json(). The two statements that consume the UNTRUSTED payload — `out["answered"] = sum(... isinstance(data.get(sym), dict) ...)` and `out["rows_by_ticker"] = rows_from_quotes(data, contracts, obs)` — are outside every guard. rows_from_quotes does `q.get("quote", {}).get(...)`, `q.get("reference", {}).get(...)` and `int(dte)` with no coercion, so a non-dict quote/reference node raises AttributeError and a non-integral daysToExpiration raises ValueError; neither is caught. In run_chain_snapshot.main() the call `pulled = pull_held_quotes(client, position_lists, obs)` is unguarded and sits BEFORE every save_chain_snapshot call site — including the designed "held pull failed -> save the chains anyway, exit 1" branch. So the exception escapes main(), nothing is saved, and every remaining in-window tick dies identically. run_daily then finds no snapshot -> snapshot_missing_outcome()=='gap' -> the whole trading day is skipped for all 25 accounts with no live fallback (owner decision 2026-07-31 forbids one). This is the exact A21-skeptic-F1 failure class the repair claims to have closed, surviving one function further down the same call chain.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; S=/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/data-quality; mkdir -p $S/state; cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import pandas as pd, types, sys, os
assert "/scratchpad/" in os.environ["WHEELBOT_STATE_DIR"]
from live.held_legs import pull_held_quotes
class C:
    def __init__(s,d): s.d=d
    def get_quotes(s,syms): return s
    def json(s): return s.d
pos=[[{"ticker":"RIG","short":{"contract":{"root":"RIG","expiry":"2026-08-21","strike":3.5,"right":"P"}}}]]
sym="RIG   260821P00003500"; obs=pd.Timestamp("2026-08-03")
cases={"quote node is a list":{sym:{"quote":[],"reference":{}}},
 "reference node is a list":{sym:{"quote":{"bidPrice":0.01,"askPrice":0.05},"reference":[]}},
 "daysToExpiration junk str":{sym:{"quote":{"bidPrice":0.01,"askPrice":0.05},"reference":{"daysToExpiration":"n/a"}}},
 "daysToExpiration NaN":{sym:{"quote":{"bidPrice":0.01,"askPrice":0.05},"reference":{"daysToExpiration":float("nan")}}},
 "quote node is a string":{sym:{"quote":"unavailable"}}}
for n,p in cases.items():
    try: print("OK   ",n,pull_held_quotes(C(p),pos,obs)["error"])
    except Exception as e: print("RAISE",n,"->",type(e).__name__,e)
PY

# blast radius through the REAL runner (no network; scratch state dir only):
cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import os,sys,types,pandas as pd
assert "/scratchpad/" in os.environ["WHEELBOT_STATE_DIR"]
SYM="RIG   260821P00003500"
BAD={SYM:{"quote":{"bidPrice":0.01,"askPrice":0.05},"reference":{"daysToExpiration":"n/a"}}}
class FakeClient:
    def get_quotes(s,syms):
        r=types.SimpleNamespace(); r.json=lambda: BAD; return r
st_=types.ModuleType("schwab_client"); st_.get_client=lambda: FakeClient()
sys.modules["schwab_client"]=st_
import live.run_daily as rd, live.accounts as acc, live.state as st, live.chain_store as cs, live.run_chain_snapshot as rcs
class FM:
    def __init__(s):
        s._chains={"RIG":pd.DataFrame()}; s.truncated_closes=[]; s.skipped_closes=[]
        s.skipped_chains=[]; s.chain_attempts=1; s.chains_ok=1
rd._live_market=lambda *a,**k: FM()
acc.all_accounts=lambda: [(100_000,1)]
acc.account_paths=lambda cap,n: {"state":"unused"}
st.load_state=lambda p: types.SimpleNamespace(positions=[{"ticker":"RIG","phase":"PUT","short":{"contract":{"root":"RIG","expiry":"2026-08-21","strike":3.5,"right":"P"}}}])
saved=[]; cs.save_chain_snapshot=lambda *a,**k: (saved.append(k), "<would-save>")[1]
sys.argv=["run_chain_snapshot","--force"]
try: print("main() returned", rcs.main())
except Exception as e: print(f"main() RAISED {type(e).__name__}: {e}")
print("save_chain_snapshot called:", len(saved), "time(s)")
PY
```

**Finder evidence:**
```
$ .venv-live/bin/python p1_neverraises.py
RAISE 'quote node is a list'                 -> AttributeError: 'list' object has no attribute 'get'
RAISE 'reference node is a list'             -> AttributeError: 'list' object has no attribute 'get'
RAISE 'daysToExpiration is junk string'      -> ValueError: invalid literal for int() with base 10: 'n/a'
RAISE 'daysToExpiration is NaN float'        -> ValueError: cannot convert float NaN to integer
RAISE 'quote node is a string'               -> AttributeError: 'str' object has no attribute 'get'

$ .venv-live/bin/python p4_snapshot_blast.py    # real run_chain_snapshot.main(), stubbed client
main() RAISED ValueError: invalid literal for int() with base 10: 'n/a'
save_chain_snapshot called: 0 time(s)  <-- 0 means the whole day's RTH chain snapshot was lost for every account

(baseline: live suite 376 passed, 1 warning in 9.21s — this is not caught by any test)
```

### `data-quality:2` — HIGH — A held leg whose ticker's chain is present-but-EMPTY is dropped from the merge and appears in NO stat — merged/unquoted/no_chain/drift all silent, take-profit suspended, run prints a success-shaped line
**Where:** `live/held_legs.py`:196

**Claim:** merge_pulled attributes an unspliced held leg to `no_chain` only when `market.chain(ticker, obs) is None`. But market_live.add_chain_rows returns 0 for BOTH `existing is None` AND `len(existing) == 0` — and its own docstring states the empty case is "routine for a thin name" (chain_from_json returns an EMPTY DataFrame, not None, when every contract is filtered by bid<=0/ask<=0/missing delta). When the chain is empty-but-present: add_chain_rows returns 0, `market.chain()` returns a DataFrame so the no_chain door never fires, the leg WAS quoted so it is not in `unquoted`, and _print_held_drift finds it in row_keys so the F7 drift disclosure stays silent. held_marks_failed() is False (answered>0). run_daily:561 then prints `held-leg quotes: 0 spliced into chains (1 distinct legs held)` — which merge_pulled's own docstring says is indistinguishable from a healthy run, the exact reason no_chain was created. Consequence: option_mark returns None for the leg, its EOD take-profit is mechanically suspended for the day, and nothing anywhere says so. Directly reachable on the current live book: RIG (3 accounts) is a ~$3 name whose 12-strike window is exactly the 0.00-bid placeholder population chain_from_json filters out.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; S=/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/data-quality; mkdir -p $S/state; cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import os, pandas as pd
assert "/scratchpad/" in os.environ["WHEELBOT_STATE_DIR"]
from live.market_live import LiveMarket
from live.data import chain_from_json
from live.held_legs import rows_from_quotes, merge_pulled
from live.run_daily import _print_held_drift, held_marks_failed
from src.engine_v2.options.select import option_mark
from src.engine_v2.options.chain import Contract
obs = pd.Timestamp("2026-08-03")
# thin-name chain: every contract is a 0.00x0.00 placeholder -> EMPTY frame
empty = chain_from_json({"underlyingPrice":3.42,"putExpDateMap":{"2026-08-21:18":
    {"3.5":[{"strikePrice":3.5,"daysToExpiration":18,"delta":-0.31,
             "bid":0.0,"ask":0.0,"mark":0.0,"multiplier":100.0}]}}}, obs)
print("chain_from_json ->", type(empty).__name__, "len", len(empty))
closes = pd.Series([3.0]*400, index=pd.bdate_range(end=obs, periods=400), name="close")
m = LiveMarket(["RIG"], {"RIG"}, obs, closes_fn=lambda tk: closes, chain_fn=lambda tk: empty)
c = [{"ticker":"RIG","root":"RIG","expiry":pd.Timestamp("2026-08-21"),"strike":3.5,
      "right":"P","symbol":"RIG   260821P00003500"}]
q = {"RIG   260821P00003500":{"quote":{"bidPrice":0.01,"askPrice":0.03,"mark":0.02},
                              "reference":{"daysToExpiration":18}}}
held = {"rows_by_ticker": rows_from_quotes(q,c,obs), "contracts": c,
        "requested":1, "answered":1, "unquoted":[], "error":None}
stats = merge_pulled(m, held, obs)
pos = [[{"ticker":"RIG","short":{"contract":{"root":"RIG","expiry":"2026-08-21","strike":3.5,"right":"P"}}}]]
print("merge stats      :", stats)
print("held_marks_failed:", held_marks_failed(stats))
print("drift disclosure :", _print_held_drift(pos, held))
print("option_mark      :", option_mark(m.chain("RIG",obs), obs,
      Contract("RIG", pd.Timestamp("2026-08-21"), 3.5, "P")))
PY
```

**Finder evidence:**
```
$ .venv-live/bin/python p8_drift.py
merge stats      : {'requested': 1, 'merged': 0, 'unquoted': [], 'no_chain': [], 'error': None, 'answered': 1}
held_marks_failed: False
drift disclosure : [] (empty = nothing printed)
run_daily would print: 'held-leg quotes: 0 spliced into chains (1 distinct legs held)'
option_mark for the leg -> None   <- None => take-profit mechanically suspended, undisclosed

(earlier probe, same scenario) chain_from_json -> DataFrame len: 0 ; chain present? True len: 0 chains_ok: 1 skipped_chains: []

Held tickers on the real synced book (data/live-synced/accounts/*/state.json):
Counter({'DECK': 10, 'TMO': 6, 'WFC': 3, 'EXPE': 3, 'RIG': 3, 'HAL': 2, 'VALE': 2, 'WBD': 1, 'SCHW': 1})
```

### `data-quality:3` — MED — No ask>=bid sanity anywhere on the mark/close path: all three quote parsers accept a crossed book, and try_take_profit fills the buy-back at ask=0.01 while the same quote bids 5.00
**Where:** `live/held_legs.py`:74

**Claim:** The admission test in live/held_legs.py:rows_from_quotes (`bid is None or ask is None or ask <= 0 or bid < 0`), in live/marks.py:contract_quotes (identical), and in live/data.py:_rows_for (`bid <= 0 or ask <= 0`) contain no ask>=bid check, and none check that Schwab's `mark` lies inside [bid, ask]. The repo already knows this predicate: select.py:liquidity_ok returns (False, "no_two_sided_market") on `ask < bid` — but that gate is explicitly entries-only ("never on closes, expiry, marks, held-only rows, or covered calls"). So a crossed/locked quote (a real artifact of a stale one-sided book) is spliced verbatim into the chain and try_take_profit fires on mark.ask: with credit $0.50 and take_profit_pct=0.60 (the FROZEN production value) a 5.00 x 0.01 book books CLOSE_PUT at $0.01, a fill that could not exist, closing a live position in the ledger at a fabricated gain. Separately, both parsers pass an out-of-band exchange `mark` straight through as `mid` (proved: bid 1.0 / ask 1.1 / mark 99.0 -> mid 99.0), which is the `last_mid` fallback for reported equity when `last_ask` is absent.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; S=/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/data-quality; mkdir -p $S/state; cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import pandas as pd
from live.held_legs import rows_from_quotes
from live.data import chain_from_json
from src.engine_v2.options.fills import try_take_profit
from src.engine_v2.options.chain import Mark
from src.engine_v2.options.wheel import WheelConfig
obs = pd.Timestamp("2026-08-03")
c = [{"ticker":"RIG","root":"RIG","expiry":pd.Timestamp("2026-08-21"),"strike":3.5,
      "right":"P","symbol":"RIG   260821P00003500"}]
q = {"RIG   260821P00003500":{"quote":{"bidPrice":5.00,"askPrice":0.01,"mark":2.50,
     "underlyingPrice":3.4},"reference":{"daysToExpiration":18}}}
r = rows_from_quotes(q,c,obs)["RIG"][0]
print("rows_from_quotes kept:", {k:r[k] for k in ("bid","ask","mid")})
ch = chain_from_json({"underlyingPrice":3.4,"putExpDateMap":{"2026-08-21:18":{"3.5":[
  {"strikePrice":3.5,"daysToExpiration":18,"delta":-0.30,"bid":5.00,"ask":0.01,
   "mark":2.50,"multiplier":100.0}]}}}, obs)
print("chain_from_json kept:", ch[["bid","ask","mid"]].to_dict("records"))
cfg = WheelConfig(ticker="RIG", starting_capital=5000.0, take_profit_pct=0.60, fees_per_contract=0.05)
print(try_take_profit(mark=Mark(r["bid"],r["ask"],r["mid"]), credit=0.50, contracts=1,
                      cfg=cfg, day=obs, expiry=pd.Timestamp("2026-08-21")))
# mark outside the spread, accepted verbatim as mid:
print(chain_from_json({"underlyingPrice":100.0,"putExpDateMap":{"2026-08-21:18":{"100.0":[
  {"strikePrice":100.0,"daysToExpiration":20,"delta":-0.30,"bid":1.0,"ask":1.1,
   "mark":99.0,"multiplier":100.0}]}}}, obs)[["bid","ask","mid"]].to_dict("records"))
PY
```

**Finder evidence:**
```
$ .venv-live/bin/python p7_crossed.py
rows_from_quotes kept the crossed row: {'bid': 5.0, 'ask': 0.01, 'mid': 2.5}
chain_from_json kept it too: [{'bid': 5.0, 'ask': 0.01, 'mid': 2.5}]
try_take_profit on the crossed quote -> FillDecision(filled=True, price=0.01, cost=1.7000000000000002, stamp=Timestamp('2026-08-03 00:00:00'), via='quote', filled_contracts=1)
  ... buys back at ask=0.01 while the same book bids 5.00

$ .venv-live/bin/python p3_chain.py   (mark outside the spread)
mark far outside bid/ask   rows=1 {... 'bid': 1.0, 'ask': 1.1, 'mid': 99.0 ...}
bid > ask (crossed book)   rows=1 {... 'bid': 5.0, 'ask': 1.0, 'mid': 1.0 ...}
```

### `data-quality:4` — MED — snapshot_pulled_at is the one unvalidated field in chain_store; a non-string or offset-naive pulled_at kills the entire 17:00 run for all 25 accounts on every retry tick
**Where:** `live/chain_store.py`:187

**Claim:** chain_store enforces "corrupt -> None, never a crash and never a served row" everywhere (load_chain_snapshot validates every row against _CHAIN_COLS; load_held_rows re-imposes held_only and coerces stats INSIDE the guard, explicitly because "a corrupt held_stats otherwise raised out of the 17:00 run on every 5-min retry all evening"). snapshot_pulled_at does no type check at all — it returns payload.get("pulled_at") raw. Its sole consumer, run_daily.py:522-528, wraps `datetime.fromisoformat(pa)` and the subtraction in `except ValueError` only. A numeric or object pulled_at raises TypeError('fromisoformat: argument must be str'), and an offset-NAIVE ISO string raises TypeError("can't subtract offset-naive and offset-aware datetimes") — neither is caught, so main() dies at a display-only log line before any account steps. Because the RTH snapshot is immutable after 16:05, every 5-minute retry from 17:00-23:30 dies identically and the day is lost for all 25 accounts — precisely the A16-F2 retry-spam/immutable-file class the repair cites when justifying the other guards. Note load_chain_snapshot returns a perfectly valid chains dict for the same file, so the run gets past the snapshot check and then dies.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; S=/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/data-quality; mkdir -p $S/state; cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import os, json, datetime as dt, pandas as pd
from zoneinfo import ZoneInfo
assert "/scratchpad/" in os.environ["WHEELBOT_STATE_DIR"]   # never the frozen archive
from live.chain_store import snapshot_path, snapshot_pulled_at, load_chain_snapshot
ET = ZoneInfo("America/New_York"); obs = pd.Timestamp("2026-08-03")
p = snapshot_path(obs); os.makedirs(os.path.dirname(p), exist_ok=True)
for bad in [1784310199088, {"t":"x"}, "2026-08-03T15:25:00"]:
    json.dump({"obs":"2026-08-03","pulled_at":bad,"chains":{}}, open(p,"w"))
    print("chains load OK:", load_chain_snapshot(obs) is not None,
          "| pulled_at:", repr(snapshot_pulled_at(obs)))
    try:                                   # verbatim run_daily.py:522-528
        pa = snapshot_pulled_at(obs)
        if pa: (dt.datetime.now(ET) - dt.datetime.fromisoformat(pa)).total_seconds()
        print("   survived")
    except ValueError as e: print("   ValueError -> CAUGHT:", e)
    except Exception as e: print(f"   {type(e).__name__} -> UNCAUGHT, run_daily.main() dies: {e}")
PY
```

**Finder evidence:**
```
$ (command above)
chains load OK: True | pulled_at returned: 1784310199088
   TypeError -> UNCAUGHT, run_daily.main() dies: fromisoformat: argument must be str
chains load OK: True | pulled_at returned: {'t': 'x'}
   TypeError -> UNCAUGHT, run_daily.main() dies: fromisoformat: argument must be str
chains load OK: True | pulled_at returned: '2026-08-03T15:25:00'
   TypeError -> UNCAUGHT, run_daily.main() dies: can't subtract offset-naive and offset-aware datetimes
```

### `data-quality:5` — LOW — select_roll_contract is the only selection site missing the held_only filter — the documented cross-account security boundary has one open door (latent)
**Where:** `src/engine_v2/options/select.py`:124

**Claim:** The held_only flag is described as "a security boundary — one account's held leg must never become a candidate entry for the other 24" and is re-imposed at load in chain_store.load_held_rows. Three of the four consumers filter it (select.py:37 liquidity_ok, :75 at_risky_window_edge, :99 select_contract). select_roll_contract (line 118) builds `cand` with no held_only filter and will return a spliced mark-only row as a roll destination — a contract the bot never surveyed, sourced from another account's position. Currently LATENT, not live: portfolio.step_one_day (the engine run_daily uses) hard-refuses rolls (portfolio.py:539-542) and FROZEN never sets roll_tested_puts, while backtest chains carry no held_only column. It is a one-line divergence from a stated invariant that becomes a wrong-trade path the moment the roll mechanic is enabled on the portfolio engine.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; cd $E && PYTHONPATH=. .venv-live/bin/python - <<'PY'
import pandas as pd
from src.engine_v2.options.select import select_contract, select_roll_contract
date = pd.Timestamp("2026-08-03")
cols = ["date","expiry","strike","right","dte","delta","bid","ask","mid","underlying","held_only"]
chain = pd.DataFrame([
  dict(date=date, expiry=pd.Timestamp("2026-08-21"), strike=3.5, right="P", dte=18,
       delta=-0.30, bid=0.20, ask=0.25, mid=0.22, underlying=3.4, held_only=False),
  dict(date=date, expiry=pd.Timestamp("2026-09-18"), strike=3.5, right="P", dte=46,
       delta=-0.42, bid=0.35, ask=0.45, mid=0.40, underlying=3.4, held_only=True),
], columns=cols)
print("select_contract      ->", select_contract(chain, date, "P", 0.30, 46, "RIG"))
print("select_roll_contract ->", select_roll_contract(chain, date, "P", 3.5,
                                    pd.Timestamp("2026-08-21"), 28, "RIG"))
PY
# reachability check:
grep -n "roll_tested_puts" $E/src/engine_v2/options/portfolio.py $E/live/run_daily.py
```

**Finder evidence:**
```
$ .venv-live/bin/python p6_roll_heldonly.py
select_contract  (dte target 46) -> None   <- held_only row correctly refused
select_roll_contract (from Aug-21, target_dte 28) -> Contract(root='RIG', expiry=Timestamp('2026-09-18 00:00:00'), strike=3.5, right='P')   <- held_only row RETURNED as a roll destination

$ grep -n roll src/engine_v2/options/portfolio.py
539:    if cfg.roll_tested_puts or cfg.put_stop_mult is not None or \
542:                         "roll/stop/gates/liquidate are solo mechanics")
(FROZEN in live/run_daily.py:32-57 never sets roll_tested_puts -> default False)
```

### `data-quality:6` — LOW — B1's bad-close disclosure names neither the ticker nor the dropped date, across a 531-name universe
**Where:** `live/data.py`:40

**Claim:** closes_from_json prints `closes: dropped {n} non-positive/non-finite row(s)` with no ticker and no date. daily_closes() is called once per universe name (531 after RESERVED+_RETIRED subtraction), so on a real run this line is an anonymous entry in a 531-ticker log with nothing to correlate it to. The dropped bar leaves a silent hole in the close series: spot(ticker, day, fallback) then falls back for that date. (The money path is safe here — portfolio.py:284 settles from market.spot(exact date) and LiveMarket deliberately has no bounded_settle_price, so a scrubbed expiry-day bar becomes an expiry_unsettleable warning rather than a wrong settlement; LiveMarket.settle_price is dead code, `grep -rn '\.settle_price(' src/ live/ scripts/ | grep -v tests` returns nothing.) The defect is observability: B1 exists to say the drop "out loud" and the message cannot be acted on.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; S=/private/tmp/claude-501/-Users-georgiemanavazian-Documents-Trading-vault/ba35d49d-89bf-4da7-ad52-7783351cfb5f/scratchpad/f4/data-quality; mkdir -p $S/state; cd $E && WHEELBOT_STATE_DIR=$S/state PYTHONPATH=. .venv-live/bin/python - <<'PY'
import pandas as pd
from live.data import closes_from_json
from live.market_live import LiveMarket
ms = lambda d: int(pd.Timestamp(d).value // 10**6)
days = pd.bdate_range("2024-12-02", periods=402)
candles = [{"datetime": ms(d), "close": 50.0 + i*0.01} for i, d in enumerate(days)]
candles[-1]["close"] = 0.0            # vendor serves a 0.00 bar
bad_day = days[-1]
s = closes_from_json({"candles": candles})   # <-- the ONLY disclosure emitted
print("which ticker? which date? -- not in the message above")
print("dropped day in index:", bad_day in s.index)
m = LiveMarket(["ZZZ"], set(), pd.Timestamp(bad_day.date()), closes_fn=lambda tk: s,
               chain_fn=lambda tk: (_ for _ in ()).throw(KeyError("no chain")))
print("spot(ZZZ, %s, fallback=-1) ->" % bad_day.date(), m.spot("ZZZ", bad_day, -1.0))
PY
cd $E && grep -rn "\.settle_price(" src/ live/ scripts/ 2>/dev/null | grep -v tests; echo "(no output above = LiveMarket.settle_price is dead on the live path)"
```

**Finder evidence:**
```
$ .venv-live/bin/python p5_close_scrub.py
expiry-day raw close from vendor : 0.0 2026-06-16
prior-day  raw close from vendor : 54.0 2026-06-15
closes: dropped 1 non-positive/non-finite row(s)      <-- no ticker, no date
expiry day in scrubbed index?    : False
spot(ZZZ, expiry, fallback=-1)    -> -1.0

$ grep -rn "\.settle_price(" src/ live/ scripts/ | grep -v tests
(no output — confirms the money path is not affected)

$ (universe size) UNIVERSE: 531 raw _ALL: 555 dedup: 547
```

### `data-quality:7` — LOW — B10's non-standard/multiplier guard is a no-op when `multiplier` is absent or null, and `nonStandard` is matched with `is True` so a string/int truthy value passes
**Where:** `live/data.py`:104

**Claim:** The B10 filter is `if ct.get("nonStandard") is True or (m_ is not None and m_ != 100)`. A contract dict with no `multiplier` key, or `"multiplier": null`, yields m_=None and the multiplier arm short-circuits to False, so the contract is admitted and every x100 cost computation downstream uses the wrong deliverable. `nonStandard` is compared with identity, so `"true"` or `1` also pass. Real Schwab payloads do carry `"multiplier": 100.0` and a JSON boolean (verified on live/fixtures/option_chain_gdx_all.json), so this is a robustness/hygiene gap rather than an observed live break — but B10 is the ONE place a contract-side corporate action is meant to become visible, and it fails open rather than closed. Related: the OCC-adjusted case with multiplier still 100 (deliverable "90 XYZ + $340 CASH", optionDeliverablesList length > 1) is not detected at all — neither `deliverableNote` nor `optionDeliverablesList` is inspected.

**Repro:**
```
E=/Users/georgiemanavazian/Documents/Trading/code/etf-bot; cd $E && PYTHONPATH=. .venv-live/bin/python - <<'PY'
import pandas as pd
from live.data import chain_from_json
obs = pd.Timestamp("2026-08-03")
base = {"strikePrice":100.0,"daysToExpiration":20,"delta":-0.30,"bid":1.0,
        "ask":1.1,"mark":1.05,"multiplier":100.0,"nonStandard":False,"symbol":"X"}
def run(name, **kw):
    ct = dict(base); ct.update(kw)
    if kw.get("_drop"): ct.pop("multiplier"); ct.pop("_drop")
    df = chain_from_json({"underlyingPrice":100.0,
         "putExpDateMap":{"2026-08-21:18":{"100.0":[ct]}}}, obs)
    print(f"{name:46} rows={len(df)}  (0 = correctly skipped)")
run("baseline standard contract")
run("multiplier: null (adjusted deliverable)", multiplier=None,
    deliverableNote="90 XYZ + $340 CASH")
run("multiplier key absent", _drop=True)
run("nonStandard: 'true' (string)", nonStandard="true")
run("nonStandard: 1 (int)", nonStandard=1)
run("multiplier: 10 (mini) -- control", multiplier=10.0)
PY
# real Schwab payload does carry the fields:
cd $E && .venv-live/bin/python -c "import json;p=json.load(open('live/fixtures/option_chain_gdx_all.json'));m=p['putExpDateMap'];k=list(m)[0];s=list(m[k])[0];c=m[k][s][0];print({x:c[x] for x in ('multiplier','nonStandard','deliverableNote')})"
```

**Finder evidence:**
```
$ .venv-live/bin/python p3_chain.py  (excerpt)
baseline                                     rows=1
multiplier absent (adjusted contract)        rows=1   <- admitted, no 'nonStandard/multiplier skip' printed
multiplier missing key                       rows=1   <- admitted
nonStandard as string 'true'                 rows=1   <- admitted
nonStandard as 1                             rows=1   <- admitted

$ (fixture check)
{'multiplier': 100.0, 'nonStandard': False, 'deliverableNote': '100 GDX'}
```

## domain: infra-ops  (8 findings)

**Finder summary:** Audited the infra/ops surface that will be deployed at Phase F: scripts/wheelbot_tick.sh line by line, deploy/{wheelbot.service,wheelbot.timer,wheelbot-alert.service,mirror-freshness.yml,check_mirror_freshness.py}, live/sync.py (mirror push + PAT handling + .github self-install), live/alerts.py (spool), live/run_notify.py, live/run_health.py, live/state.py backup rotation, and the legacy Mac scripts. I built a standalone bash sandbox for the real tick script (same stubbed-python journal pattern as live/tests/test_tick_script.py) at /private/tmp/.../f4/infra-ops/tickharness.sh and swept every failure branch for its exit code; I raced two overlapping ticks and two concurrent processes through the real already_stepped/append_snapshot guard; I drove the real live/sync.py against a local bare repo whose pre-receive hook mimics GitHub's PAT-workflow-scope refusal; and I drove the real deploy/check_mirror_freshness.py with injected timestamps. Everything ran read-only against the repo (git status is unchanged except a plan doc edited at 11:03, before this session; nothing under data/live was written — verified with find -newermt). Confirmed-good along the way: split .dailyran/.synced markers, health-first ordering, marker-only-on-success for the EOD run and chain snapshot, D5b's missing-token nag, A23 smoke isolation (no globbed account discovery, all alert/gap sites wrappered), the state.json 7-day backup pruning, and the intraday liveness parser against real archived logs. The 33 tick/deploy/sync/mirror tests pass. Seven defects survive; two are HIGH.

### `infra-ops:1` — HIGH — No mutual exclusion between the timer's EOD run and a manual/catch-up run — already_stepped is a TOCTOU check, so a day can be double-stepped
**Where:** `live/run_daily.py`:115

**Claim:** There is no lock anywhere in the live path (grep -rn 'flock' over live/ scripts/ deploy/ returns nothing). scripts/wheelbot_tick.sh gates the EOD run on `[ ! -f "$MARKER" ]` and live/run_daily.py gates each account on already_stepped(), both plain check-then-act. systemd serialises the *timer's* own ticks, but it cannot serialise a human running `PYTHONPATH=. .venv-live/bin/python live/run_daily.py` — which is an established practice here (data/live/logs/manual-2026-07-20.log and catchup-2026-07-24.log exist) and which A11's gate explicitly permits from 17:00 ET onward, i.e. exactly inside the tick's 17:00-23:30 retry window, while the tick's run_daily takes ~7 minutes. Two overlapping runs both pass already_stepped and both step the day: trades.jsonl gets every trade twice, snapshots.jsonl gets two rows for the same date, and state.json is whichever process called os.replace last — so the ledger and the state diverge silently. run_daily.py's own comment at line 717 records that this already happened once ('every account still carries a duplicate 2026-07-24 row from before this guard existed'); the guard added then does not cover the concurrent case.

**Repro:**
```
# 1) prove no lock exists anywhere in the live path
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && grep -rn "flock\|LOCK_EX\|lockf" live scripts deploy ; echo "grep found nothing (rc=$?)"
ls data/live/logs/ | grep -E 'manual|catchup'   # manual runs are an established practice

# 2) race the REAL guard: two processes, scratch store, never data/live
mkdir -p /tmp/f4race && cat > /tmp/f4race/probe.py <<'EOF'
import os, sys, multiprocessing as mp, time
os.environ["WHEELBOT_STATE_DIR"] = sys.argv[1]
sys.path.insert(0, "/Users/georgiemanavazian/Documents/Trading/code/etf-bot")
import pandas as pd
from live.run_daily import already_stepped
from live.snapshots import append_snapshot
SNAP = os.path.join(sys.argv[1], "snapshots.jsonl"); OBS = pd.Timestamp("2026-07-24")
def worker(tag, q, barrier):
    barrier.wait()
    seen = already_stepped(SNAP, OBS)
    time.sleep(0.2)                      # stands in for the ~7-min paper_step
    if not seen: append_snapshot(SNAP, {"date": "2026-07-24", "equity": 100000.0, "by": tag})
    q.put((tag, seen))
if __name__ == "__main__":
    os.makedirs(sys.argv[1], exist_ok=True)
    q = mp.Queue(); b = mp.Barrier(2)
    ps = [mp.Process(target=worker, args=(t, q, b)) for t in ("timer-tick", "manual-run")]
    [p.start() for p in ps]; [p.join() for p in ps]
    print("already_stepped saw:", sorted(q.get() for _ in ps))
    print(open(SNAP).read()); print("ROW COUNT:", sum(1 for l in open(SNAP) if l.strip()))
EOF
rm -rf /tmp/f4race/state
/Users/georgiemanavazian/Documents/Trading/code/etf-bot/.venv-live/bin/python /tmp/f4race/probe.py /tmp/f4race/state

# 3) the shell layer has no lock either: two overlapping ticks both fire run_daily
#    (uses the standalone tick harness in finding infra-ops:5's repro)
```

**Finder evidence:**
```
$ grep -rn "flock\|LOCK_EX\|lockf" live scripts deploy
grep found nothing (rc=0)
$ ls data/live/logs/ | grep -E 'manual|catchup'
catchup-2026-07-24.log
manual-2026-07-20.log

$ .venv-live/bin/python /tmp/f4race/probe.py /tmp/f4race/state
already_stepped saw: [('manual-run', False), ('timer-tick', False)]
{"date": "2026-07-24", "equity": 100000.0, "by": "manual-run"}
{"date": "2026-07-24", "equity": 100000.0, "by": "timer-tick"}
ROW COUNT: 2

# shell layer, two ticks started 0.4s apart against the sandbox harness:
results: [('A', 0), ('B', 0)]
run_daily invocations: 2
sync invocations: 1
  live/run_notify.py retry-spool
  live/run_notify.py token-age .../token.json
  live/run_daily.py
  live/run_notify.py retry-spool
  live/run_daily.py
  -c import sys; from live.sync import sync_state; ...
```

### `infra-ops:2` — HIGH — A PAT without `workflow` scope permanently bricks the whole state mirror on the first Phase-F sync, and sync_state re-creates the offending file so it cannot be opted out of
**Where:** `live/sync.py`:183

**Claim:** live/sync.py writes deploy/mirror-freshness.yml into <state_dir>/.github/workflows/ on EVERY call, unconditionally, BEFORE `git add -A` (lines 183-202). GitHub refuses any push that creates or updates a file under .github/workflows/ when the PAT lacks the `workflow` scope. Because sync_state commits before pushing, the workflow file lands in local history on the first sync and the same commit is re-pushed and re-rejected on every subsequent sync — so a single missing PAT scope kills the entire state mirror permanently, not just the watcher. The dashboard (scripts/dashboard.sh) reads only that mirror, so it freezes too. There is no fallback push that omits .github/, and an operator who deletes the file to get the mirror moving again finds sync_state re-creating and re-staging it on the very next call. D14 declares 'PAT needs `workflow` scope (Phase-F check)' but not that failing the check is unrecoverable-by-retry and takes the whole mirror down with it — and the off-VPS watcher that would report a frozen mirror is precisely the artefact that cannot be pushed.

**Repro:**
```
set -e; mkdir -p /tmp/f4pat && cd /tmp/f4pat && rm -rf remote.git state cfg.json
git init -q --bare remote.git
cat > remote.git/hooks/pre-receive <<'H'
#!/bin/bash
while read old new ref; do
  if git ls-tree -r --name-only "$new" | grep -q '^\.github/workflows/'; then
    echo "remote: ! [remote rejected] main -> main (refusing to allow a Personal Access Token to create or update workflow without \`workflow\` scope)" >&2; exit 1; fi
done; exit 0
H
chmod +x remote.git/hooks/pre-receive
mkdir -p state/accounts && echo '{"cash":100000}' > state/accounts/state.json
printf '{"token":"ghp_notarealtokenAAAAAAAAAAAAAAAAAAAAAAAA","repo":"owner/wheelbot-state"}' > cfg.json
cat > probe.py <<'EOF'
import sys, os, subprocess
sys.path.insert(0,"/Users/georgiemanavazian/Documents/Trading/code/etf-bot")
import live.sync as S
REMOTE = os.path.abspath("remote.git"); orig = S._git
def patched(args, cwd):
    if args and args[0] == "push": args = ["push","--force",REMOTE,"main"]
    return orig(args, cwd)
S._git = patched; S._known_literals = lambda cfg: ()
for i in (1,2,3): print(f"sync #{i} ->", S.sync_state("state", f"eod day{i}", cfg_path="cfg.json"))
# operator tries to opt out:
subprocess.run(["git","-C","state","rm","-r","-q","--cached",".github"],capture_output=True)
subprocess.run(["rm","-rf","state/.github"])
subprocess.run(["git","-C","state","commit","-q","-m","drop workflow"],capture_output=True)
print("sync after removal ->", S.sync_state("state","eod after-removal",cfg_path="cfg.json"))
print("file re-created by sync_state?", os.path.exists("state/.github/workflows/mirror-freshness.yml"))
print("remote commits:", subprocess.run(["git","-C",REMOTE,"log","--oneline"],capture_output=True,text=True).stdout.strip() or "(EMPTY)")
EOF
/Users/georgiemanavazian/Documents/Trading/code/etf-bot/.venv-live/bin/python probe.py
```

**Finder evidence:**
```
[sync push failed] remote: ! [remote rejected] main -> main (refusing to allow a Personal Access Token to create or update workflow `.github/workflows/mirror-freshness.yml` without `workflow` scope)
sync #1 -> False
sync #2 -> False
sync #3 -> False
local commits: 3b14b28 eod day1
remote commits: (EMPTY -- mirror never received anything)

# operator removes the workflow to unblock the mirror:
after operator removal, file present? False
[sync push failed] remote: ! [remote rejected] ... without `workflow` scope
sync after removal -> False
file re-created by sync_state? True
remote commits: (EMPTY)
```

### `infra-ops:3` — MED — The off-VPS freshness watcher files a false STALE issue + email after only ~2h30m of GitHub Actions cron delay, contradicting its own declared tolerance
**Where:** `deploy/check_mirror_freshness.py`:34

**Claim:** deploy/mirror-freshness.yml:15 fires at 01:30 UTC (Tue-Sat) and its header declares 'Actions cron is best-effort (delays up to hours -- fine at daily granularity)'. That is false. check_mirror_freshness.py computes `expected` from the ET date AT RUN TIME (line 34) and compares it to the heartbeat's `today` (line 47). 01:30 UTC = 21:30 EDT / 20:30 EST on the previous ET day, so the ET date rolls over after only 2h30m (EDT) or 3h30m (EST) of delay. Past that boundary a perfectly healthy mirror is reported as 'STALE: the VPS is dead, the tick is broken, or the sync stopped pushing', the step exits nonzero, and the workflow both emails a failure notification and auto-files a GitHub issue. The delay range the file itself calls acceptable is the range that breaks it; the checker should anchor on the most recent completed ET weekday, not on 'now'.

**Repro:**
```
cd /tmp && mkdir -p f4mirror && printf '{"tick_at":"2026-08-03T21:05:00Z","today":"2026-08-03","dailyran":true,"synced":false,"fail":false}\n' > f4mirror/heartbeat.json
PY=/Users/georgiemanavazian/Documents/Trading/code/etf-bot/.venv-live/bin/python
CHK=/Users/georgiemanavazian/Documents/Trading/code/etf-bot/deploy/check_mirror_freshness.py
for t in 2026-08-04T01:30:00Z 2026-08-04T03:55:00Z 2026-08-04T04:00:00Z 2026-08-04T05:10:00Z; do
  printf '%s -> ' $t; $PY $CHK --root f4mirror --now-utc $t; echo "   exit=$?"; done
```

**Finder evidence:**
```
2026-08-04T01:30:00Z -> fresh: 2026-08-03, EOD completed
   exit=0
2026-08-04T03:55:00Z -> fresh: 2026-08-03, EOD completed
   exit=0
2026-08-04T04:00:00Z -> STALE: mirror heartbeat is from 2026-08-03, expected 2026-08-04 -- the VPS is dead, the tick is broken, or the sync stopped pushing
   exit=1        <-- only 2h30m of Actions delay (EDT)
2026-08-04T05:10:00Z -> STALE: mirror heartbeat is from 2026-08-03, expected 2026-08-04 ...
   exit=1
```

### `infra-ops:4` — MED — The freshness watcher runs at 20:30/21:30 ET but the tick's EOD retry window runs to 23:30 ET, so any late-succeeding day is falsely reported INCOMPLETE
**Where:** `deploy/mirror-freshness.yml`:15

**Claim:** The cron fires 01:30 UTC = 20:30 EST / 21:30 EDT, i.e. up to three hours BEFORE the tick's EOD retry window closes (scripts/wheelbot_tick.sh:125 allows the daily run at every tick through HM=2330; live/health.py:24 pins EOD_WINDOW_CLOSE='23:30' and only run_health at 23:45 ET is entitled to call a day missed). A day whose run_daily fails at 17:00 and succeeds at, say, 22:00 ET is a completely normal, fully-recovered day — but the watcher has already sampled dailyran=false and filed an issue plus an email saying 'the EOD run never completed ... the day may be recorded as a gap'. The two deadlines are not cross-pinned to each other the way D12 cross-pinned EOD_WINDOW_CLOSE to the tick's 2330; the watcher should not judge before 23:45 ET (03:45/04:45 UTC).

**Repro:**
```
cd /tmp && mkdir -p f4mirror
# heartbeat sampled at 21:30 ET on a day whose EOD run has not succeeded YET
# (the tick will keep retrying every 5 min until 23:30 ET)
printf '{"tick_at":"2026-08-04T01:30:00Z","today":"2026-08-03","dailyran":false,"synced":false,"fail":true}\n' > f4mirror/heartbeat.json
/Users/georgiemanavazian/Documents/Trading/code/etf-bot/.venv-live/bin/python \
  /Users/georgiemanavazian/Documents/Trading/code/etf-bot/deploy/check_mirror_freshness.py \
  --root f4mirror --now-utc 2026-08-04T01:30:00Z; echo "exit=$?"
# and the two deadlines that disagree:
grep -n 'cron:' /Users/georgiemanavazian/Documents/Trading/code/etf-bot/deploy/mirror-freshness.yml
grep -n '2330\|2345' /Users/georgiemanavazian/Documents/Trading/code/etf-bot/scripts/wheelbot_tick.sh
grep -n 'EOD_WINDOW_CLOSE' /Users/georgiemanavazian/Documents/Trading/code/etf-bot/live/health.py
```

**Finder evidence:**
```
INCOMPLETE: heartbeat is from today but the EOD run never completed (dailyran=False) -- check the VPS logs; the day may be recorded as a gap
exit=1

deploy/mirror-freshness.yml:15:    - cron: "30 1 * * 2-6"          # 20:30 EST / 21:30 EDT
scripts/wheelbot_tick.sh:48:if [ "$DOW" -le 5 ] && [ "$HM" -ge 2345 ]; then
scripts/wheelbot_tick.sh:125:if [ "$DOW" -le 5 ] && [ "$HM" -ge 1700 ] && [ "$HM" -le 2330 ] && [ ! -f "$MARKER" ]; then
live/health.py:24:EOD_WINDOW_CLOSE = "23:30"
```

### `infra-ops:5` — MED — A dead mail channel is undetectable: the tick discards retry-spool's exit code with `|| true` and nothing in the repo ever reads alerts-failed.jsonl
**Where:** `scripts/wheelbot_tick.sh`:53

**Claim:** live/run_notify.py:174 returns 1 whenever the spool still has undelivered alerts, but scripts/wheelbot_tick.sh:53 pipes that call through `|| true`, so an unbounded and permanently-growing alert backlog never sets FAIL, never trips systemd's OnFailure=wheelbot-alert.service, and never appears in the heartbeat's `fail` field. live/alerts.py:66 claims the backlog is 'VISIBLE instead of evaporating', but a whole-repo grep shows the only references to alerts-failed.jsonl outside live/alerts.py itself are a comment in live/sync.py and the plan doc — the dashboard, live/health.py, live/run_health.py and deploy/check_mirror_freshness.py all ignore it. So if the Gmail app password is revoked (the exact incident D3 was written for), every alert silently spools forever while the tick keeps exiting 0. The catastrophic cases (dead VPS, lapsed token) still surface because dailyran goes false and the mirror-freshness watcher catches it; everything the freshness watcher does NOT model — intraday-manager-died, corporate-action freeze, unsettleable expiry, already-recorded gaps, token nag — goes silent with no automated detector at all.

**Repro:**
```
# 1) nothing reads the spool
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
grep -rn "alerts-failed" --exclude-dir=.venv --exclude-dir=.venv-live --exclude-dir=.git --exclude-dir=data . | grep -v "/tests/"
grep -n 'retry-spool' scripts/wheelbot_tick.sh

# 2) tick swallows a failing spool retry -- standalone sandbox for the real script
cat > /tmp/tickharness.sh <<'OUTER'
#!/bin/bash
R="$(mkdir -p "$1" && cd "$1" && pwd)"; shift; DOW="$1"; HM="$2"; shift 2
mkdir -p "$R/data/live/logs" "$R/.venv-live/bin"
cat > "$R/.venv-live/bin/python" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TICK_JOURNAL:?}"
key="${1:-inline}"; [ "$key" = "-c" ] && key=inline_sync
key=$(basename "$key" .py); key=${key//[^a-zA-Z0-9_]/_}
s="TICK_SLEEP_${key}"; [ -n "${!s:-}" ] && sleep "${!s}"
o="TICK_OUT_${key}"; r="TICK_RC_${key}"
[ -n "${!o:-}" ] && printf '%s\n' "${!o}"
exit "${!r:-0}"
SHIM
chmod +x "$R/.venv-live/bin/python"; : > "$R/journal.txt"
env "$@" WHEELBOT_REPO="$R" WHEELBOT_FAKE_DOW="$DOW" WHEELBOT_FAKE_HM="$HM" \
  WHEELBOT_FAKE_TODAY=2026-07-24 WHEELBOT_TOKEN_PATH="$R/token.json" TICK_JOURNAL="$R/journal.txt" \
  bash /Users/georgiemanavazian/Documents/Trading/code/etf-bot/scripts/wheelbot_tick.sh
echo "TICK EXIT=$?"; echo "--- python calls:"; cat "$R/journal.txt"
echo "--- heartbeat:"; cat "$R/data/live/heartbeat.json" 2>/dev/null
OUTER
chmod +x /tmp/tickharness.sh
rm -rf /tmp/f4spool && /tmp/tickharness.sh /tmp/f4spool 5 1000 TICK_RC_run_notify=1
```

**Finder evidence:**
```
$ grep -rn "alerts-failed" ... | grep -v /tests/
live/alerts.py:58:    return in_state("alerts-failed.jsonl")
live/alerts.py:66:    D3: a failed send is appended to the spool (data/live/alerts-failed.jsonl,
live/sync.py:151:        # D13: self-healing .gitignore (ONLY *.tmp -- alerts-failed.jsonl and
docs/superpowers/plans/2026-07-31-repair-plan.md:...
(no dashboard/, live/health.py, live/run_health.py or deploy/ reader)

$ grep -n 'retry-spool' scripts/wheelbot_tick.sh
53:py live/run_notify.py retry-spool >> "$LOGDIR/tick.log" 2>&1 || true

$ /tmp/tickharness.sh /tmp/f4spool 5 1000 TICK_RC_run_notify=1
TICK EXIT=0
--- python calls:
live/run_notify.py retry-spool        <-- returned 1, swallowed
live/run_notify.py token-age .../token.json
live/run_intraday.py
--- heartbeat:
{"tick_at": ..., "dailyran": false, "synced": false, "fail": false}
```

### `infra-ops:6` — MED — On a weekend a total tick failure (dead interpreter/venv) exits 0 and writes a heartbeat claiming fail:false — the every-day token nag is silently dead all weekend
**Where:** `scripts/wheelbot_tick.sh`:63

**Claim:** On a weekend every block in the tick is either skipped (DOW>5) or has its failure deliberately discarded: retry-spool via `|| true` (line 53) and token-age via the `if py ...; then` form (line 63-68), which is indistinguishable from run_notify's 'nothing to say' return of 1. I destroyed the interpreter and ran the real script: on Saturday the tick performs ZERO python invocations, exits 0, and writes a heartbeat asserting "fail": false — so systemd never enters failed state, OnFailure=wheelbot-alert.service never fires, and because no sync runs on a weekend the mirror does not change either. The same failure on a weekday correctly exits 1. D5's entire premise is that the Schwab token nag must be eligible on weekends because 'two of fourteen login times had ZERO warning before the 7-day lapse'; a broken venv, a corrupted site-packages, or a full disk from Friday 23:xx is therefore invisible until the mirror-freshness watcher runs on Monday ~21:30 ET — the exact window in which a refresh token lapses and blinds Monday's session.

**Repro:**
```
cat > /tmp/tickharness.sh <<'OUTER'
#!/bin/bash
R="$(mkdir -p "$1" && cd "$1" && pwd)"; shift; DOW="$1"; HM="$2"; shift 2
mkdir -p "$R/data/live/logs" "$R/.venv-live/bin"
cat > "$R/.venv-live/bin/python" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TICK_JOURNAL:?}"; key="${1:-inline}"; [ "$key" = "-c" ] && key=inline_sync
key=$(basename "$key" .py); key=${key//[^a-zA-Z0-9_]/_}
o="TICK_OUT_${key}"; r="TICK_RC_${key}"; [ -n "${!o:-}" ] && printf '%s\n' "${!o}"; exit "${!r:-0}"
SHIM
chmod +x "$R/.venv-live/bin/python"; : > "$R/journal.txt"
OUTER
chmod +x /tmp/tickharness.sh
TICK=/Users/georgiemanavazian/Documents/Trading/code/etf-bot/scripts/wheelbot_tick.sh
for DOW in 6 3; do
  R=/tmp/f4vg$DOW; rm -rf $R; /tmp/tickharness.sh $R $DOW 1200
  rm -f $R/.venv-live/bin/python                      # interpreter / venv gone
  env WHEELBOT_REPO=$R WHEELBOT_FAKE_DOW=$DOW WHEELBOT_FAKE_HM=1200 \
      WHEELBOT_FAKE_TODAY=2026-07-24 WHEELBOT_TOKEN_PATH=$R/token.json \
      TICK_JOURNAL=$R/journal.txt bash $TICK
  echo "DOW=$DOW TICK EXIT=$?  python invocations=$(wc -l < $R/journal.txt)"
  cat $R/data/live/heartbeat.json
done
```

**Finder evidence:**
```
DOW=6 TICK EXIT=0  python invocations=0
{"tick_at": "2026-08-03T15:26:45Z", "today": "2026-07-24", "dailyran": false, "synced": false, "fail": false}
DOW=3 TICK EXIT=1  python invocations=0
{"tick_at": "2026-08-03T15:26:45Z", "today": "2026-07-24", "dailyran": false, "synced": false, "fail": true}

(Saturday: zero work done, exit 0, heartbeat asserts fail:false -> no OnFailure, no alert,
 no sync, nothing off-VPS changes until Monday evening.)
```

### `infra-ops:7` — LOW — The tick hardcodes data/live for its log dir, markers, heartbeat and sync target while every Python module honours WHEELBOT_STATE_DIR — a split-brain the E6 test explicitly pins against for Python only
**Where:** `scripts/wheelbot_tick.sh`:31

**Claim:** live/paths.py documents WHEELBOT_STATE_DIR as the single source of truth for the live store, and E6 added a fresh-interpreter test pinning that every module follows the override. scripts/wheelbot_tick.sh does not: LOGDIR (line 31), the .dailyran/.synced/.chainsnap/.tokennag markers (41-42, 60, 78, 111, 157), the heartbeat (line 140) and the sync target string `sync_state('data/live', ...)` (lines 93, 152) are all hardcoded to $REPO/data/live. Set WHEELBOT_STATE_DIR in the unit's environment and the split is total: run_daily/run_health/gaps write and read one store while the tick writes its completion markers and heartbeat into another, so live/health.py's LOGS_DIR sees no .dailyran-* marker at all and would gap every single day, and the sync would push the wrong tree. Nothing sets the variable on the VPS today, so this is latent — but it is a real divergence from a declared invariant that has its own regression test on the Python side only.

**Repro:**
```
cat > /tmp/tickharness.sh <<'OUTER'
#!/bin/bash
R="$(mkdir -p "$1" && cd "$1" && pwd)"; shift; DOW="$1"; HM="$2"; shift 2
mkdir -p "$R/data/live/logs" "$R/.venv-live/bin"
cat > "$R/.venv-live/bin/python" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TICK_JOURNAL:?}"; key="${1:-inline}"; [ "$key" = "-c" ] && key=inline_sync
key=$(basename "$key" .py); key=${key//[^a-zA-Z0-9_]/_}
o="TICK_OUT_${key}"; r="TICK_RC_${key}"; [ -n "${!o:-}" ] && printf '%s\n' "${!o}"; exit "${!r:-0}"
SHIM
chmod +x "$R/.venv-live/bin/python"; : > "$R/journal.txt"
OUTER
chmod +x /tmp/tickharness.sh
R=/tmp/f4sd; rm -rf $R; /tmp/tickharness.sh $R 5 1700; mkdir -p $R/ALT
env WHEELBOT_STATE_DIR=$R/ALT WHEELBOT_REPO=$R WHEELBOT_FAKE_DOW=5 WHEELBOT_FAKE_HM=1700 \
    WHEELBOT_FAKE_TODAY=2026-07-24 WHEELBOT_TOKEN_PATH=$R/token.json TICK_JOURNAL=$R/journal.txt \
    bash /Users/georgiemanavazian/Documents/Trading/code/etf-bot/scripts/wheelbot_tick.sh
echo "marker in data/live/logs:  $([ -f $R/data/live/logs/.dailyran-2026-07-24 ] && echo YES || echo NO)"
echo "marker in \$WHEELBOT_STATE_DIR/logs: $([ -f $R/ALT/logs/.dailyran-2026-07-24 ] && echo YES || echo NO)"
echo "heartbeat in data/live: $([ -f $R/data/live/heartbeat.json ] && echo YES || echo NO) | in ALT: $([ -f $R/ALT/heartbeat.json ] && echo YES || echo NO)"
grep -n "sync_state('data/live'" /Users/georgiemanavazian/Documents/Trading/code/etf-bot/scripts/wheelbot_tick.sh
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot && WHEELBOT_STATE_DIR=$R/ALT PYTHONPATH=. .venv-live/bin/python -c "from live.health import LOGS_DIR; from live.gaps import GAPS_PATH; print('health LOGS_DIR =',LOGS_DIR); print('GAPS_PATH   =',GAPS_PATH)"
```

**Finder evidence:**
```
marker in data/live/logs:  YES
marker in $WHEELBOT_STATE_DIR/logs: NO
heartbeat in data/live: YES | in ALT: NO
sync call recorded: -c import sys; from live.sync import sync_state; sys.exit(0 if sync_state('data/live', 'eod 2026-07-24') else 1)

health LOGS_DIR = /tmp/f4sd/ALT/logs
GAPS_PATH   = /tmp/f4sd/ALT/gaps.jsonl
(the tick's markers land where health.py will never look)
```

### `infra-ops:8` — LOW — The tick's chain-snapshot window (<=15:55 ET) is wider than the runner's own window (<=15:50 ET), so the last tick in the window is a guaranteed no-op
**Where:** `scripts/wheelbot_tick.sh`:112

**Claim:** scripts/wheelbot_tick.sh:112 gates the RTH chain snapshot on `HM -ge 1520 && HM -le 1555` and its comment says '15:20-15:55'. live/run_chain_snapshot.py:39 sets SNAP_CLOSE=1550 and snapshot_window_open() refuses anything past it (returning 1, correctly leaving the marker unwritten). The 15:55 tick therefore always burns a python start to print 'chain snapshot REFUSED', and the real last retry opportunity is 15:50 — which matters because a slow failing pull started at 15:45 that runs past 15:50 pushes the queued next tick out of the runner's window entirely and forfeits the day's snapshot (run_daily then gaps the day). The two constants are not cross-pinned, unlike EOD_WINDOW_CLOSE which D12 explicitly pinned to the tick's 2330 and which test_deploy_units-style lint covers.

**Repro:**
```
cd /Users/georgiemanavazian/Documents/Trading/code/etf-bot
grep -n '1520\|1555' scripts/wheelbot_tick.sh
grep -n 'SNAP_OPEN, SNAP_CLOSE' live/run_chain_snapshot.py
sed -n '87,94p' live/run_chain_snapshot.py
# and prove nothing pins them together (contrast with EOD_WINDOW_CLOSE):
grep -rn '1550\|1555' live/tests/ ; echo "no cross-pin test (rc=$?)"
grep -rn 'EOD_WINDOW_CLOSE' live/health.py live/tests/ | head -3
```

**Finder evidence:**
```
scripts/wheelbot_tick.sh:106:# --- RTH chain snapshot: weekdays 15:20-15:55 ET, once per day (A16) --------
scripts/wheelbot_tick.sh:112:if [ "$DOW" -le 5 ] && [ "$HM" -ge 1520 ] && [ "$HM" -le 1555 ] && [ ! -f "$SNAPMARKER" ]; then
live/run_chain_snapshot.py:39:SNAP_OPEN, SNAP_CLOSE = 1520, 1550

live/run_chain_snapshot.py:87: def snapshot_window_open(now_et) -> bool:
    """Weekday 15:20-15:50 ET. ..."""
    hm = now_et.hour * 100 + now_et.minute
    return SNAP_OPEN <= hm <= SNAP_CLOSE

(no live/tests file references 1550 or 1555 -- the pair is unpinned)
```
