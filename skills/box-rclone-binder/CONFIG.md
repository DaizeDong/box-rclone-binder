# box-rclone-binder, Config

`box-rclone-binder` is **config-bearing**: it reads a per-fleet machine inventory (`machines.yaml`)
that lists your hosts, auth mode, and **pointers** to where secrets live. This file is the
authoritative config contract (config-spec E1).

The public examples and private configuration have separate homes:

| Artifact | Versioned location | Role |
|---|---|---|
| `config/machines.example.yaml` | public tool repository | generated synthetic template with no secret values |
| `machines.yaml` | private companion repository | your real inventory and secret references |
| `config/secrets.env.example` | public tool repository | generated synthetic environment example |
| `*.env` / `*.pem` / `*.key` / `rclone.conf` | private storage or private backup repository | the real secret material (Mode B) |

The cardinal invariant: **`machines.yaml` never holds a secret value, only a pointer.**
`box-binder verify-config` hard-fails (and `config.py` refuses to load) if a literal secret appears.

## Discovery convention (how the skill finds your config), E2

`box-binder` resolves the `machines.yaml` path in this order; the first that resolves wins:

1. `-c/--config <path>`, explicit flag (a file, or a dir holding `machines.yaml`).
2. `$BOX_RCLONE_BINDER_CONFIG`, env var; a file path, or a dir holding `machines.yaml` (recommended; location-independent).
3. `$BOX_RCLONE_BINDER_CONFIG_DIR`, accepted alias; a dir holding `machines.yaml`.
4. `machines.yaml` in the private companion resolved by `guards/tools/datadir.py`.

A selected path is retained even when the file is missing; another inventory cannot silently
replace it. Without a companion or explicit selection, initialization and verification exit
`EXIT_CONFIG (3)` with setup guidance. A missing or broken guard resolver also fails explicitly.
Keep real configurations in the private companion's Git history, outside the public tool tree.

## Schema, `machines.yaml` (E1)

```yaml
version: 1
defaults: { ... }     # per-host fallbacks (any key here is overridable per host)
secrets:  { ... }     # source + pointer refs only
hosts:    [ ... ]     # >= 1 host; each may override any defaults key
alerts:   { ... }     # optional
```

### `version` (a.k.a. `schema_version`)

The top-level `version` field is the config **`schema_version`**, it pins which `machines.yaml`
schema the loader should expect. The loader accepts `schema_version` as an alias for `version`. Only integer `1` is supported;
booleans, strings, future versions and conflicting aliases are rejected. Sections must be mappings
and hosts must be a list of mappings. Parse and schema errors produce redacted configuration errors.
The bundled parser supports JSON and block YAML; unsupported YAML syntax is rejected. PyYAML
adds presentation support, but duplicate keys and inline secret values remain invalid.

| Field | Type | Required | Default | Example |
|---|---|---|---|---|
| `version` (alias `schema_version`) | int | recommended | `1` | `1` |

### `defaults` (mapping, per-host fallbacks)

| Field | Type | Required | Default | Example / allowed |
|---|---|---|---|---|
| `auth_mode` | enum | no | `jwt` | `jwt` \| `ccg-native` \| `ccg-mint` \| `oauth-broker` |
| `box_sub_type` | enum | no | `enterprise` | `enterprise` \| `user` |
| `remote_name` | str | **yes** (here or per host) | `box` | `box` |
| `root_folder_id` | str | no | `"0"` | `"0"` (service-account root) |
| `rclone_min_version` | str | no | none | `"1.71.0"` |
| `config_dir` | str (remote path) | no | `/etc/box-binder` | `/etc/box-binder` |
| `health_interval` | str (systemd OnCalendar) | no | none | `"*:0/15"` (every 15 min) |
| `mint_interval_min` | int | no (ccg-mint only) | `45` | `45` (re-mint before 60-min expiry) |
| `broker_role` | enum | for oauth-broker | `slave` | exactly one host must be `master` |
| `impersonate_user_id` | str | no | `""` | `"1234567"` (as-user) |

`config_dir` must name a dedicated absolute Linux directory. Validation rejects traversal,
trailing slashes, and every spelling that normalizes to the root directory, including `/.`
and `//./.`. The same rule applies to existing `host.json` settings before deployment changes
files, directory modes, or services.

### `secrets` (mapping, WHERE secrets live, never the value)

| Field | Type | Required | Allowed / shape |
|---|---|---|---|
| `source` | enum | no (default `env`) | `env` \| `file` \| `op` \| `vault` \| `aws-ssm` |
| `jwt_config_ref` | pointer | for `jwt` | see pointer shape below |
| `client_id_ref` | pointer | for `ccg-*` | pointer |
| `client_secret_ref` | pointer | for `ccg-*` | pointer |
| `box_subject_id_ref` | pointer | for `ccg-*` | pointer |
| `rclone_config_pass_ref` | pointer | only for encrypted-conf fallback | pointer |
| `broker_state_ref` | pointer | first deployment of broker master | JSON containing the initial refresh token |

Execution resolves `env` and `file` sources. Export `op`, `vault`, or `aws-ssm` secrets to one
of these sources first; unsupported sources fail before deployment. A per-host `secrets`
mapping may override the fleet references. Slaves receive no client credentials or refresh token.
The master persists rotated state and deployment never replaces that state with an old seed.
Its scheduled distributor requires noninteractive SSH access to the configured slaves.

