# Live paper bot → always-on VPS migration — design

**Date:** 2026-07-27
**Project:** Live Paper Bot — infrastructure. Strategy logic is FROZEN and untouched by this work.
**Status:** design, pending implementation plan.
**Depends on / consumes:** `live/run_daily.py`, `live/run_intraday.py`, `scripts/wheelbot_loop.sh`, `scripts/schwab/schwab_client.py`, `data/live/` state store.

## Problem

The bot runs on the owner's MacBook via `scripts/wheelbot_loop.sh` — a `while true; sleep 300` loop wrapped in `caffeinate -is`. This makes correct operation depend on the laptop being awake, plugged in, and lid-open during market hours. The owner has stated this is not sustainable long-term.

Three distinct failures are already on disk, all silent:

1. **Dead days.** `data/live/logs/` has `.dailyran-` markers for 2026-07-20, 07-22, 07-24 only. **07-21 and 07-23 were weekdays with no run at all.** Entries, assignments and expiries for those days never happened and cannot be recovered.
2. **Zombie run.** `2026-07-24.log` opens with `skipped 547 tickers (pull failures)` — the entire universe failed — then books 0 trades across 25 accounts, exits 0, and writes the `.dailyran-2026-07-24` marker. A totally failed day was permanently recorded as complete.
3. **No restart.** The loop dies on reboot or logout with nothing to relaunch it. Its own header says so.

Separately, the launchd agents at `~/Library/LaunchAgents/com.wheelbot.{daily,intraday}.plist` are **not loaded** (`launchctl list | grep wheelbot` → empty) — macOS TCC blocks launchd from `~/Documents`. They are dead weight and a trap for a future reader.

Intraday coverage is the sharpest dependency: `live/marks.py` uses Schwab's `get_quotes`, a **live snapshot** endpoint. Nothing is persisted. A take-profit crossing that occurs while the laptop sleeps is not merely missed — it never existed in any retrievable form.

## Non-goals

- **Any change to strategy logic.** `FROZEN` params, the chop gate, `step_one_day`, TP rules — all untouched. This migration must be P&L-neutral by construction.
- **Order placement.** The bot stays data-only/paper. `schwab_client.py`'s no-orders-by-design boundary holds.
- **Moving the dashboard, the backtest engine, or the ThetaData pull.** Those stay on the Mac.
- **Removing the weekly Schwab login.** Schwab's refresh token lapses every 7 days and re-auth is interactive by design. No architecture removes it.
- **Backfilling missed days.** See "The unrecoverable half" — this is impossible, not deferred.
- **Tuning the intraday interval.** 5 minutes carries over unchanged.

## Architecture

### Host

Free-tier Linux VPS, Ubuntu 24.04 LTS.

- **Primary: Oracle Cloud Always Free.** ARM Ampere A1 (4 OCPU / 24GB) preferred; AMD micro (1GB) acceptable. Free with no expiry.
- **Fallback: Google Cloud `e2-micro` always-free** in `us-west1`/`us-central1`/`us-east1`, 1GB RAM, 30GB disk.
- **Decision rule:** if Oracle returns "out of host capacity" on two attempts across two regions, switch to GCP and stop optimizing.

Sizing is not a constraint. The daily run pulls `daily_closes` for all 547 universe names (~5MB of float series) and option chains only for held ∪ good-to-rent. 1GB RAM is sufficient with headroom.

VPS clock stays UTC. All market-hours logic already resolves ET explicitly (`ZoneInfo("America/New_York")` in `run_intraday.py`, `TZ=America/New_York` in the loop script). No timezone changes.

### What moves

```
live/                 runner + adapter
src/engine_v2/        wheel engine
scripts/schwab/       auth client
data/live/            state, trades, snapshots (~2.5MB)
~/.schwab/            config.json + token.json   (chmod 600, never committed)
~/.wheelbot/          alerts.json                (chmod 600, never committed)
```

The 3.4G of options parquet, `.venv` (py3.9 backtest), `dashboard/` and the ThetaData tooling do **not** move.

### Scheduling — systemd replaces the loop

