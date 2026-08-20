# Shadow Notifications v1 设计

## 1. 定位与边界

Shadow Notifications 是 Platform 的个人通知中枢，同时提供统一收件箱、外部通道投递和轻量运维视图。它保存的是“哪个 Shadow 应用在何时向哪个用户发出了什么事实”，不是聊天记录，也不是 Agent 会话存储。

```text
Domain App ──发布事实──> Notification + Inbox
                              │
                              └── Delivery Queue ──> Telegram / 飞书 / QQ

Hermes / OpenClaw ──归一化命令──> Chat Command Bridge ──> Inbox / Ops
```

职责边界：

- 业务应用决定通知的内容、接收人、类别、严重性和业务 URI；
- Platform 根据本地目标策略创建投递任务，持久保存、重试并形成死信；
- Hermes/OpenClaw 继续负责平台事件订阅、群聊上下文、提及识别和回复发送；
- Platform 只接受有限结构化命令，不保存群聊原文，不执行任意 Agent Tool；
- Domain App 的提醒计划仍属于领域项目，Platform 不复制 Health、Travel 等业务调度规则。

这套边界借鉴了 Hermes 的 group allowlist、提及门控和 home-channel 投递，以及 OpenClaw 的 peer/thread 隔离、durable inbound 和按通道账号配置方式。参考：[Hermes Telegram](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/telegram.md)、[Hermes Automation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/automation-blueprints.md)、[OpenClaw Telegram](https://github.com/openclaw/openclaw/blob/main/docs/channels/telegram.md)、[OpenClaw 飞书](https://github.com/openclaw/openclaw/blob/main/docs/channels/feishu.md)、[OpenClaw QQ](https://github.com/openclaw/openclaw/blob/main/docs/channels/qqbot.md)。

## 2. 核心模型

```text
Notification
├── source_app_id + source_event_id     幂等来源
├── recipient(issuer, subject)          OIDC 稳定身份
├── category / severity
├── title / body / attributes
├── shadow:// resource_uri
└── unread / read / archived

NotificationDelivery
├── notification_id
├── channel / account_id
├── target_kind / target_id / thread_id
├── pending / delivering / retrying / delivered / dead_letter / suppressed
└── attempts / provider_message_id / last_error

ChannelTarget
└── 用户和外部会话的绑定、home、过滤和群聊策略

ChannelPrincipal
└── 外部 sender ID 到 (issuer, subject) 的显式绑定
```

`Notification` 和 `NotificationDelivery` 必须分离：收件箱写入成功不依赖外部机器人可用性；同一事实也可投递到多个目标。业务应用无法声明 `source_app_id`，服务端从 Bearer Token 推导，避免冒充其他项目。

唯一约束为 `(source_app_id, source_event_id, recipient_issuer, recipient_subject)`。应用重试相同事件时返回原通知，不重复入箱或创建投递。

## 3. 投递策略

通知支持三个模式：

- `inbox_only`：只进入统一收件箱；
- `home`：进入收件箱并投递到接收人的 home target；
- `all`：投递到符合通道、最低严重性和类别过滤的全部目标。

外部投递是持久的 at-least-once 语义。Worker 用 PostgreSQL `FOR UPDATE SKIP LOCKED` 并发领取；可重试错误使用指数退避，不可重试错误或超出次数进入死信。Worker 崩溃后，超过五分钟的 `delivering` 租约可重新领取。由于供应商通常没有跨请求幂等键，在“供应商已接收、数据库尚未提交”这一极小窗口仍可能重复发送，通知内容必须允许重复展示。

当前原生出站适配器只发送文本：

| 通道 | 私聊 | 群聊 | 频道/话题 | 凭据 |
|---|---:|---:|---:|---|
| Telegram Bot API | 是 | 是 | 频道、Topic thread | Bot Token |
| 飞书/Lark Open API | 是 | 是 | v1 暂不原生回复 thread | App ID + App Secret |
| QQ 官方机器人 API | 是 | 是 | QQ 频道 | App ID + App Secret |

QQ 主动消息仍受官方场景和时效限制；提供方拒绝时按错误类型重试或进入死信。Platform 不通过非官方 QQ 协议登录个人账号。

## 4. Hermes / OpenClaw 群聊接入

入站采用 Gateway Bridge，而不是在 Platform 再实现三套长连接/Webhook：

1. Hermes 或 OpenClaw 接收 Telegram、飞书、QQ 事件；
2. Gateway 完成平台签名校验、事件持久化、bot/self 过滤和命令解析；
3. 仅把允许的命令归一化为 `POST /v1/chat/commands`；
4. Platform 检查 gateway 服务身份、事件幂等、sender 配对、群 allowlist 与 mention；
5. Gateway 把 `response_text` 发回原 peer/thread。

支持的命令：

```text
/help
/inbox
/read <通知编号前缀>
/archive <通知编号前缀>
/status
/ops
```

安全规则：

- sender 必须通过 `(channel, account_id, sender_id)` 显式配对到 OIDC 身份；
- 群/频道必须显式 allowlist，且目标所属身份必须与 sender 配对身份相同；
- 群/频道的出站 `categories` 必须显式列出，空列表不能表示接收全部通知；
- 群聊默认 `require_mention: true`；
- bot 发送的事件一律拒绝，阻断机器人互相触发；
- 群聊 `command_level: safe` 只允许 `/help`、`/status`，显式设为 `operator` 才能操作个人收件箱；
- `/ops` 仅允许配对角色为 `operator` 的用户在私聊中执行，任何群配置都不能放开；
- Platform 不接收 raw message、附件和历史上下文，也不会将通知正文交给模型解析。

OpenClaw 文档中的 channel/account/peer/thread 维度被统一映射为：

```text
channel + account_id + peer_kind + peer_id + thread_id
```

Telegram Topic 使用 `thread_id`；没有 thread 的通道传空字符串。Gateway 必须使用提供方稳定事件 ID 作为 `event_id`，重放会返回第一次保存的响应。

## 5. 身份和秘密

- Web 收件箱使用 Authorization Code + PKCE、state、nonce 和浏览器绑定；回调地址为精确 HTTPS allowlist；
- 本地 Session 仅保存随机句柄的哈希，Cookie 使用 `Secure + HttpOnly + SameSite=Lax`；写操作检查 Origin 和双提交 CSRF Token；
- 页面管理权限来自 `shadow-admins`；项目服务 Token 与 Hermes/OpenClaw Gateway Token 使用独立摘要 registry；
- 通道 Token/AppSecret、数据库 URL、OIDC secret 和 session secret 只从受限文件读取；
- `notification-channels.yml` 含真实用户/群 ID，不提交远程；仓库只保留占位模板。

## 6. 运维能力

`/operations` 和 `GET /v1/operations` 提供：待投递、重试、死信、目标数量、本机服务探活和最近死信列表。管理员可对单条死信执行重新投递，操作会写审计事件。

探活 URL 只允许显式 IPv4/IPv6 loopback 地址，避免服务成为 SSRF 跳板。探活结果是轻量可用性信号，不替代 Prometheus、日志或数据库备份。

保留策略默认 180 天：

- 未过期的 `unread/read` 通知继续保留；
- 已归档超过保留期或过期超过保留期的通知可清理；
- Chat ingress 幂等记录和审计事件按同一保留期清理；
- 已失效的浏览器 session 和 OIDC transaction 单独清理。

PostgreSQL schema 必须先执行 Alembic migration。生产 API/Worker 不自动建表，`/readyz` 会在表不存在时失败。
个人级 v1 使用单个 API 进程完成本地通道配置同步；横向扩容前需把配置同步改为带数据库锁的独立部署步骤。

## 7. v1 不做的内容

- 不保存完整聊天、Agent memory 或群聊成员目录；
- 不让 LLM 自由决定运维写操作；
- 不做营销群发、复杂模板设计器、已读回执同步和多租户计费；
- 不在 Platform 建立 Health/Travel 等领域提醒规则；
- 不实现未经官方支持的 QQ 个人号协议。

这些限制使 v1 足够完整地覆盖个人级统一收件箱、可靠投递和家庭/个人群机器人场景，同时不引入企业消息平台的复杂度。
