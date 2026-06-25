# box-rclone-binder — Design Philosophy

> One test governs every change: **does it fix the framing, or just patch a symptom?**

The whole tool follows from one reframing: *binding one Box drive to many servers is an auth-model
problem wearing a deploy problem's clothes.* Patch the deploy and you keep fighting expired tokens
forever; fix the auth model and the deploy becomes boring.

## P1 — Remove the shared rotating secret, don't coordinate it

- **Symptom patch:** keep one OAuth `refresh_token` and build a clever broker/lock so only one host
  refreshes at a time, racing a 60-minute clock across machines.
- **Root cause:** the rotating, single-use `refresh_token` is *structurally* unshareable — any host
  that refreshes invalidates the others. Coordination only narrows the race; it never removes it.
- **Decision it produced:** default to Box **server auth (JWT)**. Each host holds the same long-term
  credential and mints its own short-lived access token locally via rclone's resident renewer. No
  refresh_token exists to share, so multi-host consistency is the default, not an achievement.
  oauth-broker survives only as an explicitly-degraded last resort for personal Box.

## P2 — Generated/configured != working; prove it under mocks

- **Symptom patch:** ship once `rclone config` succeeds and the daemon starts.
- **Root cause:** "it looks configured" hides silent breakage (the token that will rotate-fail in 60
  days, the cron that never fires, the secret that leaked into a log).
- **Decision it produced:** a 10-signal, program-adjudicable acceptance gate that runs with **no real
  Box credentials** — refresh logic, idempotency, multi-host invariants, config validation, dry-run
  no-op, secret hygiene, error classification, anti-pattern rejection, atomic writes, CLI contract.
  Green gate is the bar; the only thing left is the human one-time authorization.

## P3 — Secrets are referenced, never possessed

- **Symptom patch:** chmod-600 the conf and hope nobody commits it.
- **Root cause:** any secret the tool *stores* is a secret it can *leak* — via repo, log, argv, or
  image layer.
- **Decision it produced:** `machines.yaml` carries only pointers (`*_ref`); values come from a
  secret backend at runtime; tokens are injected by `@file`/env, never on argv; `verify-config`
  hard-fails on inline secrets; alerts are scrubbed; gitignore blocks the whole secret-bearing set;
  logs are `-v`, never `-vv`.

## P4 — Converge declaratively; mutate atomically; heal within bounds

- **Symptom patch:** re-run the installer and append to cron each time.
- **Root cause:** non-idempotent, non-atomic operations drift hosts apart and corrupt state on
  partial failure; unbounded self-heal amplifies outages.
- **Decision it produced:** sha256 diff -> write only what changed; temp on the same volume ->
  fsync -> rename; cron marker-block replaced whole; self-heal retries only transient errors and
  treats `invalid_grant` as a stop-and-alert broken chain — never delete-and-recreate the remote.
