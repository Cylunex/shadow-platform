# 安全边界

## 身份

- OIDC 只允许 Authorization Code Flow，浏览器客户端必须使用 PKCE。
- 回调地址必须逐项登记，不使用通配符。
- 应用以 `iss + sub` 识别用户，校验 issuer、audience、签名、过期时间和 nonce。
- 业务应用自己的会话 Cookie 使用 `Secure`、`HttpOnly`、`SameSite=Lax`，并收窄 Path。
- Nginx 必须覆盖而不是透传客户端提交的 `Remote-User`、`Remote-Groups` 等身份头。
- 代理身份头只允许来自本机 Nginx 或明确的可信代理地址。

## 人类与机器凭据

浏览器使用 OIDC；Agent、MCP、备份和定时任务使用独立服务凭据。服务凭据必须：

- 每个项目独立；
- 可撤销、可轮换；
- 不写入移动端、网页源码、Git 或日志；
- 只授予必要 namespace 和操作；
- 在数据库中只保存哈希或密钥版本标识。

## 媒体

- 默认 `private`，只有明确声明才允许 `public`。
- 原始文件名永不作为对象键。
- 上传意图短时有效、只能完成一次，并绑定大小、MIME 和调用应用。
- 服务端验证魔数和图片解码结果，不只相信扩展名或 `Content-Type`。
- 对公开和协作图片默认移除 EXIF/GPS；需要保留时必须由应用策略明确允许。
- 私密下载 URL 短时有效，禁止把 OSS 永久地址保存到业务表。
- 删除先标记，经过保留期后再删除对象，以支持恢复和审计。
- 日志记录 media ID、app ID、subject 和结果，不记录签名 URL、上传 Token 或文件内容。

## 密钥

生产密钥放在 `/etc/shadow-platform/secrets/`，文件权限 `0600`，目录权限 `0700`。容器通过只读 volume 和 `*_FILE` 变量读取。仓库中的 `secrets/README.md` 只描述名称，不存值。

需要备份且不可随意重建的密钥：

- Authelia storage encryption key；
- Authelia OIDC signing private key；
- 应用 OIDC client secret；
- 媒体服务凭据哈希的 pepper（如启用）；
- OSS access credential。

轮换前必须确认旧令牌和加密数据的兼容窗口。
