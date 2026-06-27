# box-rclone-binder

Bind one Box drive to many servers via rclone — auto-refresh, self-heal, multi-host consistency. Zero secrets in the repo.

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.1-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ Read this first — the design philosophy

Binding one Box account to several servers with rclone looks like a deploy problem. It is not. It
is an **auth-model** problem. Box's OAuth `refresh_token` is **single-use and rotating**: the first
host that refreshes invalidates every other host's token (`invalid_grant`). No amount of careful
scripting fixes a credential that is structurally unshareable.

So box-rclone-binder's first move is to **delete the shared rotating secret** by switching to Box
**server auth** (JWT by default): each host holds the same long-term credential and mints its own
short-lived access token locally — naturally consistent, nothing to fight over. Only *then* does the
tooling layer on idempotent deploy, read-only health checks, bounded self-heal, and alerts. Every
behaviour is provable under mocks with **no real Box credentials**, because "it looks configured" is
not "it works".

📜 **[Read the full design philosophy -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## What it is (and isn't)

- **Is:** a focused CLI (`box-binder`) that binds ONE Box drive to MANY servers via rclone and keeps
  it alive unattended — idempotent deploy, auto-refresh/self-heal, multi-host consistency checks,
  cron/systemd scheduling, Discord alerts.
- **Isn't:** a single-machine helper (use plain `rclone config`), a generic cron templater, or a
  general cloud sync tool. One job, three modules (deploy / refresh / healthcheck).

## Install

```
/plugin install github:DaizeDong/box-rclone-binder
```

Or clone manually:

```bash
git clone https://github.com/DaizeDong/box-rclone-binder.git ~/.claude/plugins/box-rclone-binder
```

## Quick start

```bash
cd skills/box-rclone-binder
cp config/machines.example.yaml machines.yaml      # edit hosts; secrets stay as *_ref pointers
python scripts/box_binder.py doctor        -c machines.yaml --json   # probe rclone/ssh/systemd/CCG
python scripts/box_binder.py verify-config -c machines.yaml --json   # schema + no inline secrets
python scripts/box_binder.py deploy        -c machines.yaml --dry-run # plan, touches nothing
python scripts/box_binder.py deploy        -c machines.yaml          # converge all hosts
python scripts/box_binder.py healthcheck   -c machines.yaml --json   # read-only probe + consistency
python tests/run_gate.py                                             # full mock acceptance gate
```

## Config

`box-rclone-binder` is **config-bearing** — it reads a per-fleet inventory (`machines.yaml`: hosts,
auth mode, and **pointers** to where secrets live). Full contract:
[CONFIG.md](skills/box-rclone-binder/CONFIG.md).

- **Mount (discovery order):** `-c/--config <path>` → `$BOX_RCLONE_BINDER_CONFIG` →
  `$BOX_RCLONE_BINDER_CONFIG_DIR` → `./machines.yaml` → `~/.box-rclone-binder-config/machines.yaml`
  → `~/.config/box-rclone-binder/machines.yaml`. First that resolves wins; none = `EXIT_CONFIG (3)`
  naming the path it looked for.
- **First time:**
  ```bash
  cd skills/box-rclone-binder
  python scripts/init_config.py                       # stamp machines.yaml from the template (deterministic)
  export BOX_RCLONE_BINDER_CONFIG=~/.box-rclone-binder-config/machines.yaml  # or pass --out / -c
  # edit hosts, keep secrets as *_ref pointers, then:
  python scripts/box_binder.py verify-config --json   # doctor: schema + pointer-only + no inline secrets
  ```
- **Switch configs (hot-swap):** `machines.yaml` is self-contained (pointer-only, no hardcoded
  paths) — repoint the env var or pass `-c`:
  `export BOX_RCLONE_BINDER_CONFIG=~/configs/fleet-prod.yaml` ↔ `~/configs/fleet-staging.yaml`.
- **Secrets:** Mode B — `machines.yaml`, `*.env`, `*.pem`, `*.key`, `rclone.conf` are gitignored and
  never enter git; only `*_ref` pointers live in the inventory, real values stay in your backend
  (`env`/`file`/`op`/`vault`/`aws-ssm`). `verify-config` hard-fails on any inline secret.

## How to invoke

Trigger phrases: "bind my Box drive to multiple servers with rclone", "rclone Box auto-refresh /
token keeps expiring", "keep Box mounted across my servers", "multi-host rclone Box health check".

## Example output

`healthcheck --json` (abridged):

```json
{"command":"healthcheck","hosts":[{"host":"203.0.113.10","healthy":true,"category":"ok",
 "auth_mode":"jwt","root_folder_id":"0","has_refresh_token":false}],
 "consistency":{"consistent":true,"divergences":[]},
 "refresh_token_invariant":{"ok":true,"holders":[]},"exit_code":0}
```

## Limitations

- **One-time Box authorization is a human step** (login + Admin approve), deferred to the user; see
  `skills/box-rclone-binder/reference/runbook.md`. Until then the logic is fully tested under mocks
  but the real end-to-end `rclone lsd box:` smoke test cannot run.
- CCG-native support is rclone-version dependent; `doctor` decides native vs mint.
- oauth-broker (personal Box) cannot be strictly unattended forever (a broken chain needs re-auth).

## Languages

English (`README.md`, authoritative) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE) (MIT).