**Pointer shape (enforced):** `env` uses an uppercase environment variable name, `file` uses an
absolute path, and `op`/`vault`/`aws-ssm` use a matching URI scheme. Empty and `<placeholder>`
values remain uninitialized. **Literal values are rejected.** The same checks apply after merging
fleet and per-host references; changing a host's source also changes how its inherited references
are validated. Example: `client_id_ref: BOX_BINDER_CLIENT_ID` (the value lives in `$BOX_BINDER_CLIENT_ID`).

### `hosts[]` (list, REQUIRED, ≥ 1)

| Field | Type | Required | Example |
|---|---|---|---|
| `host` | str | **yes** | `203.0.113.10` |
| `ssh` | str | no | `root@203.0.113.10` |
| `ssh_opts` | str | no | `-o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new` |
| *any `defaults` key* | per-type | no | per-host override, e.g. `root_folder_id: "987654"` |

### `alerts` (mapping, optional)

These fields configure the separate alert helper. The shipped runtime does not automatically
dispatch alerts or use `jitter_sec` for retry scheduling.

| Field | Type | Default | Example / allowed |
|---|---|---|---|
| `discord` | bool | `false` | `true` |
| `relay` | path | none | `~/.local/relay.py`, a relay that takes `send --stream infra --text <msg>` (or set `BOX_RCLONE_BINDER_RELAY`) |
| `on_recovered` | level | none | `INFO` |
| `on_heal_failed` | level | none | `CRITICAL` |
| `on_drift` | level | none | `WARN` |
| `jitter_sec` | int | none | `30` (stagger probes to dodge 429) |

**Egress resolution (`alerts.send`), in order:** the `relay` key above (a `~` in it is expanded),
then `$BOX_RCLONE_BINDER_RELAY` (default `~/.local/relay.py`) if that file exists, then a minimal
notifier at `$BOX_RCLONE_BINDER_NOTIFIER` (default `~/.local/notifier.py`). A relay is invoked as
`send --stream infra --text <message>`; a notifier gets the message as its first argument with
`--stream infra` trailing. Either way the stream is always `infra`, never the `mail` default.

**A push is reported as delivered only when the egress process exits 0.** A missing egress script,
a nonzero exit, or a timeout yields `pushed: false` plus a `reason`, so non-delivery is never
reported as success. Policy skips carry a `reason` too: a log-only severity such as `jitter`, or
`alerts.send(..., enabled=False)` for a caller that has turned alerting off.

**Validation (`box-binder verify-config`):** `hosts` non-empty; each host has `host` and a resolved
`remote_name`; each present defaults or host field above has its declared type and enum value,
including fields later overridden by a host. Omit optional fields to use their defaults;
explicit null is not a string. Folder IDs are nonempty numeric strings; an impersonation ID
may also be empty. `rclone_min_version` uses a version string such as `1.71.0` or `v1.71.0`;
this validates its shape and does not compare the remote installation. `ssh_opts` must have
balanced shell quoting and contain no line breaks or NUL. Secret sources and references are
validated separately; every `*_ref` is a pointer, and inline secrets are forbidden.

## Secrets, Mode B (E6)

Secrets stay outside the public tool repository. The live `machines.yaml` carries only `*_ref` pointers;
the real values live in your backend (`env`/`file`/`op`/`vault`/`aws-ssm`). The repo `.gitignore`
blocks `machines.yaml`, `*.env`, `secrets.env`, `*.pem`, `*.key`, `*.p12`, `rclone.conf`,
`credentials*`, `*.token`, and state files. `config.py` additionally scans for inline key/JWT/blob
material and refuses to load if any is found. The skill never echoes a secret value (only presence).
Private repositories may version the inventory, run records and credentials according to the
owner's backup policy; the public repository must never contain them.

## First-time setup (E3), succeeds on the first try

```bash
cd skills/box-rclone-binder

# 1. Clone or initialize your private companion repository, then select its directory:
export BOX_RCLONE_BINDER_CONFIG_DIR=/path/to/private-companion
python scripts/init_config.py                # writes the selected companion's machines.yaml

# 2. Edit and version the inventory in that private repository; keep secrets as *_ref pointers.

# 3. Put the real secret VALUES in your backend (env/op/vault/aws-ssm/file), then confirm:
python scripts/box_binder.py verify-config --json   # schema + pointer-only + no inline secrets
python scripts/box_binder.py doctor        --json   # per-host rclone/ssh/systemd probe; names gaps
```

## Switching between configs (hot-swap), E5

`machines.yaml` is self-contained: pointer-only secrets, and no machine-local absolute-path
coupling, so a config is swappable with no other change. Switch by repointing the env var, or pass `-c`:

```bash
export BOX_RCLONE_BINDER_CONFIG=/path/to/private-companion/fleet-prod.yaml     # config A
export BOX_RCLONE_BINDER_CONFIG=/path/to/private-companion/fleet-staging.yaml  # config B
# or, per invocation:
python scripts/box_binder.py healthcheck -c /path/to/private-companion/fleet-staging.yaml --json
```

Verify the swap: `verify-config` against each path, then flip `$BOX_RCLONE_BINDER_CONFIG` between
them, both must report a valid schema.
