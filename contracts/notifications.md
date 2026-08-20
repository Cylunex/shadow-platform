# Shadow Notification Contract v1

Base URL 示例：`https://notify.example.com`。所有时间均为带时区的 RFC 3339；所有服务 API 使用独立 Bearer Token。

## 发布通知

`POST /v1/notifications`

```json
{
  "event_id": "trip-reminder:example-id:2026-08-20",
  "recipient": {
    "issuer": "https://auth.example.com",
    "subject": "REPLACE_OIDC_SUBJECT"
  },
  "category": "travel.reminder",
  "severity": "warning",
  "title": "行程资料还未补齐",
  "body": "明天的行程缺少一份确认单。",
  "resource_uri": "shadow://travel/trips/REPLACE_ID",
  "attributes": {"missing_count": 1},
  "delivery_mode": "home",
  "channels": [],
  "occurred_at": "2026-08-20T10:00:00+08:00",
  "expires_at": "2026-08-22T10:00:00+08:00"
}
```

响应 `202 Accepted`：

```json
{
  "notification_id": "REPLACE_UUID",
  "duplicate": false,
  "deliveries": 1
}
```

约束：

- `event_id` 在调用项目和接收人范围内稳定且可重试；
- `category` 使用 `<app>.<event>` 小写命名；
- `severity` 为 `info | success | warning | critical`；
- `resource_uri` 为空或使用 `shadow://<app>/<opaque-path>`；
- `delivery_mode` 为 `inbox_only | home | all`；
- `channels` 为空表示不额外限制，非空时只匹配列出的通道；
- `source_app_id` 不在请求中，由服务凭据推导。

Python 应用优先使用 `shadow_sdk.notifications.NotificationClient`。

## 收件箱

- `GET /v1/inbox?state=unread&limit=50&cursor=<previous-next_cursor>`
- `POST /v1/inbox/{notification_id}/read`
- `POST /v1/inbox/{notification_id}/archive`

收件箱使用 OIDC Browser Session。POST 请求必须携带允许的 `Origin` 和 `X-CSRF-Token`。
列表按发生时间倒序，响应中的 `next_cursor` 非空时可继续读取下一页。

## Chat Gateway bridge

`POST /v1/chat/commands`

```json
{
  "event_id": "REPLACE_PROVIDER_EVENT_ID",
  "channel": "telegram",
  "account_id": "default",
  "peer_kind": "group",
  "peer_id": "REPLACE_CHAT_ID",
  "thread_id": "",
  "sender_id": "REPLACE_SENDER_ID",
  "sender_is_bot": false,
  "mentioned": true,
  "command": "inbox",
  "argument": ""
}
```

成功响应：

```json
{
  "accepted": true,
  "duplicate": false,
  "response_text": "未读通知（最近 1 条）：...",
  "resource_uri": null
}
```

Gateway 约束：

- 只能使用被 `SHADOW_NOTIFY_CHAT_GATEWAY_APPS` 允许的服务身份；
- Gateway 先可靠保存提供方事件，再调用本接口；
- `event_id` 必须在 Gateway 身份下唯一；
- Gateway 负责将响应发回同一个 peer/thread；
- 不传 raw text、附件、用户昵称或历史消息；
- Platform 返回 403/422 时不得交给模型绕过。

## 运维

- `GET /v1/operations`：仅 `shadow-admins`；
- `POST /v1/operations/deliveries/{delivery_id}/retry`：仅管理员并检查 CSRF；
- `GET /healthz`：进程存活；
- `GET /readyz`：数据库和 notification schema 就绪。
