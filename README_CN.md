# box-rclone-binder

用 rclone 把一个 Box 网盘绑到多台服务器：自动续期、过期自愈、多机一致，仓库里零密钥。

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 先读这里 — 设计理念

把一个 Box 账号用 rclone 绑到多台服务器，看似是部署问题，其实是**鉴权模型**问题。Box 的 OAuth
`refresh_token` 是**单次使用 + 轮转**的：任一台刷新就作废其余各台的 token（`invalid_grant`）。
一个结构上不可共享的凭证，再精巧的脚本也救不回来。

所以本工具第一步是**直接消灭这个共享的轮转密钥**——改用 Box **服务端鉴权**（默认 JWT）：每台机持
同一份长期凭证、各自本地铸造短命 access token，天然多机一致、无可争抢。之后才在其上叠加幂等部署、
只读健康检查、有界自愈与告警。所有行为都能在**无真实 Box 凭证**下用 mock 验证——因为「看起来配好了」
不等于「真能用」。

📜 **[完整设计理念 -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## 它是什么(不是什么)

- **是**：一个聚焦的 CLI（`box-binder`），把一个 Box 网盘绑到多台服务器并无人值守地保活——幂等部署、
  自动续期/自愈、多机一致性校验、cron/systemd 计划、Discord 告警。
- **不是**：单机小助手（用原生 `rclone config` 即可）、通用 cron 模板器、通用云同步工具。一事一职，
  三模块（deploy / refresh / healthcheck）。

## 安装

```
/plugin install github:DaizeDong/box-rclone-binder
```

或手动克隆:

```bash
git clone https://github.com/DaizeDong/box-rclone-binder.git ~/.claude/plugins/box-rclone-binder
```

## 快速开始

```bash
cd skills/box-rclone-binder
cp config/machines.example.yaml machines.yaml      # 改 hosts；密钥保持 *_ref 指针，不写值
python scripts/box_binder.py doctor        -c machines.yaml --json   # 探 rclone/ssh/systemd/CCG
python scripts/box_binder.py verify-config -c machines.yaml --json   # schema + 禁内联密钥
python scripts/box_binder.py deploy        -c machines.yaml --dry-run # 只规划，不动任何状态
python scripts/box_binder.py deploy        -c machines.yaml          # 幂等铺到所有主机
python scripts/box_binder.py healthcheck   -c machines.yaml --json   # 只读探活 + 一致性
python tests/run_gate.py                                             # 完整 mock 验收闸
```

## 如何触发

触发词：「用 rclone 把 Box 绑到多台服务器」「rclone Box 自动续期 / token 老过期」「让 Box 在我的
服务器上一直挂着」「多机 rclone Box 健康检查」。

## 示例输出

`healthcheck --json`（节选）:

```json
{"command":"healthcheck","hosts":[{"host":"203.0.113.10","healthy":true,"category":"ok",
 "auth_mode":"jwt","root_folder_id":"0","has_refresh_token":false}],
 "consistency":{"consistent":true,"divergences":[]},
 "refresh_token_invariant":{"ok":true,"holders":[]},"exit_code":0}
```

## 局限

- **Box 一次性授权是人工步骤**（登录 + Admin 批准），交接给用户；见
  `skills/box-rclone-binder/reference/runbook.md`。在此之前逻辑已全量 mock 验证，但真实端到端
  `rclone lsd box:` 冒烟测试无法运行。
- CCG-native 是否原生支持取决于 rclone 版本；由 `doctor` 决定 native 还是 mint。
- oauth-broker（个人版 Box）无法严格永久无人值守（链断需重新浏览器授权）。

## 语言

中文 (`README_CN.md`) · English (`README.md`, 权威版)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE)(MIT)。
