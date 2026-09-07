# 应用接入规范

2026-09-07 目标规范：[统一身份、鉴权与 Agent 接入 UA-1](nexus-unified-access-design.md)。中央 Session/Access 尚未实现；当前项目登录实现保留到各自迁移验收完成。本页是接入索引，不再要求领域自行开发 OIDC/Session。

## 注册一次

Platform 统一登记 app/product/instance、Identity client/callback、canonical origin/允许别名、应用准入、workload、能力及披露策略。真实配置在仓库外；领域声明无秘密 Manifest/OpenAPI/Surface，使用编译产物，不手工维护第二套地址/认证清单。

## 浏览器

应用只挂载 Platform SDK 路由；Identity 仍是唯一 OIDC issuer，Code + PKCE、state/nonce/JWKS/claim 校验由中央服务与共享 SDK 完成。每应用 host-only Session handle、中央会话状态与唯一 user 映射；不共享父域 Cookie、不在前端保存 Token。详情见 UA-1 第 4 节。

已有本地 OIDC 表、Health Hybrid、Nexus 代理头属于 legacy，不作为新项目模板。多入口是否跳转由编译后的 Catalog 与历史兼容决定，不为统一形式破坏已支持的子路径，也不在别名建立未经登记的回调。

## Agent、服务与设备

Agent/MCP 进入中央目录与 Access；后台服务与设备使用各自中央 principal，不复用人类 Cookie。票据限制 audience/instance/capability/resource/command，SDK 获取 Principal 后领域仍校验 owner/成员/来源。新项目不生成独立 Agent registry、Token 管理页或 OAuth 服务。

## 领域能力

最小接入是共享 auth middleware、resource_check、事务 handler、操作结果查询及声明式 Surface。普通记录 direct，高影响 inline_confirm；不要求每个域都有 Review create/list/commit/reject。Public health 无敏感信息，readiness 受保护；机器未登录返回 JSON 401/403，不重定向 HTML。

## Asset、模型与任务

文件生命周期继续由 Asset 管理；业务仅保存固定版本/稳定引用。各 workload 向中央申请自己目标范围的权限，不传递上游 Token。模型调用通过统一 Model SDK/Gateway，业务模板和计算留域；敏感 provider fallback 不扩权。长任务仍由领域 worker 持久化，通过统一 task/result 合同展示。

## 接入检查

- app/client/callback、SDK/Runtime/合同版本装配一致。
- 跨 owner、audience、过期/撤销、旧 credential fallback、日志脱敏负向 fixture 通过。
- 普通 command 写前持久、同键重试/异内容冲突、真实 Receipt/read-back 通过。
- 历史 user/grant/Session 映射有清单，旧权限不扩大，旧会话明确过期而非静默归属新用户。
- 真正涉及设备/浏览器/发布的能力在相应层验收，编译或 health 绿灯不替代实际验证。

现有旧配置的完整参考保存在 [v1 历史实现说明](legacy/app-integration-v1.md)，仅用于迁移期维护。
