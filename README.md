# Shadow Platform

Shadow Platform 是 Shadow 系列的共享基础设施层。它统一身份、应用目录、文件资产、通知、模型
配置和 Agent 访问合同，但不承载 Health、Travel、Ledger 等领域项目的业务事实。

## 理念

- Platform 只管理跨项目真相，领域语义始终留在业务项目；
- 人类登录、服务身份和 Agent 身份分离；
- 真实文件、业务引用和衍生关系分层；
- 密钥只从受限文件读取，真实部署拓扑不进入仓库；
- 共享能力提供稳定合同，不成为所有请求的流量代理。

## 主要功能

- Authelia OIDC 身份与应用准入组；
- App Catalog 和跨项目接入声明；
- Asset/Media 文件、去重、版本、引用和生命周期；
- Notifications 收件箱、TG/QQ/飞书投递与运维状态；
- LLM 配置 SDK 和脱敏用量元数据；
- Shadow Plugin 合同、Agent Profile、DSH Bundle 构建与最小权限验证；
- Shadow SDK、接口合同和配置自检工具。

## 本地验证

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/platform_doctor.py
```

## 文档

- [架构概览](docs/architecture.md)
- [应用接入](docs/app-integration.md)
- [Asset v1](docs/asset-service-v1.md)
- [通知与收件箱](docs/notifications.md)
- [统一 Agent](docs/unified-agent.md)
- [Shadow Plugin Specification](docs/shadow-plugin-spec.md)
- [DeepSeek Harness 接入](docs/dsh-integration.md)
- [领域项目插件化接入清单](docs/domain-plugin-onboarding.md)
- [安全边界](docs/security.md)
