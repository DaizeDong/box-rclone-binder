# box-rclone-binder

Deploy a Box/rclone runtime across Linux servers, validate access, and refresh credentials with per-host results.

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.2-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

**Maintenance status:** retired from active development. The commands below are retained for
maintaining existing installations; the former expansion plans are deferred without a delivery commitment.

---

## Design Philosophy

Box OAuth refresh tokens rotate after use. Sharing the same rotating token across hosts makes
their credentials diverge as soon as one host refreshes. The default JWT design gives each host
the long-term server credential needed to mint its own short-lived access token. This removes
that shared-token race, at the cost of Box application setup and administrative authorization.
Personal accounts that need the broker path still require coordinated refresh and possible reauthorization.

Deployment then converges declared files and systemd timers, while health checks report observed
access and unknown measurements separately. Synthetic checks can validate these contracts without
credentials. They cannot establish Box authorization, timer firing or renewal across token expiry.
Keeping those outcomes separate prevents a configured host from being reported as an accepted deployment.

[Read the full design philosophy](PHILOSOPHY.md).

## What it is (and isn't)

- **Is:** a focused CLI (`box-binder`) for checked Linux/systemd deployment, explicit refresh,
  scheduled CCG mint or broker distribution, and read-only access validation.
- **Isn't:** a single-machine helper (use plain `rclone config`), a generic cron templater, or a
  general cloud sync tool. One job, three modules (deploy / refresh / healthcheck).

## Install

```
/plugin install github:DaizeDong/box-rclone-binder
```

Or clone manually:

```bash
git clone --recurse-submodules https://github.com/DaizeDong/box-rclone-binder.git ~/.claude/plugins/box-rclone-binder
```

## Quick start

```bash
cd skills/box-rclone-binder
export BOX_RCLONE_BINDER_CONFIG=/path/to/private-companion/machines.yaml
python scripts/init_config.py --out "$BOX_RCLONE_BINDER_CONFIG"
python scripts/box_binder.py doctor        -c "$BOX_RCLONE_BINDER_CONFIG" --json   # discover installed tools and SSH; no Box authorization test
python scripts/box_binder.py verify-config -c "$BOX_RCLONE_BINDER_CONFIG" --json   # schema + no inline secrets
python scripts/box_binder.py deploy        -c "$BOX_RCLONE_BINDER_CONFIG" --dry-run # plan, touches nothing
python scripts/box_binder.py deploy        -c "$BOX_RCLONE_BINDER_CONFIG"          # converge all hosts
python scripts/box_binder.py healthcheck   -c "$BOX_RCLONE_BINDER_CONFIG" --json   # read-only probe + consistency
python tests/run_gate.py                                             # full mock acceptance gate
```

## Config

`box-rclone-binder` is **config-bearing**, it reads a per-fleet inventory (`machines.yaml`: hosts,
auth mode, and **pointers** to where secrets live). Full contract:
[CONFIG.md](skills/box-rclone-binder/CONFIG.md).

- **Mount (discovery order):** `-c/--config <path>` → `$BOX_RCLONE_BINDER_CONFIG` →
  `$BOX_RCLONE_BINDER_CONFIG_DIR` → the private companion repository.
  The selected path is retained even if missing; no selection = `EXIT_CONFIG (3)`
  with private companion setup guidance.
- **First time:**
  ```bash
  cd skills/box-rclone-binder
  export BOX_RCLONE_BINDER_CONFIG_DIR=/path/to/private-companion  # an initialized private Git repository
  python scripts/init_config.py                       # stamp its machines.yaml from the generated template
  # edit hosts, keep secrets as *_ref pointers, then:
  python scripts/verify_config.py --json              # local schema and required-reference readiness
  ```
- **Switch retained profiles:** select separate PRIVATE companions, each containing exactly
  `machines.yaml`, for example `export BOX_RCLONE_BINDER_CONFIG=/path/to/private-profile-b/machines.yaml`.
  The local verifier reports NOT READY when a required reference is missing. See the full
  [discovery and switching contract](skills/box-rclone-binder/CONFIG.md).
- **Secrets:** Mode B, `machines.yaml`, `*.env`, `*.pem`, `*.key`, `rclone.conf` stay outside the
  public repository. Version real inventories in the private companion; only `*_ref` pointers
  live in the inventory, while secret values stay in your backend
  (`env`/`file`/`op`/`vault`/`aws-ssm`). `verify-config` hard-fails on any inline secret.

## How to invoke

Trigger phrases: "bind my Box drive to multiple servers with rclone", "rclone Box auto-refresh /
token keeps expiring", "keep Box mounted across my servers", "multi-host rclone Box health check".

## Example output

See the [generated synthetic health report](skills/box-rclone-binder/tests/fixtures/healthcheck.json).
`healthy` describes the access probe. Consistency uses the runtime's reported settings; missing
measurements appear in `unobserved_fields`. A null consistency result remains unknown, even when
access succeeds. The runtime currently leaves the installed rclone version unobserved.

## Limitations

`plan-refresh` only plans; `refresh` executes and reports current per-host outcomes. Deployment
success establishes configured files and enabled/active timers. It does not establish working
Box authorization. Read the [deployment contract](skills/box-rclone-binder/reference/deploy.md)
and [refresh contract](skills/box-rclone-binder/reference/refresh-healthcheck.md) for exact behavior.
Execution resolves env/file secret sources; other providers need an external export step.
Cron installation, automatic alerts, and in-command retry/backoff are outside this runtime.

- **One-time Box authorization is a human step** (login + Admin approve), deferred to the user; see
  `skills/box-rclone-binder/reference/runbook.md`. Offline checks use synthetic inputs. Authorization,
  actual SSH execution, timers, restarts and renewal across expiry require separate live acceptance.
- CCG-native support is rclone-version dependent; `doctor` reports installed tools but does not
  test Box authorization or renewal across an expiry boundary.
- oauth-broker (personal Box) cannot be strictly unattended forever (a broken chain needs re-auth).

## Languages

English (`README.md`, authoritative) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE) (MIT).
