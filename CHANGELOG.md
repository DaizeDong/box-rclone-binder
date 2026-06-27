# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [0.1.1] - 2026-06-27
### Changed
- **Discord egress unified through Agent Center relay**: pushes now prefer schedule-reminder's
  `relay.py send --stream infra` (per-stream identity in the Agent Center server) when the base
  is installed, and **fall back to the Big Brother relay (send.py) when it is not** — fully
  pluggable, no behaviour change when the base is absent. Existing env/arg overrides still win.

## [0.1.0] - 2026-06-25
### Added
- Initial release of `box-rclone-binder`.
- `box-binder` CLI with `deploy / refresh / healthcheck / status / verify-config / doctor`,
  `--json` + `--dry-run`, and exit codes 0/1/2/3/4/5.
- Four auth modes (jwt default, ccg-native, ccg-mint, oauth-broker) selected via `doctor`.
- Idempotent declarative deploy, atomic same-volume writes, read-only health probing,
  per-mode refresh/self-heal, flock single-master broker, and pointer-only secret handling.
- systemd timer + cron scheduling, severity-routed scrubbed Discord alerts.
- 10-signal mock acceptance gate (`tests/run_gate.py`) passing with no real Box credentials.

### Deferred (gap)
- One-time Box app authorization (login/Admin) and the real end-to-end `rclone lsd box:` smoke
  test — handed off to the user; see `skills/box-rclone-binder/reference/runbook.md`.
