# Roadmap

Current: **v0.1.2**

## v0.1.2 (current)
- `box-binder` CLI: `deploy | refresh | plan-refresh | healthcheck | status | verify-config | doctor`,
  JSON output and exit codes 0/1/2/3/4/5. Deploy/refresh support plans without host mutation.
- Four auth modes: `jwt` (default) / `ccg-native` / `ccg-mint` / `oauth-broker`.
- Idempotent declarative deploy (sha256 converge) + atomic writes (same-volume temp + fsync + rename).
- Read-only health probe (`rclone lsd`, never `about`) + stderr classification + multi-host consistency.
- Explicit refresh per auth mode; flock single-master broker (persist-then-distribute, access-only slaves).
- Secret hygiene: pointer-only `machines.yaml`, inline-secret rejection, gitignore, scrubbed alerts.
- Linux/systemd health, CCG mint and broker timers. Alert/classification helpers are separate
  from runtime scheduling and are not automatically dispatched.
- 10-signal deterministic acceptance gate (`tests/run_gate.py`) using synthetic inputs.
  Quote results only for the tested source and distinguish native/live checks from offline checks.

## Planned
- Controlled live acceptance: transport, timer firing, restart recovery and token-expiry renewal.
- Automatic alert dispatch, explicit transient-error backoff and optional cron installation.
- v0.2: wire G1/G2 (agent-skills-eval lift + held-out trigger rate) into the gate.
- v0.2: real end-to-end smoke test once Box authorization is provided (one-time, deferred to user).
- v0.3: optional secret backends beyond env/file (1Password / Vault / AWS SSM resolvers).
- v0.3: rclone mount unit support (`--vfs-cache-mode`, `Restart=on-failure`) for long-lived mounts.
