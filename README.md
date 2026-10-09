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

## Scope

The `box-binder` CLI maintains Linux/systemd deployment, explicit refresh,
scheduled CCG mint or broker distribution, and read-only access validation through
three modules: deploy, refresh and healthcheck. Use plain `rclone config` for a
single host. Generic scheduling templates and general cloud synchronization are
outside this tool's scope.

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

The tool reads `machines.yaml` with hosts, authentication modes and `*_ref` secret
references. Keep real inventories versioned in a PRIVATE companion, with one
`machines.yaml` per retained profile. Secret values stay in the configured backend;
inline secrets are rejected. Execution resolves env/file sources; export values
from `op`, `vault` or `aws-ssm` to one of those sources first.

Selection starts with `-c/--config`, then `BOX_RCLONE_BINDER_CONFIG`,
`BOX_RCLONE_BINDER_CONFIG_DIR` and shared companion discovery. A selected missing
file remains an error; no selection returns `EXIT_CONFIG (3)` with setup guidance.
[CONFIG.md](skills/box-rclone-binder/CONFIG.md) defines the full discovery order,
generated-template initialization, schema, secret references and profile switching.
The local `scripts/verify_config.py --json` also checks required references and
reports NOT READY when they are unavailable. `box-binder verify-config` checks schema.

For this retired tool, initialize an inventory only for an existing deployment or
retained recovery need. See [DATA.md](DATA.md) for storage and retirement.

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
  [the runbook](skills/box-rclone-binder/reference/runbook.md). Offline checks use synthetic inputs. Authorization,
  actual SSH execution, timers, restarts and renewal across expiry require separate live acceptance.
- CCG-native support is rclone-version dependent; `doctor` reports installed tools but does not
  test Box authorization or renewal across an expiry boundary.
- oauth-broker (personal Box) cannot be strictly unattended forever (a broken chain needs re-auth).

## Languages

English (`README.md`, authoritative) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE) (MIT).
