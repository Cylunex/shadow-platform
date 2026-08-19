# Asset API v1 contract

服务从 Bearer 凭据解析 `app_id`，客户端不得提交可信 app 标识。运行时 FastAPI OpenAPI 文档的
`assets` tag 是请求/响应字段的机器可读定义；本文件固定跨项目必须依赖的语义。

| Endpoint | 用途 | 幂等键 |
| --- | --- | --- |
| `POST /v1/upload-sessions` | 创建上传会话 | `Idempotency-Key` + 调用应用 |
| `PUT /v1/upload-sessions/{id}/content` | 用 Upload Token 直传原件 | 单会话一次成功 |
| `POST /v1/upload-sessions/{id}/complete` | 校验、去重并创建 Asset | upload session |
| `POST /v1/assets/{id}/version-upload-sessions` | 给现有 Asset 上传新版本 | `Idempotency-Key` + 调用应用 |
| `GET /v1/assets/{id}` | 获取有权访问的 Asset 和当前版本 | 只读 |
| `POST /v1/assets/{id}/trash` | 将 Asset 放入回收站 | asset id |
| `POST /v1/assets/{id}/restore` | 在保留期内恢复 Asset | asset id |
| `POST /v1/asset-references` | 创建/恢复业务引用 | `app_id + reference_key` |
| `DELETE /v1/asset-references/{id}` | 解绑业务引用 | reference id |
| `POST /v1/asset-derivatives` | 登记衍生版本关系 | source + recipe + version + params hash |
| `POST /v1/asset-versions/{id}/access-grants` | 签发版本级短时访问 | 可重复 |

Platform 返回的 `target` 是兼容客户端使用的规范上传入口。部署配置了受控局域网入口时，
响应还可包含 `alternate_targets`；它们共享同一个短时、单会话 Upload Token。客户端可以
先探测局域网目标，失败后使用规范入口，但不得持久化 Upload Token 或把目标改写为任意主机。

稳定枚举：

```text
ownership_mode: user_owned | app_managed | derived
access_mode: private | delegated | public
sensitivity: normal | sensitive | restricted
binding_mode: pinned | latest
lifecycle_state: active | trashed | purged
```

业务引用 URI 必须符合 `shadow://{app_id}/{opaque-path}`。错误响应不暴露跨应用对象是否存在，
调用方应将 404 统一视为不存在或无权访问。
