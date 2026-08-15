# Identity contract v1

## OIDC provider

```text
issuer: https://auth.cylunex.top
discovery: https://auth.cylunex.top/.well-known/openid-configuration
flow: authorization_code
pkce: S256 required for browser and mobile-facing clients
```

每个应用有独立 client ID、client secret 和回调地址。不得共享 client secret，也不得登记通配符回调地址。

## Required claims

| claim | 类型 | 用途 |
| --- | --- | --- |
| `iss` | string | 身份提供方，必须精确匹配 |
| `sub` | string | 提供方内不可变用户标识 |
| `preferred_username` | string | 登录名，仅展示和查找 |
| `name` | string | 显示名 |
| `email` | string | 邀请和联系 |
| `groups` | string[] | 应用准入，不用于资源级权限 |

稳定外部键为 `(iss, sub)`。应用首次见到合法身份时创建内部 `shadow_user_id`；业务外键只引用 `shadow_user_id`。

## Legacy proxy headers

旧应用过渡时，Nginx 可以注入：

```text
Remote-User
Remote-Groups
Remote-Name
Remote-Email
```

这些字段只有在请求确认来自可信反向代理时才有效。应用必须拒绝直连后端，Nginx 必须覆盖客户端提交的同名头。

## Machine identities

Agent、MCP、定时任务和服务间调用不使用用户 OIDC 会话。每个调用方使用独立、可撤销的服务凭据，并通过审计字段记录 `service_id`。

Agent 使用与所属项目相同的 LLM registry 和项目级密钥，直接请求供应商；身份中心和平台不代理模型内容。
