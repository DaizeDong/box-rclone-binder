# refresh, healthcheck, and alerts

## healthcheck, read-only probe

`rclone lsd box: --max-depth 1 --contimeout 15s --timeout 30s --low-level-retries 1 --retries 1`.

- **Never** `rclone about box:`, the Box backend does not support it (always errors).
- **Always** bounded by timeouts so a hung remote cannot stall the multi-host barrier.
- It only LISTS; it never writes or deletes LIVE data.

stderr is classified (signal S7) and routed:

| pattern | category | action |
|---|---|---|
| `Invalid refresh token` / `401` / `Unauthorized` / `token expired` / `invalid_grant` | auth | self-heal |
| `429` / `rate limit` / `Retry-After` | ratelimit | back off + retry |
| `timeout` / `connection refused` / `dial tcp` / `i/o timeout` | network | back off + retry |
| anything else with rc != 0 | unknown | fail (surface, do not guess) |

## Multi-host consistency

Each host probes **independently** (no shared token, no shared conf). `consistency()` flags drift
on `auth_mode / root_folder_id / box_sub_type / remote_name / rclone_version`.
`refresh_token_invariant()` enforces: server-auth modes -> **0** hosts hold a refresh_token;
oauth-broker -> **exactly one** (the master). A violation is a structural alarm.

## refresh, by auth_mode

- **jwt / ccg-native**: validation no-op. rclone auto-renews; refresh only re-probes that the
  credential is still authorized. Failure escalates; success is a quiet INFO.
- **ccg-mint**: re-mint via `mint.sh` (correct CCG body, @file injection), then re-probe.
- **oauth-broker**: `flock` single-master refresh. Order is load-bearing: **persist the new
  refresh_token first**, then render access-only slave blobs. `invalid_grant` = broken chain ->
  NonRetryable -> CRITICAL (manual re-auth), never blind-retry, never delete+recreate the remote.

Retry policy: exponential backoff + jitter for 429/5xx/network (honor `Retry-After`); zero retries
for `invalid_grant`.

## Alerts (Discord, via the configured notifier -- see `alerts.relay` / `BOX_RCLONE_BINDER_NOTIFIER`)

Severity routing: transient-recovered = log only (no push); auth self-healed = INFO; self-heal
failed / broken chain = CRITICAL + runbook; structure drift = WARN. **Every** message passes
`alerts.scrub()` which redacts JWTs, `token=`/`secret=` assignments, PEM blocks, and long opaque
blobs, a secret can never reach the relay. Stagger probes/alerts with `jitter_sec` to dodge 429.

## Scheduling

systemd timer (`OnCalendar=*:0/15`, `Persistent=true`) preferred; cron fallback uses a marker
block (`# >>> box-binder >>> ... <<<`) replaced whole, never appended. Optionally layer a
healthchecks.io dead-man ping to catch "the job itself never ran".
