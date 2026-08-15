# 同类项目对标与取舍

本轮对标的目标不是把 Shadow Platform 做成另一个大型平台，而是吸收成熟项目已经验证过的边界和运维模式。

## Backstage：采用声明式服务目录，不引入整套门户

[Backstage Software Catalog](https://backstage.io/docs/features/software-catalog/) 把应用所有权、类型、生命周期和依赖关系保存在代码库内的 YAML 元数据中，再由平台汇总。Shadow 项目数量不多，直接部署 Backstage 的前后端和插件体系成本过高，因此只采用它的核心模式：

- `catalog/apps.yml` 是应用身份、入口、认证方式、健康检查、媒体和 LLM 能力的单一目录；
- `contracts/app-catalog.schema.json` 固定字段契约；
- `scripts/platform_doctor.py` 检查 Catalog、LLM registry、Agent registry 和 OIDC 客户端之间的引用。

## Authelia / authentik：保留 OIDC + 过渡 Forward Auth

[Authelia](https://www.authelia.com/overview/authorization/openid-connect-1.0/) 已支持标准 OIDC，[Nginx 集成](https://www.authelia.com/integration/proxies/nginx/)也覆盖 AuthRequest。authentik 的官方文档同样把原生 OIDC 用于新应用，把 [Forward Auth](https://docs.goauthentik.io/add-secure-apps/providers/proxy/forward_auth) 用于暂时不能接 OIDC 的旧应用。

因此不更换身份产品：新项目使用 Authorization Code + PKCE；Garden、Health、Stock 在迁移期间使用单应用、独立授权规则的 AuthRequest。Authelia 镜像锁定版本，不使用 `latest`。

## Langfuse / OpenTelemetry：只借异步遥测，不集中提示词

[Langfuse SDK](https://langfuse.com/docs/observability/sdk/overview) 的成熟经验是异步发送、精确时间戳以及遥测失败不能破坏应用；它也明确建议敏感数据在客户端离开应用前就完成掩码。OpenTelemetry 的 [GenAI metrics 约定](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/gen-ai-metrics.md)统一了输入/输出 Token 等指标。

Shadow 的隐私边界更严格：默认根本不采集 prompt、response、tools 和媒体 URL。SDK 写本地 JSONL outbox，独立 systemd timer 批量上报固定字段；collector 按 `(app_id, request_id)` 幂等入库并只提供聚合查询。模型请求仍然直接到供应商。

## MinIO / tus：当前图片走短时 PUT，视频阶段再上分片续传

MinIO 官方 SDK 支持私有桶的短时 [presigned PUT](https://docs.min.io/aistor/developers/sdk/javascript/api/)，并支持 multipart 和校验和。tus 定义了基于 offset/PATCH 的[可恢复上传协议](https://tus.io/protocols/resumable-upload)。

当前 Shadow Media 上限为 15 MiB，且服务端需要解码图片、确认真实 MIME、移除 EXIF/GPS，因此本地/NAS 后端继续采用一次性短时 PUT，链路更简单且能完成内容校验。未来加入旅行视频或大文件时，再增加 S3 multipart 或 tus，不让当前图片 API 提前承担复杂度。
