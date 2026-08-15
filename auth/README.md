# Shadow Identity

本目录保存 Authelia 的审查模板，不保存生产配置和真实用户库。

## 目标

- 公网 issuer：`https://auth.cylunex.top`
- 本机监听：`127.0.0.1:9091`
- 用户后端：首期 YAML 文件，管理员手工开户
- 持久数据：PostgreSQL
- 会话：Redis
- 二次验证：WebAuthn/TOTP
- 新应用：OIDC Authorization Code + PKCE
- 旧应用：Nginx AuthRequest 过渡

## 上线前必须完成

1. 按部署时锁定的 Authelia 版本校验并更新 `configuration.yml.example`。
2. 生成 Argon2 用户密码哈希，复制模板为不入库的 `users_database.yml`。
3. 在 `/etc/shadow-platform/secrets/` 创建全部密钥文件。
4. 为每个应用生成独立 client ID、client secret 和精确 redirect URI。
5. 将文件通知器替换为 SMTP；文件通知器只能用于首次旁路验证。
6. 执行 Authelia 配置校验，再允许 Nginx 转发公网流量。

`oidc-clients.yml.example` 是待合并到 `identity_providers.oidc.clients` 的登记模板，其中的 secret 必须替换为 Authelia 支持的哈希格式。
