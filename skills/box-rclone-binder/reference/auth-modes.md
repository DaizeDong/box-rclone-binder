# Choosing the auth mode (the load-bearing decision)

## Why OAuth user-flow cannot be shared across hosts

Box OAuth `refresh_token` is **single-use + rotating**: access tokens live 60 min; each refresh
immediately invalidates the old refresh_token and issues a new one (60-day / one-use). rclone
trusts the token `expiry` and writes the rotated token back to its conf. Copy one conf to N hosts
and the first host to refresh breaks the other N-1 with `Invalid refresh token`. rclone's
concurrency guard only protects a *shared* conf on one machine — it does nothing across hosts.

**Therefore: for multi-host, remove the rotating secret. Use Box server auth.**

## jwt (default, recommended)

rclone's Box backend natively implements JWT: it reads `config.json`, signs a JWT assertion with
the RSA private key locally, exchanges it for an access token, and a resident `tokenRenewer`
re-mints automatically. No refresh_token, no browser, no rotation. The SAME `config.json` can sit
on every host; each mints its own short-lived access token independently -> consistent by
construction.

- Long-term credential: `config.json` (contains an RSA private key) — chmod 600, gitignored,
  injected from the secret backend, never in the repo.
- Service-account identity: JWT authenticates `AutomationUser_*@boxdevedition.com`, which sees
  none of a human's files. Add it as a folder **collaborator (Editor)** or use
  `--box-impersonate <userID>`, and lock `root_folder_id` to the target folder.

## ccg-native / ccg-mint (no private key)

Client Credentials Grant trades the RSA key for `client_id` + `client_secret`. rclone CCG support
is version-dependent (source review shows `--box-client-credentials` may not send the mandatory
`box_subject_id`, yielding HTTP 400). So:

- `box-binder doctor` probes whether CCG-native actually mints a token across a 60-min boundary.
- If native works -> `auth_mode: ccg-native` (env-var remote, zero external timer).
- If not -> `auth_mode: ccg-mint`: `mint.sh` POSTs `grant_type=client_credentials` with
  `box_subject_type=enterprise` + `box_subject_id`, writes the access token to a chmod-600 @file,
  and a systemd timer re-mints every <60 min (rclone does NOT auto-renew a static access token).

## oauth-broker (degraded; personal Box only)

When there is no enterprise Admin to authorize a server app. One **master** holds and refreshes the
refresh_token under `flock`; it persists the new refresh_token FIRST, then distributes
**access-only** blobs (refresh_token stripped) to slaves, which are structurally unable to rotate.
This cannot be strictly unattended forever — a broken chain (`invalid_grant`) needs a one-time
browser re-auth — so it is a last resort.

## Decision flow

```
enterprise Box + Admin?  -- no --> personal: oauth-broker (accept the caveat)
        | yes
   prefer a private key?  -- no --> doctor: CCG-native works? -- yes --> ccg-native
        | yes                                   | no --> ccg-mint
       jwt
```
