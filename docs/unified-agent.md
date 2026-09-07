# Shadow 统一 Agent 设计

2026-09-07 起，目标设计以 [UA-1 统一接入规范](nexus-unified-access-design.md) 为唯一公共依据；旧“每域独立凭据直连、永久无 Gateway”不再作为未来架构约束。中央能力尚未实现，不因文档变更改变现有接口权限。

## 统一维护的内容

Platform 维护身份、Session、Agent/workload/device 注册与委托、工具目录/传输适配、MCP 入口、公共执行/确认、模型传输/预算/重试和审计格式。Nexus 复用 DSH loop 承接会话和跨领域协调；领域助手采用同一 Runtime 客户端，不各建一套通用 Agent 调度框架。

领域维护业务 Skill、Prompt、参数/结果 Schema、handler 和正确性 eval；领域事实、确定性计算、worker/lease 与 Receipt 继续在所属项目。模型会用到的内容经授权的中央 Gateway 临时处理，默认不落日志；这替代旧“Platform 永不接触 Prompt”的绝对承诺。

## 装配与权限

Manifest/OpenAPI/Surface 仍是领域能力来源；Profile Compiler 生成版本化工具目录和 App/Nexus 投影。中央根据用户、Agent、delegate、Profile、disclosure 和资源范围裁剪目录，执行时重查，不把 Skill、目录可见性或模型风险标签当授权。

普通写入为受信用户意图的隐藏 executor 调用；真正高影响动作通过中央内联确认。授权采用单 audience/instance 短时票据，跨域重新申请，不透传 Token。业务操作不构成跨领域事务，部分成功与未知结果逐项呈现。

## 兼容与边界

现有 DSH Bundle、Review v1、各域 MCP、进程内 LLM SDK 继续描述当前代码；新增接入使用 UA-1 的能力迁移阶段。旧执行安全限制保持到验收完成，不批量降低 risk 或 scope。

既有只读 Composition 保持可用。多域工作流复用统一 Runtime 与 Journal，不创建独立授权数据库；领域任务仍可独立部署，只有实测资源需求才拆分运行进程。

详细职责、API、数据模型、故障/撤销、迁移与验收见 UA-1 第 1—10 节；各领域差异见其 `docs/nexus-integration-design.md`，不复制另一份通用协议。

现有旧配置的完整参考保存在 [v1 历史实现说明](legacy/unified-agent-v1.md)，仅用于迁移期维护。
