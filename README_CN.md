# box-rclone-binder

在多台 Linux 服务器上部署 Box/rclone 运行环境，验证访问权限，并逐台报告凭据刷新结果。

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.2-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ 先读这里, 设计理念

把一个 Box 账号用 rclone 绑到多台服务器，看似是部署问题，其实是**鉴权模型**问题。Box 的 OAuth
`refresh_token` 是**单次使用 + 轮转**的：任一台刷新就作废其余各台的 token（`invalid_grant`）。
一个结构上不可共享的凭证，再精巧的脚本也救不回来。

所以本工具第一步是**直接消灭这个共享的轮转密钥**,改用 Box **服务端鉴权**（默认 JWT）：每台机持
同一份长期凭证、各自本地铸造短命 access token，天然多机一致、无可争抢。之后才在其上叠加幂等部署、
只读健康检查和逐台刷新结果。合成测试能验证确定性逻辑；真实授权、定时器触发、重启恢复和跨过
token 过期点后的续期，还需要独立的执行证据。

📜 **[完整设计理念 -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## 它是什么(不是什么)

- **是**：一个聚焦的 CLI（`box-binder`），负责 Linux/systemd 部署、显式刷新、定时 CCG mint 或
  broker 分发，以及只读访问验证。每台主机的结果分别报告。
- **不是**：单机小助手（用原生 `rclone config` 即可）、通用 cron 模板器、通用云同步工具。一事一职，
  三模块（deploy / refresh / healthcheck）。

## 安装

```
/plugin install github:DaizeDong/box-rclone-binder
```

或手动克隆:

```bash
git clone --recurse-submodules https://github.com/DaizeDong/box-rclone-binder.git ~/.claude/plugins/box-rclone-binder
```

## 快速开始

```bash
cd skills/box-rclone-binder
export BOX_RCLONE_BINDER_CONFIG=/path/to/private-companion/machines.yaml
python scripts/init_config.py --out "$BOX_RCLONE_BINDER_CONFIG"
python scripts/box_binder.py doctor        -c "$BOX_RCLONE_BINDER_CONFIG" --json   # 检查已安装工具和 SSH，不验证 Box 授权
python scripts/box_binder.py verify-config -c "$BOX_RCLONE_BINDER_CONFIG" --json   # schema + 禁内联密钥
python scripts/box_binder.py deploy        -c "$BOX_RCLONE_BINDER_CONFIG" --dry-run # 只规划，不动任何状态
python scripts/box_binder.py deploy        -c "$BOX_RCLONE_BINDER_CONFIG"          # 幂等铺到所有主机
python scripts/box_binder.py healthcheck   -c "$BOX_RCLONE_BINDER_CONFIG" --json   # 只读探活 + 一致性
python tests/run_gate.py                                             # 完整 mock 验收闸
```

## 配置

`plan-refresh` 只生成计划；`refresh` 才执行刷新并报告本次结果。部署成功表示文件和定时器
状态已经读回核对，不表示 Box 授权已经可用。执行阶段支持 env/file 密钥来源，其他后端需要
先导出。当前运行环境使用 systemd，不安装 cron，也不自动发送告警或在命令内重试。

`box-rclone-binder` 是**带 config 的 skill**, 它读取一份按机群组织的清单（`machines.yaml`：主机、
鉴权模式，以及指向密钥存放处的**指针**）。完整规范见
[CONFIG.md](skills/box-rclone-binder/CONFIG.md)。

- **挂载（发现顺序）:** `-c/--config <path>` → `$BOX_RCLONE_BINDER_CONFIG` →
  `$BOX_RCLONE_BINDER_CONFIG_DIR` → 私有伴生仓。
  选定的文件缺失时会报错；没有选定路径则返回 `EXIT_CONFIG (3)`
  并给出私有伴生仓的初始化指引。
- **首次配置:**
  ```bash
  cd skills/box-rclone-binder
  export BOX_RCLONE_BINDER_CONFIG_DIR=/path/to/private-companion  # 已初始化的私有 Git 仓库
  python scripts/init_config.py                       # 在伴生仓中从合成模板生成 machines.yaml
  # 改 hosts，密钥保持 *_ref 指针，然后:
  python scripts/box_binder.py verify-config --json   # doctor: schema + 仅指针 + 禁内联密钥
  ```
- **切换 config（即插即用）:** `machines.yaml` 自包含（仅指针、无硬编码路径）, 把环境变量指向另一份
  即可，或用 `-c`:
  `export BOX_RCLONE_BINDER_CONFIG=/path/to/private-companion/fleet-prod.yaml`，也可选择伴生仓中的另一份清单。
- **密钥:** Mode B，`machines.yaml`、`*.env`、`*.pem`、`*.key`、`rclone.conf` 均保存在公开仓之外。
  真实清单在私有伴生仓中做版本管理；清单里只放 `*_ref` 指针，真实值留在你的后端
  （`env`/`file`/`op`/`vault`/`aws-ssm`）。
  `verify-config` 对任何内联密钥硬失败。

## 如何触发

触发词：「用 rclone 把 Box 绑到多台服务器」「rclone Box 自动续期 / token 老过期」「让 Box 在我的
服务器上一直挂着」「多机 rclone Box 健康检查」。

## 示例输出

查看[生成器产生的合成健康报告](skills/box-rclone-binder/tests/fixtures/healthcheck.json)。
`healthy` 表示访问探针的结果。一致性比较使用远端 runtime 回传的配置；未观测字段列在
`unobserved_fields` 中。`consistent: null` 表示证据不足，即使访问成功，也不能据此认定配置
全部一致。当前 runtime 尚未测量远端 rclone 版本。

## 局限

- **Box 一次性授权是人工步骤**（登录 + Admin 批准），交接给用户；见
  `skills/box-rclone-binder/reference/runbook.md`。离线测试使用合成输入；真实 SSH 执行、定时器、
  重启恢复及跨过期点续期都需要另行验证。
- CCG-native 是否可用取决于 rclone 版本；`doctor` 只报告工具与能力，不能证明授权或跨过期点续期成功。
- oauth-broker（个人版 Box）无法严格永久无人值守（链断需重新浏览器授权）。

## 语言

中文 (`README_CN.md`) · English (`README.md`, 权威版)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE)(MIT)。
