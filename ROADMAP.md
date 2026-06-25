# Roadmap

Current: **v0.1.0**

## v0.1.0 (current)
- `box-binder` CLI: `deploy | refresh | healthcheck | status | verify-config | doctor`, all with
  `--json` and `--dry-run`, exit codes 0/1/2/3/4/5.
- Four auth modes: `jwt` (default) / `ccg-native` / `ccg-mint` / `oauth-broker`.
- Idempotent declarative deploy (sha256 converge) + atomic writes (same-volume temp + fsync + rename).
- Read-only health probe (`rclone lsd`, never `about`) + stderr classification + multi-host consistency.
- Refresh/self-heal per auth mode; flock single-master broker (persist-then-distribute, access-only slaves).
- Secret hygiene: pointer-only `machines.yaml`, inline-secret rejection, gitignore, scrubbed alerts.
- systemd timer + cron marker-block scheduling; Discord severity-routed alerts.
- 10-signal program-adjudicable acceptance gate (`tests/run_gate.py`), green with no real Box creds.

## Planned
- v0.2: wire G1/G2 (agent-skills-eval lift + held-out trigger rate) into the gate.
- v0.2: real end-to-end smoke test once Box authorization is provided (one-time, deferred to user).
- v0.3: optional secret backends beyond env/file (1Password / Vault / AWS SSM resolvers).
- v0.3: rclone mount unit support (`--vfs-cache-mode`, `Restart=on-failure`) for long-lived mounts.
