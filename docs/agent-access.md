# Agent 统一接入

状态：2026-09-07 目标设计；中央服务尚未实现。规范入口为 [统一身份、鉴权与 Agent 接入 UA-1](nexus-unified-access-design.md)。

## 唯一授权来源

Platform 集中管理 Agent/workload/device、user 委托、audience/capability/resource scope、Token/Session 生命周期与撤销。领域挂载 SDK 并保留 owner、成员、来源和内容可见性检查，不再维护自己的 Agent 注册表、摘要文件或 Grant 管理页。

一个稳定 Agent 身份可按中央授权访问多域，但每张短期票据只绑定一个 audience/instance/command；不使用跨全部域的万能 Token。机器身份不自动代表人类，执行前必须有 current_intent、standing_policy 或精确 inline_confirmation。

## 调用链

统一 Host/MCP → Platform 目录/Access → 目标领域 SDK → 本地资源检查与事务 → 领域 Result/Receipt。中央拒绝或不可用时不回退旧凭据；模型不能提供 owner、scope、票据或批准人。

普通明确记录无二次确认，高影响确认留在当前交互；授权凭证与执行 Receipt 分开。具体 API、期限、撤销线性化、中央故障与兼容切换均由 UA-1 定义，不在领域另行解释。

## 当前实现的兼容说明

`shadow_sdk.agent.AgentAuthenticator` 目前仍是本地 YAML registry + SHA-256 摘要校验，并非中央服务。Health/Archive 还有不同 registry 实现；旧项目按现有配置运行到对应能力切换。`generate_agent_token.py` 仅维护 legacy 主体，不能作为新接入必须每域创建 Token 的模板。

现有 scope 与领域 Grant 仍必须同时满足，不可先删除校验。迁入中央的授权仅取有效权限交集，旧 allow_confirm 不自动成为新 execute 权限；按 capability 选择唯一 auth_mode 后才停旧入口。

现有旧配置的完整参考保存在 [v1 历史实现说明](legacy/agent-access-v1.md)，仅用于迁移期维护。