`scripts/wheelbot_loop.sh` is retired. It exists because macOS TCC blocked launchd from `~/Documents`; that constraint does not exist on Linux, and the loop's failure mode (dies on reboot, never returns) is one of the three problems this spec fixes.

Three timers:

| Unit | Schedule | Purpose |
|---|---|---|
| `wheelbot-intraday.timer` | every 5 min | Runs `run_intraday.py`. `market_is_open()` stays the sole authority on whether work happens. |
| `wheelbot-daily.timer` | weekdays, every 5 min from 17:00–20:00 ET, `Persistent=true` | Runs `run_daily.py`, marker-gated. Fires repeatedly **by design** — this is the retry window (see below). Persistent so a reboot-straddled trigger still fires on boot. |
| `wheelbot-health.timer` | weekdays 20:15 ET | Dead-man's switch, after the retry window closes. See "Alerting". |

**Why the daily timer repeats.** The current `daily_if_due` in `wheelbot_loop.sh` runs on every 5-minute pass and is gated by the `.dailyran-<date>` marker plus an `hm >= 1700` check. That structure gives failed runs free retries within the same evening. A once-daily systemd trigger would silently remove that property and leave the zombie gate below with nothing to retry it. The repeating window preserves current behavior exactly; the marker remains the thing that stops a second successful run.

systemd restores all three on boot with no user action. The `.dailyran-<date>` marker is retained unchanged as the double-run guard — it is already correct.

### Zombie-run detection (new)

`run_daily.py` already computes `market.skipped` and prints it. It does not act on it.

Add a threshold gate in `run_daily.py`:

- If `len(market.skipped) / len(universe) >= 0.5`, the run is **FAILED**.
- On FAILED: do **not** write the `.dailyran` marker, emit an alert, exit nonzero, and touch no account state.
- The daily timer's next 5-minute trigger retries, up to the 20:00 ET window close. If the window closes with the day still unmarked, the 20:15 health check records a gap.

**Behavior change, stated explicitly:** today, a day where every pull fails is marked complete and is unrecoverable. After this change, such a day retries until it succeeds or the day ends.

Threshold is 0.5, configurable in `data/live/config.json`. Rationale: a handful of delisted or halted names failing is normal; half the universe failing is an outage or a lapsed token.

### The unrecoverable half

Whether a missed day can be reconstructed splits cleanly:

- **Underlying closes — recoverable.** `get_price_history_every_day` returns full history. Regime series can be rebuilt for any past date.
- **Option chains — NOT recoverable.** `chain_frame` calls `get_option_chain` with `from_date=today`. Schwab exposes no historical-chain endpoint. What a chain looked like on a past date is not retrievable.

Therefore **entries, assignments and expiry decisions on a missed day are permanently lost.** No backfill is designed, and none should be added later. Synthesizing a chain from any other source and stepping the engine on it would inject exactly the look-ahead bias this project treats as a bug class.

The honest handling is disclosure:

**`data/live/gaps.jsonl`** — append-only ledger. One record per weekday with no successful daily run:

```json
{"date": "2026-07-21", "reason": "no_run", "detected": "2026-07-21T20:15:00-04:00"}
{"date": "2026-07-24", "reason": "pull_failure", "skipped": 547, "universe": 547}
```

Written by the health check (`no_run`) and by the zombie gate (`pull_failure`). Backfilled once at implementation time with the three known historical gaps (07-21, 07-23 `no_run`; 07-24 `pull_failure`), flagged as reconstructed-from-logs.

Any forward-test result citing this store must report gap days alongside it, same as the contamination disclosures in `08 Wheel Bot/_STATUS.md`.

### State sync back to the Mac

The VPS pushes `data/live/` to a **private** GitHub repo; the Mac pulls. Streamlit continues to read the local path with no change.

- Auth: deploy key on the VPS, write access, scoped to that repo alone.
- Committed: `accounts/*/state.json`, `trades.jsonl`, snapshots, `gaps.jsonl`, run logs.
- **Never committed:** `token.json`, `config.json`, `~/.wheelbot/alerts.json`. Enforced by `.gitignore` plus a pre-push check that aborts if any path under `~/.schwab` or `~/.wheelbot` is staged.
- Push triggers: after every daily run (success or failure — the logs are the diagnostic), and after any intraday run that booked ≥1 trade. **Not** on intraday no-ops, which would produce ~78 empty commits per day.

