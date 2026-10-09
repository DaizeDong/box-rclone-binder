# Design Brief, box-rclone-binder

> Historical design brief from skill-smith Step 0. The planning repository's
> `ARCHITECTURE.md` records the six-route research from 2026-06-25. The retained
> implementation and deferred work are summarized in [ROADMAP.md](../ROADMAP.md).

## Source references
- rclone official Box backend + docs (JWT native support, `tokenRenewer`, the documented multi-host
  refresh-token failure mode).
- Box developer docs: token lifetimes (access 60 min; refresh 60-day, single-use, rotating); server
  auth (JWT / CCG) with `box_subject_type`/`box_subject_id`.
- rclone source `backend/box/box.go` + `lib/oauthutil` (JWT path, static-token non-renewal, absence
  of CCG body handling), the decisive evidence for defaulting to JWT.

## Design choices
- Server authentication for independent per-host token minting without a shared rotating secret.
- Fileless env-var rclone remote via systemd `EnvironmentFile`; secret values only at runtime.
- Program-adjudicable acceptance signals runnable with NO real credentials (fake token endpoint +
  injectable host driver) so CI / self-evolve can gate regressions.

## Anti-patterns to avoid
- Sharing a rotating refresh_token across hosts (`invalid_grant`); `rclone about` for health (Box
  unsupported); token on argv; cross-fs rename; `-vv`/`config dump` to logs; blind delete-recreate
  self-heal; synchronized probes (429). Full list: [anti-patterns](../skills/box-rclone-binder/reference/anti-patterns.md).

## Verification and deferred evaluation
- 10 signals (S1 to S10) in `tests/test_signals.py`, aggregated by `tests/run_gate.py` to JSON. Green
  with no real Box credentials. The former v0.2 eval-lift (G1) and held-out trigger-rate
  (G2) work remains deferred; synthetic checks do not establish live deployment.

## Scope
- One job: bind one Box drive to many servers via rclone and keep it alive. Three modules:
  **deploy / refresh / healthcheck** (+ thin helpers status / verify-config / doctor).
