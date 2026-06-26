# box-rclone-binder — Config

`box-rclone-binder` is **config-bearing**: it reads a per-fleet machine inventory (`machines.yaml`)
that lists your hosts, auth mode, and **pointers** to where secrets live. This file is the
authoritative config contract (config-spec E1).

Two artifacts make up the config:

| Artifact | Committed? | Role |
|---|---|---|
| `config/machines.example.yaml` | yes (commit-safe) | the template you stamp from; ZERO secret values |
| `machines.yaml` | **no — gitignored** | your live inventory; pointer-only secrets, safe to keep local |
| `config/secrets.env.example` | yes (commit-safe) | example of the *.env you keep OUTSIDE the repo |
| `*.env` / `*.pem` / `*.key` / `rclone.conf` | **no — gitignored** | the real secret material (Mode B) |

The cardinal invariant: **`machines.yaml` never holds a secret value, only a pointer.**
`box-binder verify-config` hard-fails (and `config.py` refuses to load) if a literal secret appears.

## Discovery convention (how the skill finds your config) — E2

`box-binder` resolves the `machines.yaml` path in this order; the first that resolves wins:

1. `-c/--config <path>` — explicit flag (a file, or a dir holding `machines.yaml`).
2. `$BOX_RCLONE_BINDER_CONFIG` — env var; a file path, or a dir holding `machines.yaml` (recommended; location-independent).
3. `$BOX_RCLONE_BINDER_CONFIG_DIR` — accepted alias; a dir holding `machines.yaml`.
4. `./machines.yaml` — cwd-relative (the historical default).
5. `~/.box-rclone-binder-config/machines.yaml` — dotfile-in-home fallback (= `init_config.py` default).
6. `~/.config/box-rclone-binder/machines.yaml` — XDG-style fallback (Linux/macOS).

If none resolves, the command exits `EXIT_CONFIG (3)` naming the path it looked for — it never
crashes opaquely.

## Schema — `machines.yaml` (E1)

```yaml
version: 1
defaults: { ... }     # per-host fallbacks (any key here is overridable per host)
secrets:  { ... }     # source + pointer refs only
hosts:    [ ... ]     # >= 1 host; each may override any defaults key
alerts:   { ... }     # optional
```

### `version` (a.k.a. `schema_version`)

The top-level `version` field is the config **`schema_version`** — it pins which `machines.yaml`
schema the loader should expect. The loader accepts `schema_version` as an alias for `version`.

| Field | Type | Required | Default | Example |
|---|---|---|---|---|
| `version` (alias `schema_version`) | int | recommended | `1` | `1` |

### `defaults` (mapping — per-host fallbacks)

| Field | Type | Required | Default | Example / allowed |
|---|---|---|---|---|
| `auth_mode` | enum | no | `jwt` | `jwt` \| `ccg-native` \| `ccg-mint` \| `oauth-broker` |
| `box_sub_type` | enum | no | `enterprise` | `enterprise` \| `user` |
| `remote_name` | str | **yes** (here or per host) | `box` | `box` |
| `root_folder_id` | str | no | `"0"` | `"0"` (service-account root) |
| `rclone_min_version` | str | no | — | `"1.71.0"` |
| `config_dir` | str (remote path) | no | `/etc/box-binder` | `/etc/box-binder` |
| `health_interval` | str (systemd OnCalendar) | no | — | `"*:0/15"` (every 15 min) |
| `mint_interval_min` | int | no (ccg-mint only) | `45` | `45` (re-mint before 60-min expiry) |
| `impersonate_user_id` | str | no | `""` | `"1234567"` (as-user) |

### `secrets` (mapping — WHERE secrets live, never the value)

| Field | Type | Required | Allowed / shape |
|---|---|---|---|
| `source` | enum | no (default `env`) | `env` \| `file` \| `op` \| `vault` \| `aws-ssm` |
| `jwt_config_ref` | pointer | for `jwt` | see pointer shape below |
| `client_id_ref` | pointer | for `ccg-*` | pointer |
| `client_secret_ref` | pointer | for `ccg-*` | pointer |
| `box_subject_id_ref` | pointer | for `ccg-*` | pointer |
| `rclone_config_pass_ref` | pointer | only for encrypted-conf fallback | pointer |

