# Secret inventory

此目录只保留说明。真实文件由部署机创建，并被 `.gitignore` 排除。

建议文件名：

```text
authelia-session
authelia-storage-encryption-key
authelia-postgres-password
authelia-redis-password
authelia-oidc-hmac-secret
authelia-oidc-private-key.pem
service-token-hashes.json
apps/<app-id>/service-token
media-database-url
telemetry-database-url
oss-access-key-id
oss-access-key-secret
llm/<app-id>/<provider>-api-key
agents/<agent-id>/current-token.sha256
agents/<agent-id>/next-token.sha256
```

生产目录使用 `/etc/shadow-platform/secrets/`。单服务独占目录/文件通常为 `0700/0600`；多平台服务需要共读的摘要文件使用专用 `shadow-platform` 组和 `0750/0640`，不要授予无关业务用户权限。具体所有权见生产部署手册。
