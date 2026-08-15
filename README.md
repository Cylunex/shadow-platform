# Shadow Platform

Shadow 系列项目的共享基础设施，当前包含四项能力：

- **Shadow Identity**：基于 Authelia 的统一登录、用户组和 OIDC 身份提供方。
- **Shadow Media**：统一图片上传、元数据、访问签名和存储适配协议。
- **Shadow LLM SDK**：统一 Base URL、模型别名、直连客户端和脱敏用量事件，不转发模型流量。
- **Shadow Agent Access**：统一 Agent registry、audience、scope 和本地凭据验证，不做代理。

本仓库只承载跨项目能力，不承载 Garden、Health、Foliant 或 Travel 的业务数据与业务权限。

## 当前阶段

第一阶段建立架构、接口契约和本地可验证实现，不会自动修改线上 DNS、证书、Nginx 或现有应用。

目标部署地址：

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| Identity | `https://auth.cylunex.top` | 登录、二次验证、OIDC |
| Media API | `https://media.cylunex.top` | 服务端申请上传和访问地址 |
| NAS | `https://nas.cylunex.top:55080` | 局域网直连入口，后续启用 |

Travel 的正式入口仍为 `https://cylunex.top/travel/`；`travel.cylunex.top` 只做 308 跳转。

## 目录

```text
auth/                 Authelia 配置与用户模板
agents/               Agent 身份、audience 与 scope 注册表
contracts/            跨项目稳定接口契约
deploy/nginx/         Nginx 认证与反代片段
docs/                 架构、安全、接入和迁移文档
llm/                  非敏感供应商与模型注册表
media_service/        Shadow Media 服务
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

详细方案见 [架构文档](docs/architecture.md) 和 [安全边界](docs/security.md)。

## 本地验证 Shadow Media

在 Windows 上通过 Git Bash 执行：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[dev]'
cp .env.example .env
cp secrets/media-service-tokens.json.example secrets/media-service-tokens.json
```

另创建 `secrets/media-access-signing-key`，写入至少 32 字节的随机值，然后启动：

```bash
.venv/Scripts/python.exe -m uvicorn media_service.app:app \
  --host 127.0.0.1 --port 8400 --env-file .env
```

验证：

```bash
curl http://127.0.0.1:8400/healthz
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest -q
```

当前实现状态和上线前缺口见 [Shadow Media 实现状态](docs/media-status.md)。

LLM 直连配置方法见 [LLM 统一配置](docs/llm-config.md)。
Agent 直连接入见 [Agent 统一接入](docs/agent-access.md)。
