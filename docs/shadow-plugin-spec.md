# Shadow Plugin Specification v0.1

## 1. 总原则

每个领域项目都是可独立部署、独立升级并拥有自己数据与业务规则的完整应用。Shadow Plugin
只是项目向 Shadow 生态暴露应用入口、Agent 能力、事件、资源和确认界面的远程合同，不把
业务代码装入 Platform 或 Shadow App 进程。

Platform 是控制面，负责合同、注册、兼容性、权限 Profile 和运行时 Bundle。DSH 等 Agent
Runtime 直接调用领域服务；Platform 不转发 LLM、HTTP、MCP 或领域数据流量。

## 2. 插件种类

`ShadowDomainPlugin` 对应一个拥有领域数据和 API 的独立应用。`ShadowCompositionPlugin`
只声明跨两个及以上领域的 Skill、Prompt、Eval 和能力依赖，不拥有领域数据库。Composition
源码不进入 DSH 进程；Platform 可以把经过校验的只读 Skill 快照编译进目标 Runtime。
首期只实现 Domain Plugin；Composition Plugin 仅保留合同位置，不能将跨项目业务逻辑放进
Platform 核心。

## 3. Definition 与 Instance

项目仓库提交 `shadow-plugin.yaml`，其中只有静态身份、版本、兼容范围和描述符引用：

```yaml
apiVersion: shadow.cylunex/v1alpha1
kind: ShadowDomainPlugin
metadata:
  id: shadow-travel
  version: 1.0.0
  display_name: Shadow Travel
spec:
  compatibility:
    shadow_plugin_api: v1alpha1
    dsh:
      distribution: ">=0.1.1-rc.1 <0.2.0"
      tools_api: ">=0.1.1-rc.1 <0.2.0"
  descriptors:
    agent: agent/manifest.yaml
    events: contracts/events/index.yaml
    resources: contracts/resources/index.yaml
```

真实服务地址和凭据不进入 Definition。部署侧 Instance Registry 只登记环境变量名：

```yaml
travel-production:
  plugin_id: shadow-travel
  plugin_version: 1.0.0
  environment: production
  base_url_env: SHADOW_TRAVEL_BASE_URL
  credential_env: SHADOW_TRAVEL_AGENT_TOKEN
  enabled: true
```

运行时从受限环境读取真实值。一个 Definition 可以对应开发、测试和生产多个 Instance。

## 4. Agent Capability v2

领域项目的 `agent/manifest.yaml` 是能力语义真相，OpenAPI/MCP 是输入输出结构真相：

- Manifest 声明 capability、effect、数据等级、风险、确认、幂等和工具暴露策略；
- OpenAPI 声明 HTTP operation、参数、请求体和返回结构；
- MCP Tool Schema 声明 MCP 工具结构；
- Skill 只指导模型如何正确使用能力，不能放宽合同或服务端策略。

每个工具必须声明 `contract_ref`、`operation_id`、超时、并发安全、重试、结果模式和最大结果
字节数。`max_result_bytes` 限制领域服务响应，`max_model_chars` 独立限制实际渲染给模型的内容。
DSH Adapter 将稳定 Shadow 工具名转换成 `shadow_<domain>_<resource>_<verb>`，映射进入
Bundle lock 和 `shadow-runtime-manifest.json`。能力 ID 保持 `<domain>.<resource>.<verb>`，不再
重复添加 `shadow.` 前缀。

Skill 的源码目录是一个整体发布单元。`SKILL.md` 引用的 `references/`、`scripts/`、`assets/`
继续归领域项目维护，Builder 完整复制目录并生成 DSH `resourceBase`；不允许依赖项目仓库的
绝对路径，也不允许 Skill 目录包含符号链接逃逸。

## 5. 风险与确认

| 等级 | 含义 | 默认行为 |
| --- | --- | --- |
| L0 | 只读或分析 | 自动执行 |
| L1 | 可撤销的私有写入 | 自动执行并通知 |
| L2 | 普通业务写入 | 当前 Profile 预授权，否则询问 |
| L3 | 外部可见或难撤销 | 执行前明确确认 |
| L4 | 资金、安全或高危执行 | 普通 Profile 硬拒绝，专用 Profile 才可确认 |

确认策略必须与等级匹配：`none`、`notify`、`policy`、`explicit`、`elevated`。领域服务仍是最终
授权者，必须独立验证 audience、scope、资源授权、幂等键和确认凭证。

L3/L4 最终执行使用运行时无关的 `ConfirmationReceipt`。回执绑定 actor、audience、plugin、
capability、tool、effect、参数摘要、资源、有效期与单次 nonce，并由领域服务验签和消费。DSH
Approval 只能表达当次会话中的允许，不能替代该回执。L0/L1 不使用回执；L2 默认依赖 Profile
预授权和领域鉴权。

## 6. 结果合同

领域 API 保持自己的业务响应 Schema；Adapter 将需要跨 Runtime 表达的结果归一为
`ShadowToolResult`。结果使用 `inline`、`resource`、`proposal`、`continuation` 四种判别类型，
共同携带摘要、敏感级别、截断状态和来源。完整长期数据保留在领域应用或 Shadow Asset，模型
只接收有预算的摘要、`shadow://` 引用或 continuation。

## 7. 生命周期

Platform 中的“安装”表示注册远程实例，不部署领域代码：

```text
discovered → registered → trusted → configured → enabled
                                      ↘ degraded
enabled → disabled → removed
```

权限扩大必须重新审批；合同不兼容进入 `degraded`；移除插件不删除领域数据，也不允许其他
项目复用已经发布的插件 ID。旧 `shadow://` URI 在来源不可用时明确返回不可用状态。

## 8. 运行时 Profile

Profile 明确选择插件实例、能力集合、预授权规则和模型暴露预算。一个 Profile 只允许每个
Plugin/Instance 出现一次，生成一份不可变 Bundle、`agent-bundle.lock` 与
`shadow-runtime-manifest.json`。普通 Profile 不包含 L4 工具；Health、Ledger 和 Foliant 使用
分离的 Profile 与会话存储。

首期 Profile：

```text
shadow-general       Travel + Archive
shadow-health        Health
shadow-ledger        Ledger
```

Foliant 后续增加 `shadow-finance-research`；执行 Profile 只预留名称，默认不建设。

DSH 的发行版版本与宿主 npm API 版本分别管理。当前 `0.1.1` 版本线的实机基线为
`0.1.1-rc.2`。Plugin 使用兼容范围声明能力，Builder 会实际校验 Profile 的精确版本是否落入
范围；生成 Bundle 使用精确 Tools API 版本作为 peer dependency。禁止把 DSH 宿主包放入普通
`dependencies`，避免 Profile 内产生第二份宿主运行时。

## 9. DSH Adapter 边界

所有领域共用一份生成的 DSH Bundle。Bundle 内部可以分为 Remote、Policy、Skill 和 UI
模块，但在出现独立发布周期前不拆成多个 npm 包。每个领域实例只生成一条 Cordis 配置；
Adapter 按 `instanceId` 读取正确的地址和凭据环境变量，然后直接调用领域 API。Platform 负责
配置、合同、令牌与审计控制面，不转发领域响应流量。

正式 Shadow Profile 不包含 Dynamic Extension 或外部市场安装入口。实验性扩展使用不同
Profile、DSH_HOME 和凭据边界，不能注册正式领域能力 ID。

## 10. 首期范围

Platform 先完成协议、校验、DSH Builder 与 conformance fixture，再依次接入 Travel、Archive、
Health、Ledger。Garden 和 Foliant 进入第二批。Verse、Wingman 和 Chronicle 不在本轮范围。