Sync direction is VPS → GitHub → Mac only. The Mac never writes state. This makes the VPS the unambiguous source of truth and removes any merge scenario.

### Alerting

`smtplib` over SMTP. Credentials in `~/.wheelbot/alerts.json` (chmod 600, outside the repo), matching the existing `~/.schwab/config.json` pattern. A Gmail app password is the expected mechanism.

New module `live/alerts.py`, one function: `send_alert(subject, body)`. Failure to send is logged and swallowed — a broken mailer must never abort a trading run.

Four triggers:

| # | Trigger | Fires from |
|---|---|---|
| 1 | Daily run exits nonzero | `run_daily.py` |
| 2 | Zombie run (skipped ≥ threshold) | `run_daily.py` zombie gate |
| 3 | Schwab token age ≥ 6 days | daily run preamble (ports `wheelbot_daily.sh:18-22`, `osascript` → email) |
| 4 | **No `.dailyran` marker by 20:15 ET on a weekday** (after the retry window closes) | `wheelbot-health.timer` |

Trigger 4 is the only one that fires when the bot itself is dead — 1 through 3 all require a living process. It is the trigger that would have caught 07-21 and 07-23.

Health check on a `no_run` day: append to `gaps.jsonl`, email, exit.

### Security

- SSH key-only, password auth disabled, root login disabled, UFW default-deny inbound except SSH.
- `~/.schwab/token.json` is a live Schwab credential on a rented machine. Mitigated but not eliminated by the data-only client boundary — `schwab_client.py` has no order-placement path, so a stolen token exposes market data and account reads, not trade execution. This is a real residual risk and is accepted knowingly.
- No inbound service. The bot makes outbound HTTPS calls only. Nothing listens except sshd.

## Error handling

Existing per-account and per-ticker isolation in `run_daily.py` and `run_intraday.py` is already correct (one account's failure does not abort the other 24; one ticker's chain failure skips that ticker) and is preserved.

New failure paths:

- **Alert send fails** → log, continue. Never aborts a run.
- **Git push fails** → log, alert, continue. State on the VPS is authoritative; the Mac catches up on the next successful push.
- **Zombie gate trips** → no marker, no state mutation, alert, nonzero exit, retry on next trigger.
- **Health check finds no marker** → gap ledger entry + alert. Takes no corrective action, because none is possible.

## Testing

New logic gets tests before implementation, per project convention:

- Zombie threshold: at/below/above boundary; verify no marker written and no state mutated when tripped.
- Gap ledger: append correctness, idempotency (health check running twice must not double-record a date).
- Alert triggers: each of the four fires on its condition and not otherwise; send failure does not propagate.
- Pre-push secret guard: staging a path under `~/.schwab` aborts the push.
- **Migration equivalence:** the same `data/live/` state stepped on Mac and VPS must produce identical output. Verified at cutover via `run_daily.py --smoke` plus one real run compared against the Mac's last known state.

## Cutover

1. Provision VPS, harden SSH, install py3.12 + deps.
2. Stop the Mac loop (`wheelbot_loop.sh`); **unload and delete the dead launchd plists.**
3. Copy `data/live/`, `~/.schwab/`, `~/.wheelbot/` to the VPS.
4. `run_daily.py --smoke` on the VPS — proves Schwab connectivity end to end.
5. One manual real `run_daily.py`, output reviewed by the owner against the Mac's last state.
6. Enable the three timers.
7. Mac retains the dashboard, pulling state from the private repo.

Rollback: re-run `wheelbot_loop.sh` on the Mac against the synced state. The state format is unchanged, so rollback is always available.

## Owner actions (blocking)

These cannot be automated and gate the build:

1. Create the Oracle Cloud (or GCP) account and provision the instance. Card required for identity verification.
2. Create the private GitHub repo for state sync and add the VPS deploy key.
3. Generate a Gmail app password for alerts.
4. Re-run the Schwab login on the VPS once at cutover, and weekly thereafter.

## Open questions

None blocking. Threshold value (0.5) and alert email address are set at implementation time from `data/live/config.json`.
