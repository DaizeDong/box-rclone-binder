# box-rclone-binder

在多台 Linux 服务器上部署 Box/rclone 运行环境，验证访问权限，并逐台报告凭据刷新结果。

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.2-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

**维护状态：** 已停止主动开发。下列命令供维护已有安装时参考；原扩展计划保持搁置，不承诺交付。

---

## 设计理念

Box OAuth 的刷新令牌用过后就会轮换。多台主机共用这份令牌时，一台完成刷新，其余主机的凭据
就可能失效。默认的 JWT 方案让每台主机使用长期服务端凭据，独立取得短期访问令牌，从而消除
共用刷新令牌的竞争。代价是需要先配置 Box 应用并取得管理员授权。个人账号若使用 broker 路径，
仍需协调刷新，并保留人工重新授权的流程。

部署按声明收敛文件和 systemd 定时器；健康检查分别报告已观测的访问结果和未知字段。
合成检查可以在没有真实凭据时验证这些约定，但不能证明 Box 授权、定时器触发或跨过期点续期成功。
把这些结果分开，才能避免将“配置已写好”误报为“部署已验收”。

[完整设计理念](PHILOSOPHY.md)。

## 适用范围

`box-binder` CLI 维护 Linux/systemd 部署、显式刷新、定时 CCG mint 或 broker
分发，并通过 deploy、refresh、healthcheck 三个模块报告逐台主机的访问结果。
单台主机可直接使用 `rclone config`。通用调度模板和云同步不在本工具范围内。

## 安装

```
/plugin install github:DaizeDong/box-rclone-binder
```

或手动克隆：

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

工具读取 `machines.yaml` 中的主机、鉴权模式和 `*_ref` 密钥引用。真实清单在 PRIVATE
伴生仓中做版本管理，每套保留配置使用独立伴生仓及固定文件名 `machines.yaml`。
密钥值留在所选后端，清单内的明文密钥会被拒绝。执行阶段支持 env/file；
`op`、`vault`、`aws-ssm` 的值需先导出到这两种来源之一。

查找顺序从 `-c/--config` 开始，随后是 `BOX_RCLONE_BINDER_CONFIG`、
`BOX_RCLONE_BINDER_CONFIG_DIR` 和共享伴生仓发现。选定文件缺失时仍报错；没有选定路径
时返回 `EXIT_CONFIG (3)` 并给出设置指引。[CONFIG.md](skills/box-rclone-binder/CONFIG.md)
规定完整发现顺序、合成模板初始化、字段、密钥引用和配置切换。
本地 `scripts/verify_config.py --json` 还会检查必需引用，缺失时返回 NOT READY；
`box-binder verify-config` 只检查结构。

本工具已停止主动开发，只有已有部署或保留的恢复需求才需要初始化清单。
存储和退役规则见 [DATA.md](DATA.md)。

## 如何触发

触发词：「用 rclone 把 Box 绑到多台服务器」「rclone Box 自动续期 / token 老过期」「让 Box 在我的
服务器上一直挂着」「多机 rclone Box 健康检查」。

## 示例输出

查看[生成器产生的合成健康报告](skills/box-rclone-binder/tests/fixtures/healthcheck.json)。
`healthy` 表示访问探针的结果。一致性比较使用远端 runtime 回传的配置；未观测字段列在
`unobserved_fields` 中。`consistent: null` 表示证据不足，即使访问成功，也不能据此认定配置
全部一致。当前 runtime 尚未测量远端 rclone 版本。

## 局限

`plan-refresh` 只生成计划；`refresh` 执行刷新并报告本次结果。部署成功表示文件和定时器
状态已经读回核对，不表示 Box 授权已经可用。当前运行环境使用 systemd，不安装 cron，
不自动发送告警，也不在命令内重试。具体行为见[部署](skills/box-rclone-binder/reference/deploy.md)
和[刷新](skills/box-rclone-binder/reference/refresh-healthcheck.md)规范。

- **Box 一次性授权是人工步骤**（登录 + Admin 批准），交接给用户；见
  [运行手册](skills/box-rclone-binder/reference/runbook.md)。离线测试使用合成输入；真实 SSH 执行、定时器、
  重启恢复及跨过期点续期都需要另行验证。
- CCG-native 是否可用取决于 rclone 版本；`doctor` 只报告工具与能力，不能证明授权或跨过期点续期成功。
- oauth-broker（个人版 Box）无法严格永久无人值守（链断需重新浏览器授权）。

## 语言

中文 (`README_CN.md`) · English (`README.md`, 权威版)

## Roadmap · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE)(MIT)。
