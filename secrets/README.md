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
media-service-tokens.json
media-database-password
oss-access-key-id
oss-access-key-secret
llm/<app-id>/<provider>-api-key
agents/<agent-id>/current-token.sha256
agents/<agent-id>/next-token.sha256
```

生产目录使用 `/etc/shadow-platform/secrets/`，目录 `0700`、文件 `0600`。
