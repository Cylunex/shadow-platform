# 渐进迁移顺序

## Phase 0：旁路部署

- 部署 Identity 和 Media，但不修改现有路由。
- 使用测试用户、测试 bucket 和独立数据库验证。
- 验证 ShadowApp WebView 的跳转、Cookie、文件选择器和返回键。

## Phase 1：Garden

- 公开页面保持匿名。
- `/admin/` 和写接口接入 SSO。
- 新上传先进入 Media；旧 `/uploads/` 保持只读。
- 后台批量生成旧图片的媒体元数据，内容 URL 分批迁移。

## Phase 2：Stock

- Web 页面接入 SSO。
- MCP、Agent 和任务接口保持独立服务 Token。

## Phase 3：Health

- 先接 SSO，不立刻迁移照片。
- 增加 `shadow_user_id` 后再评估由单用户转为多用户的数据隔离。
- 餐照、化验单迁移到 NAS media namespace；迁移完成前旧文件路由只读兼容。
- AI 图片分析通过受限内部访问读取，不使用公共 URL。

## Phase 4：Travel

- 从首版直接使用 OIDC 和 Media。
- 地图成员、邀请、照片可见性全部由 Travel 管理。
- 正式入口为 `/travel/`，子域名只做重定向。

## 回滚原则

- 每次只迁移一个入口或一种媒体写路径。
- 切换期间保留旧认证/旧文件读取，但禁止双写身份状态。
- 数据迁移先复制并核验哈希，再切引用，最后进入延迟删除期。
- Nginx 配置先 `nginx -t`，再 reload，不直接覆盖无备份的线上配置。
