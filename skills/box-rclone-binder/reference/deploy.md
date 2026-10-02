# Deploy the Linux runtime

`deploy --dry-run` reports a plan without constructing a driver, reading secret values, or
contacting hosts. `deploy` resolves configured env/file references, then handles each host
independently. A failed host produces a nonzero batch exit and a per-host failure.

## Installed files

- `/opt/box-binder/box_runtime.py` and the complete `boxbinder` Python package.
- `/opt/box-binder/install/mint.sh`, the standalone CCG helper.
- Executable `/usr/local/bin/box-binder-health`, plus mint or broker wrappers for those modes.
- Protected `host.json` and `secrets.env` under `config_dir`; the latter contains only the host-file path.
- Protected `/opt/box-binder/deployment.json`, recording the installed non-secret host settings
  independently of the requested configuration directory.
- JWT `config.json`, or CCG/master `runtime.json`, mode 0600. Broker slaves receive no long-term credential.
- Health service/timer, CCG mint service/timer, or the master's broker distribution service/timer.
- The master also receives a non-secret peer inventory and an initial broker state only when no state exists.

The configuration directory is mode 0700, wrappers and shell helpers 0755, package and unit
files 0644. The runtime reads credential files directly and supplies rclone settings through
its environment. Systemd variable interpolation and `token=@file` are not required.

Shared system directories and their path aliases are rejected as configuration destinations.
An existing destination must already be a directory with mode 0700; deployment refuses
to restrict the permissions of an existing shared directory. A missing destination is created
with mode 0700, and its type and mode are read back. Remote metadata checks reject symbolic links
in the destination path.

## Convergence and evidence

Deployment compares content and mode, writes through stdin to a temporary remote file,
atomically renames it, and reads back the result. Required directories and credential files
must exist with the intended mode. Failed SSH reads are distinct from missing files.

Every service-manager failure propagates. A reload marker advances only after daemon-reload
succeeds, allowing a failed reload to be retried after files already match. Every convergence
reads enabled/active timer state, repairs disabled or inactive timers, and reads state again.
A correct second deployment writes no files and performs no state-changing commands.

Installed timers must match the complete generated timer structure and point to the expected
service. Legacy custom schedules are read from the timer itself because older host settings
did not store interval fields. A timer with an unproven structure or target requires review.
When unit bytes or permissions drift despite a matching reload marker, deployment invalidates
the marker before rewriting any unit. An interrupted write or failed reload therefore remains
pending on the next invocation.

`status: configured` means the runtime and timers converged. It does not prove Box access.
Run `healthcheck` and `refresh` for fresh operation receipts. Python 3, rclone, systemd and
noninteractive SSH must already be available; this tool does not install or upgrade them.
The broker master also needs SSH access to each slave. Changing an existing host's auth mode
is rejected before mutation and requires the manual retirement procedure below.

## Changing an existing broker role

An apply deployment rejects a master/slave role change within oauth-broker before writing
configuration, credentials or timer state. Unreadable or invalid prior host.json is an error,
not a fresh deployment. A dry run describes desired artifacts only and does not inspect the
existing remote role. Persisted host settings must have a supported auth mode and valid host,
remote name and configuration path before prerequisites or mutations run. A missing broker_role
in otherwise valid legacy broker settings means slave, matching the inventory default. New
runtime settings persist that default explicitly, so an identical redeployment remains a no-op.

The migration check also reads the stable deployment locator, the installed Box binder service
definitions, and systemd's loaded unit locations. Changing `config_dir` cannot hide the prior
role. A directory change, missing prior settings, unproven installed units, or unit overrides
requires manual review before deployment can mutate the host.

Auth-mode, role or configuration-directory migration is manual. Stop and disable the old Box binder timers
and verify that their services are no longer running. Review broker-state.json, runtime.json and
peers.json in the old config directory; retain required credentials in a private backup and securely relocate the
obsolete master state. Do not copy rotating refresh credentials to a slave. After verifying
that cleanup, retire the old Box binder service and timer definitions and their overrides,
reload systemd, and verify that no old Box binder units remain loaded or active. Only then remove
the obsolete host.json and deployment locator and deploy the selected role and directory.
This command does not delete credentials or migrate roles automatically. When promoting a slave,
review the existing access-only state and the previous master before establishing the single new master.

On POSIX, an atomic persistence operation fails if parent-directory open, fsync or close fails.
Windows uses file fsync and replacement but cannot establish directory-entry crash durability
through the CRT interface; it reports that limit explicitly.
