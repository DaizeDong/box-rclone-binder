# Deploy the Linux runtime

`deploy --dry-run` reports a plan without constructing a driver, reading secret values, or
contacting hosts. `deploy` resolves configured env/file references, then handles each host
independently. A failed host produces a nonzero batch exit and a per-host failure.

## Installed files

- `/opt/box-binder/box_runtime.py` and the complete `boxbinder` Python package.
- `/opt/box-binder/install/mint.sh`, the standalone CCG helper.
- Executable `/usr/local/bin/box-binder-health`, plus mint or broker wrappers for those modes.
- Protected `host.json` and `secrets.env` under `config_dir`; the latter contains only the host-file path.
- JWT `config.json`, or CCG/master `runtime.json`, mode 0600. Broker slaves receive no long-term credential.
- Health service/timer, CCG mint service/timer, or the master's broker distribution service/timer.
- The master also receives a non-secret peer inventory and an initial broker state only when no state exists.

The configuration directory is mode 0700, wrappers and shell helpers 0755, package and unit
files 0644. The runtime reads credential files directly and supplies rclone settings through
its environment. Systemd variable interpolation and `token=@file` are not required.

## Convergence and evidence

Deployment compares content and mode, writes through stdin to a temporary remote file,
atomically renames it, and reads back the result. Required directories and credential files
must exist with the intended mode. Failed SSH reads are distinct from missing files.

Every service-manager failure propagates. A reload marker advances only after daemon-reload
succeeds, allowing a failed reload to be retried after files already match. Every convergence
reads enabled/active timer state, repairs disabled or inactive timers, and reads state again.
A correct second deployment writes no files and performs no state-changing commands.

`status: configured` means the runtime and timers converged. It does not prove Box access.
Run `healthcheck` and `refresh` for fresh operation receipts. Python 3, rclone, systemd and
noninteractive SSH must already be available; this tool does not install or upgrade them.
The broker master also needs SSH access to each slave. Changing an existing host's auth mode
requires reviewing and disabling its old mode-specific timer before deployment.
