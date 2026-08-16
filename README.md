# Shadow Platform

Shadow 系列项目的共享基础设施，当前包含六项能力：

- **Shadow Identity**：基于 Authelia 的统一登录、用户组和 OIDC 身份提供方。
- **Shadow Media**：统一图片上传、元数据、访问签名和存储适配协议。
- **Shadow LLM SDK**：统一 Base URL、模型别名、直连客户端和脱敏用量事件，不转发模型流量。
- **Shadow Agent Control Plane**：统一 Agent 身份、能力合同、跨项目路由与 Harness 装载，
  领域 Skill/Prompt 仍归各项目且调用不经过代理。
- **Shadow App Catalog**：统一应用所有权、入口、认证方式、健康检查和平台能力声明。
- **Shadow Telemetry**：只汇总脱敏 LLM 用量元数据，不采集提示词和回答。

本仓库只承载跨项目能力，不承载 Garden、Health、Foliant 或 Travel 的业务数据与业务权限。

## 目录

```text
auth/                 Authelia 配置与用户模板
agents/               Agent 身份注册表与领域 Capability Manifest 示例
catalog/              应用目录和跨能力声明
contracts/            跨项目稳定接口契约
deploy/nginx/         Nginx 认证与反代片段
docs/                 架构、安全、接入和迁移文档
llm/                  非敏感供应商与模型注册表
media_service/        Shadow Media 服务
telemetry_service/    脱敏 LLM 用量收集与聚合服务
scripts/              部署时配置渲染工具
shadow_sdk/           身份、媒体、LLM 直连和 Agent 验证 SDK
tests/                自动化测试
```

## 设计原则

1. 人类登录使用 OIDC；Agent、MCP 和定时任务使用独立服务凭据。
2. SSO 只负责身份与应用准入，业务资源权限由各应用自己管理。
3. 业务表关联内部 `shadow_user_id`，身份映射使用 `(issuer, subject)`。
4. 媒体中心只执行调用方已经完成的业务授权，不理解地图、餐次或文章权限。
5. 私密文件默认拒绝公开访问，访问地址短时有效。
6. 真实密钥只通过受限文件挂载，不进入 Git、Compose 文件或普通环境变量。
7. LLM SDK 在业务进程内直接请求供应商；平台统一配置与统计字段，不代理提示词和响应。
8. 用户可以面对一个统一人格，但运行时按项目使用独立 Agent principal 和最小权限凭据。
9. 业务 Skill、Prompt、工具和 evals 属于领域项目；Platform 只聚合合同与跨项目能力。

详细方案见 [架构文档](docs/architecture.md)、[统一 Agent 设计](docs/unified-agent.md)和
[安全边界](docs/security.md)。

## 快速验证

在 Windows 上通过 Git Bash 执行：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/platform_doctor.py
```

具体接入和部署方式见 `docs/` 与 `deploy/` 中的示例；仓库中的域名、账号和路径均为占位值。
