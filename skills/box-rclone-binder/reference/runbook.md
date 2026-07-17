# Runbook, one-time setup + incident response

## One-time authorization (the deferred gap, user action, login/approval only)

box-binder automates everything EXCEPT the initial Box authorization, which requires a human
login / Admin approval. Do this once:

1. Box Developer Console -> **Create Custom App** -> **Server Authentication (JWT)** (preferred) or
   (CCG). Enable 2FA on the account (required to generate a keypair).
2. Generate the keypair -> download `config.json` (JWT) or note `client_id` + `client_secret` (CCG).
3. Admin Console -> **Authorize App** -> grant scopes (read/write all files and folders).
4. Folder visibility: either add the service account (`AutomationUser_*@boxdevedition.com`) as a
   **collaborator (Editor)** on the target folder, OR enable `--box-impersonate <userID>`.
   Set `root_folder_id` (the Box web URL tail) in `machines.yaml`.
5. Deliver the credential to the secret backend over a secure channel (never echoed, never
   committed). box-binder reads it by reference at runtime.
6. After any app-settings change: Admin Console -> **Reauthorize**.

> Until this is done, the gate (`tests/run_gate.py`) still fully validates logic under mocks; the
> only thing that cannot run is the real end-to-end `rclone lsd box:` smoke test.

## Incidents

| symptom | category | response |
|---|---|---|
| `Invalid refresh token` / `token expired` | auth | `box-binder refresh -H <host>`; jwt/ccg re-mint locally; if it persists, credential was revoked -> re-authorize |
| `invalid_grant` (broker) | broken chain | CRITICAL. Manual browser re-auth, repopulate master `state.json`, re-run refresh |
| `429` / rate limit | transient | automatic backoff; widen `jitter_sec`; do nothing |
| network timeout | transient | automatic retry; check host/network |
| consistency divergence | drift | WARN; re-run `deploy` to converge the lagging host; bump rclone if version drift |
| timer not firing | scheduling | `systemctl status box-binder-health.timer`; check `OnCalendar`; consider healthchecks.io dead-man |

## Health & verification commands (read-only)

```bash
box-binder doctor        -c machines.yaml --json
box-binder verify-config -c machines.yaml --json
box-binder healthcheck   -c machines.yaml --json
box-binder status        -c machines.yaml --json
python tests/run_gate.py     # full mock acceptance gate (no real Box needed)
```
