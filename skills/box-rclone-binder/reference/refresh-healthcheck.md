# Refresh and health checks

`plan-refresh` and `refresh --dry-run` return `status: planned`, `executed: false` and per-host
actions. They contact no hosts. `refresh` invokes the deployed runtime and requires a receipt
matching the current operation identifier, host, action and auth mode. Stale or malformed success
output fails. The batch distinguishes completed, partial and failed outcomes.

## Auth dispatch

- JWT and native CCG validate with bounded `rclone lsd`; rclone performs native renewal.
- CCG mint requests a fresh access token, validates the response, persists access-only JSON,
  then validates access. Its systemd mint service runs on the configured interval below 60 minutes.
- OAuth broker requires one master. An OS lock serializes refresh, the new rotating token is
  persisted first, then access-only JSON is copied to slaves through protected SSH input.
  Each slave's token file is read back and its access is validated. The master's broker timer
  runs the same refresh/distribution sequence using its installed peer inventory.

Use `refresh --host node2.example.com` to retry a failed host. A slave-only broker retry reads
the master's still-valid access token without rotating again. An unavailable or expired source
blocks distribution. Missing rotated-token responses and `invalid_grant` require attention;
there is no blind retry within the command. Reauthorization remains a human operation.

## Secret transport

Credential values are never included in operation receipts or command arguments. Runtime
token requests use HTTP request bodies. The standalone `mint.sh` URL-encodes form fields into
a mode-0600 temporary file and supplies them through curl stdin. It validates and normalizes
the response before replacing the access file, and removes temporary files on failure.
Rclone receives the JSON token through its environment, loaded from the protected file.

## Health and scheduling boundaries

The CLI and health timer invoke the installed runtime, which constructs the same credential
environment as refresh. Its `rclone lsd` is read-only and bounded; command output is captured
and excluded from reports. A failed validation is a failure, even if an earlier receipt succeeded.

Health receipts include the nonsecret host settings used by the remote runtime. The controller
compares those observations rather than substituting its requested inventory. A divergence causes
a nonzero healthcheck exit. Missing or older receipts cannot certify deployed consistency.
`unobserved_fields` identifies missing measurements; `consistent: null` means the report cannot
establish full consistency. The runtime does not measure `rclone_version`. Token placement is
reported from the observed authentication configuration, not from a scan of every remote file.
An explicitly false refresh_token_invariant makes healthcheck exit nonzero. Unknown observations
remain null unless the observed hosts already prove a violation; two observed masters remain a
violation even when another host is unobserved.

`doctor --json` reports every selected host even when a driver or probe fails. It preserves
completed observations, uses safe error descriptions, and returns nonzero when rclone output
is missing, either probe fails, or systemd is unavailable. Remote stderr is excluded.

The shipped automation targets Linux/systemd. Cron installation, automatic alert dispatch,
in-command backoff and native CCG compatibility across token expiry are not implemented or
established by this repair. The separate alert/classification helpers remain available, but
their presence does not prove automatic recovery or delivery. Real authorization, timer firing,
restart persistence and token-expiry behavior require controlled live acceptance.
