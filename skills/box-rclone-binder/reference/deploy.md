# deploy, idempotent, atomic, multi-host

## What it converges

Per host, `box-binder deploy` renders a **desired artifact set** (none contain secrets):

- `/etc/box-binder/secrets.env`, the fileless rclone env-var remote (`RCLONE_CONFIG_BOX_*`).
  Secret-bearing vars (CLIENT_SECRET, the JWT config.json path content) are NOT here; they come
  from a second `EnvironmentFile` injected from the secret backend at runtime.
- `/etc/systemd/system/box-binder-health.{service,timer}`, oneshot probe + `OnCalendar=*:0/15`.
- ccg-mint only: `box-binder-mint.timer` (`*:0/45`, must be < 60 min).

## Idempotency contract

Declarative converge: read current file -> sha256 -> compare to desired -> write only on
mismatch. Reload systemd only if something changed. Running deploy twice on a correct host =
**0 changes, 0 mutations**. Verified by signal S2; `--dry-run` (signal S5) plans without touching
any host (no ssh, no writes) and is byte-deterministic (`artifact_fingerprint`).

## Atomic writes (local and remote)

`atomic.atomic_write`: temp file in the SAME directory as the destination -> write -> fsync ->
`os.replace` -> directory fsync. Cross-volume rename is refused (`CrossVolumeError`), a
cross-fs rename is non-atomic and can fail (rclone issue #6656). A crash before `os.replace`
leaves the destination untouched and no `.bbtmp.*` leftover (signal S9). The remote SSH driver
does the same: `mktemp` in the dest dir, content via **stdin** (never argv), `chmod 600`, `mv -f`,
`sync`.

## Secret injection (no plaintext on disk-by-default, never on argv)

1. JWT: `install -m600` the same `config.json` to `/etc/box-binder/config.json` (out-of-band scp,
   content from the secret backend).
2. CCG: client credentials injected via the runtime `EnvironmentFile` (env-var remote), or
   re-minted by `mint.sh` to a chmod-600 @file.
3. Encrypted-conf fallback only: `rclone config encryption set` + runtime `RCLONE_CONFIG_PASS` /
   `--password-command` + `--ask-password=false` (so a missing password fails fast, never hangs).

## Per-host loop

ssh reachability -> rclone >= `rclone_min_version` (else `rclone selfupdate`) -> inject credentials
-> install timer -> read-only smoke test (`rclone lsd box: --max-depth 1`) -> sha256 idempotency
check. Each host is independent; the batch reports partial failure (exit 1) without aborting the
rest.
