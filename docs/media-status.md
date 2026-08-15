# Shadow Media 实现状态

## 已实现并有自动测试

- 每应用独立 Bearer 服务凭据和 namespace 隔离；
- 创建 15 分钟有效的上传意图；
- 本地/NAS 原始 PUT 上传；
- 一次性上传 Token，只保存 SHA-256；
- 声明大小、全局大小和 Content-Type 检查；
- 使用 Pillow 解码图片，识别实际格式和尺寸；
- SHA-256、宽高、实际 MIME 和业务引用元数据；
- 幂等完成上传；
- public、private、scoped 可见性；
- private/scoped 的 HMAC 短时读取地址；
- 跨应用查询返回 404，避免泄露对象是否存在；
- 软删除和物理删除保留时间。

## 上线前尚需实现

- PostgreSQL Alembic 迁移；
- OSS/S3 预签名 PUT、HEAD 校验和短时 GET；
- 存储策略文件，按 `app_id` 路由 OSS 或 NAS；
- EXIF/GPS 清除、缩略图和 WebP 变体 worker；
- 到期上传意图与软删除对象清理任务；
- 服务 Token 哈希存储、轮换版本和审计表；
- 应用级格式、大小、配额和公开权限策略；
- Nginx 限流、请求体上限和完整部署验收。

因此当前版本是可验证的协议实现，不应直接暴露到公网。
