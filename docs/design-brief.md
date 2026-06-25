# Design Brief — box-rclone-binder

> Produced by skill-smith Step 0 (research-first). The design rationale, auditable. Full architecture
> lives in the planning repo's `ARCHITECTURE.md` (6-route cross-validated research, 2026-06-25).

## Best references (match-or-beat)
- rclone official Box backend + docs (JWT native support, `tokenRenewer`, the documented multi-host
  refresh-token failure mode).
- Box developer docs: token lifetimes (access 60 min; refresh 60-day, single-use, rotating); server
  auth (JWT / CCG) with `box_subject_type`/`box_subject_id`.
- rclone source `backend/box/box.go` + `lib/oauthutil` (JWT path, static-token non-renewal, absence
  of CCG body handling) — the decisive evidence for defaulting to JWT.

## Frontier ideas to incorporate
- Server auth as the *root fix* for multi-host (each host mints locally; no shared rotating secret).
- Fileless env-var rclone remote via systemd `EnvironmentFile`; secret values only at runtime.
- Program-adjudicable acceptance signals runnable with NO real credentials (fake token endpoint +
  injectable host driver) so CI / self-evolve can gate regressions.

## Anti-patterns to avoid
- Sharing a rotating refresh_token across hosts (`invalid_grant`); `rclone about` for health (Box
  unsupported); token on argv; cross-fs rename; `-vv`/`config dump` to logs; blind delete-recreate
  self-heal; synchronized probes (429). Full list: `reference/anti-patterns.md`.

## Proof bar (how we will show it is tested-real)
- 10 signals (S1–S10) in `tests/test_signals.py`, aggregated by `tests/run_gate.py` to JSON. Green
  with no real Box credentials. v0.2 adds eval-lift (G1) + held-out trigger rate (G2).

## Scope & focus (one job, <=3 modules)
- One job: bind one Box drive to many servers via rclone and keep it alive. Three modules:
  **deploy / refresh / healthcheck** (+ thin helpers status / verify-config / doctor).
