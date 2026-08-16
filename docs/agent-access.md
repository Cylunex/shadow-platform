# Agent 统一接入（无网关）

## 原则

Agent 的身份、audience、scope 和凭据格式统一，但调用仍然直接进入所属项目的 API 或 MCP 服务：

```text
Agent -> Health / Foliant / Travel -> 项目业务权限
```

平台不转发 Agent 请求，也不集中承载 Foliant 的工具、Health 的写入逻辑或 Travel 的规划流程。

Catalog 中纯后台服务使用 `auth.mode: service-bearer`。该值表示浏览器 Cookie 和代理身份头
均无效；真正可调用的身份仍以本页 Agent registry 的 audience、scopes 与凭据摘要为准。

## Registry

`agents/registry.yml` 登记：

- 稳定 `agent_id`；
- 负责人项目 `owner_app`；
- 可以调用的应用 `audiences`；
- 最小权限 scopes；
- 一至两个 Token SHA-256 文件，用于无停机轮换；
- 是否禁用。

Registry 不保存原始 Token。使用仓库工具生成高熵 Token，并在 secrets 目录只保存 SHA-256：

```bash
.venv/Scripts/python.exe scripts/generate_agent_token.py \
  --digest-output /etc/shadow-platform/secrets/agents/health-assistant/current-token.sha256
```

工具只显示一次原始 Token，交给对应 Agent；命令行和摘要文件都不包含原始值。没有 `--force` 时不会覆盖已有摘要。

原始 Token 只交给 Agent，摘要文件放在：

```text
/etc/shadow-platform/secrets/agents/<agent-id>/current-token.sha256
```

## 项目验证

每个项目启动时构造本地验证器：

```python
from shadow_sdk.agent import AgentAuthenticator

authenticator = AgentAuthenticator(
    "/etc/shadow-platform/agents/registry.yml",
    secrets_dir="/etc/shadow-platform/secrets",
    audience="health",
)
identity = authenticator.authenticate(request.headers.get("Authorization", ""))
identity.require_scope("health.write")
```

验证过程只做本地 SHA-256 和常量时间比较，没有中央网络请求。项目仍需检查幂等键、资源权限和写入审计。

## 审计字段

Agent 写操作至少记录：

```text
agent_id
owner_app
audience
scope
request_id
idempotency_key
result
created_at
```

Agent 如果代表某个用户执行操作，不能直接相信 Agent 自报的 `actor_sub`。用户委托令牌的签发和验证另行设计；未实现前只允许明确的服务身份权限。
