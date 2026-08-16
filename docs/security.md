# 安全边界

## 身份

- OIDC 只允许 Authorization Code Flow，浏览器客户端必须使用 PKCE。
- 回调地址必须逐项登记，不使用通配符。
- 应用以 `iss + sub` 识别用户，校验 issuer、audience、签名、过期时间和 nonce。
- ID Token 缺少 groups 时，只有在完整验证 ID Token、调用发现文档中的 UserInfo endpoint，
  且 UserInfo `sub` 与 ID Token `sub` 常量时间一致后，才能使用 UserInfo groups；缺组不能
  降级为默认放行。
- 业务应用自己的会话 Cookie 使用 `Secure`、`HttpOnly`、`SameSite=Lax`，并收窄 Path。
- 新项目必须原生 OIDC，不接受 `Remote-User`、`Remote-Groups` 等代理身份头建立会话。
- Nginx 应清空客户端提交的历史代理身份头，避免业务代码未来误用。
- Health 的既有 Forward Auth 链路仍须同时校验真实传输对端、应用独立代理密钥和准入组；
  该例外不得复制到新项目。

## 人类与机器凭据

浏览器使用 OIDC；Agent、MCP、备份和定时任务使用独立服务凭据。服务凭据必须：

- 每个项目独立；
- 可撤销、可轮换；
- 不写入移动端、网页源码、Git 或日志；
- 只授予必要 namespace 和操作；
- 在数据库中只保存哈希或密钥版本标识。

Media 与 Telemetry 共用服务 Token 摘要 registry，每个应用最多同时保留新旧两份 SHA-256 完成无停机轮换；原始 Token 只存在于调用应用专用、其他业务用户不可读的文件中。

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

生产密钥放在 `/etc/shadow-platform/secrets/`，按服务划分 Unix 用户/组并只授予所需读权限。单进程独占文件通常使用 `0600`；平台服务共享摘要使用 `root:shadow-platform 0640`。容器通过只读 volume 和 `*_FILE` 变量读取。仓库中的 `secrets/README.md` 只描述名称，不存值。

需要备份且不可随意重建的密钥：

- Authelia storage encryption key；
- Authelia OIDC signing private key；
- 应用 OIDC client secret；
- Media/Telemetry 服务 Token 摘要 registry 与调用端原始 Token；
- OSS access credential。
- 各项目独立的 LLM provider API Key。

轮换前必须确认旧令牌和加密数据的兼容窗口。

## LLM 直连

- registry 禁止出现 `api_key`、`token`、`secret`、`password` 等内联字段。
- 密钥文件路径必须解析在配置的 secrets 根目录内，拒绝绝对路径逃逸和 `..` 穿越。
- 优先为每个应用创建独立供应商 Key；共享 Key 必须是显式决定。
- 只有服务端读取 Key，浏览器、ShadowApp 和公开配置接口不得获取。
- LLM SDK 运行在各业务进程内，只向 registry 指定的供应商发请求，不经过平台服务。
- SDK 不记录密钥、请求正文、响应正文或供应商错误正文，归一化异常只保留状态码和错误类别。
- 可选用量事件默认只含模型、Token 数、延迟和状态，不含提示词、回答或媒体 URL。
- 用量 sink 失败会被隔离，不能改变模型调用结果；JSONL outbox 必须放在服务端受限目录。
- Collector 按服务 Token 强制 app namespace，拒绝上报其他应用的事件；查询也只能查看当前应用。

## Agent

- Agent Token 必须是至少 32 字节的高熵随机值，不使用人类密码。
- Registry 只引用 Token SHA-256 文件，不保存原始 Token。
- 每个 Agent 明确声明 audiences 和最小 scopes，应用本地同时检查二者。
- 用户可见的统一人格按项目使用独立 principal/Token，不持有横跨所有 audience 的万能凭据。
- 写操作继续要求项目级幂等键、资源权限和审计；通过 Agent 身份不等于拥有全部业务权限。
- Capability Manifest 是发现和编排元数据，不是授权来源；Skill/Prompt 不能放宽服务端策略。
- 读取个人或敏感资源时还要检查项目数据库中的资源授权；Travel 地图授权是标准参考实现。
- 高风险写入优先生成结构化草案，用户确认后由确定性 API 应用；Prompt 输出不是确认凭据。
- Token 轮换窗口最多同时接受两份摘要，完成切换后立即移除旧摘要。
- 未设计用户委托令牌前，不信任 Agent 自报的 `actor_sub`。
