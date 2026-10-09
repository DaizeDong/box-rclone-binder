# box-rclone-binder, Design Philosophy

**Maintenance status:** retired from active development. These principles explain the retained implementation.

Multi-host Box access depends on the authentication model as well as deployment.
A rotating OAuth refresh token creates coordination requirements that file
deployment alone cannot resolve.

## P1, Independent token renewal

Box OAuth refresh tokens are single-use and rotate when refreshed. Copying one to
multiple hosts lets the first refresh invalidate the others, even when access
tokens have a 60-minute lifetime. A broker can coordinate this shared state but
must preserve the rotation chain and support manual reauthorization.

The default JWT mode gives each host the same long-term server credential so it
can mint short-lived access tokens independently through rclone's resident
renewer. This requires Box application setup and administrative authorization.
The oauth-broker mode remains available for personal accounts that cannot use
server authentication.

## P2, Separate configuration and operational evidence

Generated files and a started daemon do not prove valid authorization, timer
firing or renewal. The credential-free acceptance gate checks ten synthetic
signals: refresh logic, idempotency, multi-host invariants, configuration,
dry-run behavior, secret hygiene, error classification, anti-pattern rejection,
atomic writes and the CLI contract. Actual authorization, transport, scheduling,
restart persistence and token-expiry behavior require separate live evidence.

## P3, Secret references and protected transport

`machines.yaml` contains only `*_ref` pointers. Values come from a secret backend
at runtime; protected file input and child environments keep them out of argv.
`verify-config` rejects inline secrets, alert output is scrubbed, and logs use
`-v`, never `-vv`. Secret-bearing files are excluded from the public repository;
private versioned backups follow the owner's policy.

## P4, Idempotent updates and explicit recovery

Deployment compares SHA-256 values and writes only changed content, using a
same-volume temporary file, fsync and rename. It checks systemd timers and
reports bounded access validation and per-host refresh outcomes. Atomic writes
limit partial state; idempotent convergence allows the operator to retry.

Transient failures require a manual retry or the next scheduled run.
`invalid_grant` stops the command for manual reauthorization. Automatic alert
dispatch, cron installation and in-command backoff remain deferred without an
active delivery commitment.
