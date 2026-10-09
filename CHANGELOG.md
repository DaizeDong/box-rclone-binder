# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]

### Changed
- Set a 64 MiB companion working-data review threshold. Required observations and
  recovery state stay protected when the threshold is exceeded.
- Document the retired maintenance status and retain former expansion plans as deferred work.
- Clarify the authentication tradeoff and keep configured, synthetic-tested and live-accepted outcomes separate.

### Fixed
- Separate schema validity from local readiness: required host references now cause NOT READY when unavailable. Retained profiles each use a separate companion with machines.yaml, and discovery documentation includes shared fallbacks.
- Declare an explicit companion identity for existing PRIVATE stores whose Git
  configuration cannot establish convention-based discovery.
- Resolve installed configuration discovery from the real script location, so linked installations find the pinned Guards resolver.
- Align the runtime package version with the existing 0.1.2 plugin declaration.
- Correct alert delivery reporting: explicit relays use `send --stream infra --text <msg>`,
  matching the environment branch, and expand `~`. Missing scripts fail before spawn;
  `pushed` is true only on exit 0. Nonzero exits, timeouts and policy skips retain a scrubbed
  reason. The prior `[python, relay, message]` invocation exited 2 but was reported as delivered.
  The recorded regression result was 10 `AlertDeliveryHonesty` checks and 43 tests passing.
- Correct the generated `alerts.relay` example from a notifier to a relay path. CONFIG.md
  documents resolution order and the exit-0 requirement and removes stray table commas.

## [0.1.2] - 2026-07-06
### Fixed
- **SKILL.md over-claim removed:** the `doctor` workflow step said it probes "CCG capability", but
  `cmd_doctor` only checks rclone version / ssh / systemd, description corrected to match the code.
- **Dead code removed:** `remote.py` `render_cron_block` / `merge_cron` / `CRON_BEGIN` / `CRON_END`
  had zero callers (deploy converges only systemd units via `desired_artifacts`); the unused raw-cron
  renderer is deleted. Scheduling remains the systemd timer. 33 tests still pass.

## [0.1.1] - 2026-06-27
### Changed
- **Discord egress unified through Agent Center relay**: pushes now prefer schedule-reminder's
  `relay.py send --stream infra` (per-stream identity in the Agent Center server) when the base
  is installed, and **fall back to the Big Brother relay (send.py) when it is not**, fully
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
  test, handed off to the user; see `skills/box-rclone-binder/reference/runbook.md`.
