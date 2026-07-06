---
name: box-rclone-binder
description: Bind one Box drive across multiple servers via rclone with non-expiring server auth; idempotent deploy, health-check self-heal, cron, and Discord alerts.
---

# box-rclone-binder

> Governing principle (full text in the repo's `PHILOSOPHY.md`): **the multi-host failure is the
> auth model, not the deploy script.** Box's OAuth refresh_token is single-use and rotating, so
> sharing one across N machines guarantees `invalid_grant`. The fix is to remove the shared
> rotating secret entirely (server auth), then make deploy/health idempotent on top of that.

## When to use / when to stop

- **Use** when you must bind ONE Box account to TWO OR MORE servers via rclone and keep it alive
  unattended (auto-refresh, expiry self-heal, multi-host consistency).
- **Single server only?** Plain `rclone config` is enough — you do not need this.
- **Not Box, or not rclone?** Out of scope.
- **Generic cron/systemd templating?** Route elsewhere; this owns the Box+rclone seam specifically.

## The one decision that defines the tool: auth mode

Pick with `box-binder doctor`, then set `auth_mode` in `machines.yaml`:

| mode | refresh_token? | when | how it stays alive |
|---|---|---|---|
| **jwt** (default) | none | enterprise Box + Admin authorize | rclone `tokenRenewer` re-mints locally on each host |
| **ccg-native** | none | enterprise + rclone proves CCG works | rclone renews natively |
| **ccg-mint** | none | enterprise, CCG-native unproven | external mint timer (<60 min) re-mints access token |
| **oauth-broker** | one master only | personal Box, no Admin | single flock'd master refreshes, distributes access-only blobs |

Server-auth modes give each host its OWN long-term credential and locally minted short-lived
access tokens -> naturally multi-host consistent, no rotation to fight. Read
`reference/auth-modes.md` before choosing.

## Workflow (thin)

1. `box-binder doctor -c machines.yaml` — probe rclone version / ssh / systemd.
2. `box-binder verify-config` — schema + secret-pointer-only + NO inline secrets (hard-fails otherwise).
3. `box-binder deploy [--dry-run]` — idempotently converge each host (atomic writes, systemd timer).
4. `box-binder healthcheck` — read-only `rclone lsd` probe + cross-host consistency; classify + self-heal.
5. `box-binder refresh` — jwt/ccg-native = validation no-op; ccg-mint = re-mint; oauth-broker = master refresh.

All commands take `--json` (machine verdict) and `--dry-run`. Load the matching `reference/<shard>.md`
for the step you are on — never all at once.

| step | shard |
|---|---|
| choose auth | `reference/auth-modes.md` |
| deploy / idempotency / atomic writes | `reference/deploy.md` |
| refresh + healthcheck + alerts | `reference/refresh-healthcheck.md` |
| what NOT to do | `reference/anti-patterns.md` |
| when it breaks | `reference/runbook.md` |

## Hard rules (never violate)

1. **No rotating refresh_token on >1 host.** `box-binder` hard-rejects it (anti-pattern guard).
2. **Secrets are referenced, never stored.** `machines.yaml` holds pointers (`*_ref`); values come
   from a secret backend at runtime. Inline secret values fail `verify-config`. `*.env`/`config.json`/
   `*.pem`/`*.conf` are gitignored. Never `-vv` / `rclone config dump` to a log (leaks tokens).
3. **Health check is read-only.** `rclone lsd` with timeouts — never `rclone about` (Box unsupported),
   never write/delete LIVE data.
4. **Idempotent + atomic.** Converge by sha256 diff; write temp on the SAME volume -> fsync -> rename.
5. **Self-heal is bounded.** Retry only transient (429/network); `invalid_grant` is a broken chain ->
   CRITICAL, never blind-retry, never delete-and-recreate the remote.

## Acceptance gate (program-adjudicable)

`python tests/run_gate.py` runs 10 signals (refresh logic, idempotency, multi-host consistency,
config validation, dry-run no-op, secret hygiene, probe classification, anti-pattern guard, atomic
write, CLI contract) with NO real Box credentials. All pass = green. Real end-to-end binding is the
single remaining gap (see `reference/runbook.md` one-time authorization).

## Progressive loading

This `SKILL.md` is the only always-loaded file. Read one `reference/<shard>.md` on demand.
