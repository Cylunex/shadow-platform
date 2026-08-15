# 应用接入规范

## 新应用

新应用直接使用 OIDC Authorization Code + PKCE：

1. 在 Identity 注册独立 client ID 和精确回调地址。
2. 登录回调校验 `state`、`nonce`、issuer、audience 和 PKCE。
3. 通过 `(issuer, subject)` 查找或创建本地 `shadow_user_id` 映射。
4. 建立应用自己的服务端会话，不把 OIDC Token 写入 localStorage。
5. 退出应用会话时提供“仅退出本站”和“退出全部 Shadow 应用”两个明确动作。

## 旧应用过渡

可以先由 Nginx `auth_request` 保护页面，并注入：

```text
Remote-User
Remote-Groups
Remote-Email
Remote-Name
```

后端只在请求来自可信代理时读取这些字段。完成原生 OIDC 后移除代理身份依赖。

## 媒体接入

浏览器不直接持有媒体服务凭据。业务后端代理控制面调用：

```text
POST /v1/uploads
POST /v1/uploads/{upload_id}/complete
POST /v1/media/{media_id}/access
DELETE /v1/media/{media_id}
```

业务后端应保存 `media_id`，不要保存预签名 URL。获取私密文件前先检查当前用户对业务资源的权限，再请求短时 URL。

## 健康检查

每个应用继续提供无需登录、无敏感信息的 `healthz`。探活路径不得建立会话，也不得返回用户、版本密钥或数据库连接信息。
