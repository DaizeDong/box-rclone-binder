# Runbook, one-time setup + incident response

## One-time authorization (the deferred gap, user action, login/approval only)

Prepare the Box authorization and protected credential source before deployment. Authorization
requires a human login and, where applicable, Admin approval. Then verify access and scheduling
on the target machines; a successful offline test does not establish either. Start with:

1. Box Developer Console -> **Create Custom App** -> **Server Authentication (JWT)** (preferred) or
   (CCG). Enable 2FA on the account (required to generate a keypair).
2. Generate the keypair -> download `config.json` (JWT) or note `client_id` + `client_secret` (CCG).
3. Admin Console -> **Authorize App** -> grant scopes (read/write all files and folders).
4. Folder visibility: either add the service account (`AutomationUser_*@boxdevedition.com`) as a
   **collaborator (Editor)** on the target folder, OR enable `--box-impersonate <userID>`.
   Set `root_folder_id` (the Box web URL tail) in `machines.yaml`.
5. Deliver the credential to the secret backend over a secure channel (never echoed, never
   put in a public repository). Private versioned backups are allowed. box-binder reads references at runtime.
6. After any app-settings change: Admin Console -> **Reauthorize**.

> The offline gate checks deterministic behavior with synthetic inputs. Authorization, real SSH
> transport, timer firing, restarts and renewal across token expiry require separate live acceptance.

## Incidents

| symptom | category | response |
|---|---|---|
| `Invalid refresh token` / `token expired` | auth | Run `box-binder refresh -H <host>` after diagnosis. JWT/native CCG validate access; CCG mint requests a token. A persistent failure needs credential and authorization review. |
| `invalid_grant` (broker) | broken chain | Reauthorize manually, securely replace the master's `<config_dir>/broker-state.json`, then retry refresh. The command reports failure; it does not send an automatic alert. |
| `429` / rate limit | transient | Wait for the service's retry interval and retry the command manually. The runtime has no in-command backoff; `jitter_sec` does not schedule retries. |
| network timeout | transient | Check connectivity and retry manually. The next configured systemd timer invocation is a new attempt, not an immediate retry. |
| consistency divergence | drift | Inspect the reported runtime settings and re-deploy the intended configuration. An unobserved field is unknown; check rclone versions separately. |
| timer not firing | scheduling | `systemctl status box-binder-health.timer`; check `OnCalendar`; consider healthchecks.io dead-man |

## Health & verification commands (read-only)

```bash
export BOX_RCLONE_BINDER_CONFIG_DIR=/path/to/private-companion
box-binder doctor        --json
box-binder verify-config --json
box-binder healthcheck   --json
box-binder status        --json
python tests/run_gate.py     # full mock acceptance gate (no real Box needed)
```