**Pointer shape (enforced):** an env var name (`UPPER_SNAKE`), `op://…`, `vault://…`,
`aws-ssm://…`, an absolute path (`/…`), a `<placeholder>`, or empty. **A literal secret is
rejected.** Example: `client_id_ref: BOX_BINDER_CLIENT_ID` (the value lives in `$BOX_BINDER_CLIENT_ID`).

### `hosts[]` (list — REQUIRED, ≥ 1)

| Field | Type | Required | Example |
|---|---|---|---|
| `host` | str | **yes** | `203.0.113.10` |
| `ssh` | str | no | `root@203.0.113.10` |
| `ssh_opts` | str | no | `-o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new` |
| *any `defaults` key* | per-type | no | per-host override, e.g. `root_folder_id: "987654"` |

### `alerts` (mapping — optional)

| Field | Type | Default | Example / allowed |
|---|---|---|---|
| `discord` | bool | `false` | `true` |
| `relay` | path | — | `the notifier` |
| `on_recovered` | level | — | `INFO` |
| `on_heal_failed` | level | — | `CRITICAL` |
| `on_drift` | level | — | `WARN` |
| `jitter_sec` | int | — | `30` (stagger probes to dodge 429) |

**Validation (`box-binder verify-config`):** `hosts` non-empty; each host has `host` and a resolved
`remote_name`; `auth_mode` in the enum; `secrets.source` in the enum; every `*_ref` is a pointer
(not a literal); no inline secret anywhere in the file.

## Secrets — Mode B (E6)

Secrets are **out-of-band**, never in git. The live `machines.yaml` carries only `*_ref` pointers;
the real values live in your backend (`env`/`file`/`op`/`vault`/`aws-ssm`). The repo `.gitignore`
blocks `machines.yaml`, `*.env`, `secrets.env`, `*.pem`, `*.key`, `*.p12`, `rclone.conf`,
`credentials*`, `*.token`, and state files. `config.py` additionally scans for inline key/JWT/blob
material and refuses to load if any is found. The skill never echoes a secret value (only presence).

## First-time setup (E3) — succeeds on the first try

```bash
cd skills/box-rclone-binder

# 1. Stamp a conformant machines.yaml from the commit-safe template (deterministic — E4):
python scripts/init_config.py                 # -> ~/.box-rclone-binder-config/machines.yaml
#   or:  python scripts/init_config.py --out ./machines.yaml

# 2. Edit it: set your hosts; keep secrets as *_ref pointers. Then point the skill at it
#    (skip if you stamped to a default discovery path):
export BOX_RCLONE_BINDER_CONFIG=~/.box-rclone-binder-config/machines.yaml

# 3. Put the real secret VALUES in your backend (env/op/vault/aws-ssm/file), then confirm:
python scripts/box_binder.py verify-config --json   # schema + pointer-only + no inline secrets
python scripts/box_binder.py doctor        --json   # per-host rclone/ssh/systemd probe; names gaps
```

## Switching between configs (hot-swap) — E5

`machines.yaml` is self-contained — pointer-only secrets, no machine-local absolute-path coupling —
so a config is swappable with no other change. Switch by repointing the env var, or pass `-c`:

```bash
export BOX_RCLONE_BINDER_CONFIG=~/configs/fleet-prod.yaml     # config A
export BOX_RCLONE_BINDER_CONFIG=~/configs/fleet-staging.yaml  # config B — same skill, different fleet
# or, per invocation:
python scripts/box_binder.py healthcheck -c ~/configs/fleet-staging.yaml --json
```

Verify the swap: `verify-config` against each path, then flip `$BOX_RCLONE_BINDER_CONFIG` between
them — both must report a valid schema.
