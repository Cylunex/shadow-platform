# Agent 统一接入（无网关）

## 原则

Agent 的身份、audience、scope 和凭据格式统一，但调用仍然直接进入所属项目的 API 或 MCP 服务：

```text
Agent -> Health / Foliant / Travel -> 项目业务权限
```

平台不转发 Agent 请求，也不集中承载 Foliant 的工具、Health 的写入逻辑或 Travel 的规划流程。

领域 Skill、Prompt 和 evals 也不进入 Platform。各项目通过 `agent/manifest.yaml` 发布稳定
Capability，Platform 只负责校验、路由和部署时聚合。完整设计见 `docs/unified-agent.md`。

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
  --digest-output "$SHADOW_PLATFORM_SECRETS_DIR/agents/health-assistant/current-token.sha256"
```

工具只显示一次原始 Token，交给对应 Agent；命令行和摘要文件都不包含原始值。没有 `--force` 时不会覆盖已有摘要。

原始 Token 只交给 Agent，摘要文件放在：

`$SHADOW_PLATFORM_SECRETS_DIR/agents/<agent-id>/current-token.sha256`。实际 secrets 根目录由
仓库外运维配置注入。

用户可见的全局人格不是 Agent principal。统一 Harness 应按目标项目持有多份最小权限凭据，
不要登记一个横跨所有 audience 的万能 Token。

## 项目验证

每个项目启动时构造本地验证器：

```python
import os

from shadow_sdk.agent import AgentAuthenticator

authenticator = AgentAuthenticator(
    os.environ["SHADOW_AGENT_REGISTRY_FILE"],
    secrets_dir=os.environ["SHADOW_PLATFORM_SECRETS_DIR"],
    audience="health",
)
identity = authenticator.authenticate(request.headers.get("Authorization", ""))
identity.require_scope("health.write")
```

验证过程只做本地 SHA-256 和常量时间比较，没有中央网络请求。项目仍需检查幂等键、资源权限和写入审计。

Travel 已验证 scope 之外还需要资源级授权：即使 Token 具备 `travel.maps.read`，数据库仍应
确认该 `agent_id` 获得目标地图的读取许可。Foliant 已验证路由应显式分类且默认拒绝；新增
机器路由不能仅因为路径位于 `/api/machine/` 就自动获得某个 scope。

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

## Capability Manifest

每个领域项目在自己的仓库维护 `agent/manifest.yaml`，schema 为
`contracts/agent-capability-manifest.schema.json`。Manifest 描述的是能力而不是凭据，至少应
明确 audience、scopes、工具、读写效果、数据敏感度、用户确认、资源授权和幂等要求。

Platform 中的 `agents/capability-manifest.yml.example` 展示了 Travel 的只读地图和草案能力。
业务项目发布前必须验证：

- Skill 引用的 capability 全部存在；
- capability 的 audience 已在 App Catalog 开启 Agent；
- Registry 中至少有一个预期 principal 具备所需 audience/scopes；
- `write` / `delete` 不允许无确认且必须幂等；
- `draft` 只能写草案资源，应用正式数据仍由确定性确认接口修改。
