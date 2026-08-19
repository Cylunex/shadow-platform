# Shadow Media 实现状态

> 本页描述旧图片兼容 API。新项目统一接入 `docs/asset-service-v1.md` 的 Asset API；旧 API
> 保持运行并可通过回填脚本映射，不再扩展新的通用文件能力。

## 已实现并有自动测试

- 每应用独立 Bearer 服务凭据和 namespace 隔离；
- 创建 15 分钟有效的上传意图；
- 本地/NAS 原始 PUT 上传；
- 一次性上传 Token，只保存 SHA-256；
- 声明大小、全局大小和 Content-Type 检查；
- 使用 Pillow 解码图片，识别实际格式和尺寸；
- 默认重编码并移除 EXIF/GPS、ICC、注释和文本元数据；
- SHA-256、宽高、实际 MIME 和业务引用元数据；
- 幂等完成上传；
- public、private、scoped 可见性；
- private/scoped 的 HMAC 短时读取地址；
- 跨应用查询返回 404，避免泄露对象是否存在；
- 软删除和物理删除保留时间；
- 服务 Token 仅存 SHA-256 摘要，支持新旧两份凭据无停机轮换；
- 定时清理过期上传意图、残留临时文件和到期软删除对象；
- PostgreSQL、systemd、Nginx、生产环境变量和上线验收模板。

## 当前部署边界

首版适用于当前本地/NAS 图片场景，可按 `docs/production-runbook.md` 直接部署。上线前仍需为每个应用设置独立 Token、PostgreSQL、签名密钥和 HTTPS，并通过严格 doctor 与验收清单。

以下旧规划已由 Asset v1 接管，不再在 Media 模型中重复实现：

- Asset 表结构已使用 Alembic 管理；
- 国际对象存储、OSS/S3 预签名 PUT 和按应用路由；
- 视频等大文件采用 multipart 或 tus 断点续传；
- 缩略图、WebP/AVIF 变体异步 worker；
- 应用级配额、公开权限策略和审计查询界面。
