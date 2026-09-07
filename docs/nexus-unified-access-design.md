# Nexus 统一身份、鉴权与 Agent 接入设计

设计版本：2026-09-07 / UA-1。状态：目标设计已确定，尚未实现。本文是统一接入的唯一公共规范；旧实现及旧安全限制在对应能力完成迁移前继续生效，不因文档更新而开放权限。

范围：Platform、Nexus、Health、Ledger、Travel、Archive、Garden、Foliant、App。本轮不规划 Media、Wingman、Verse、Relay、MDC。Platform 内现有 Asset 基础服务仍是文件依赖，不等同本轮改造 Media 客户端项目。

## 1. 本次架构决策

统一不是让每个项目复制一份 registry 或使用同一个万能 Token。Platform 集中维护人类身份映射、应用会话、机器主体、Agent 委托、凭据生命周期、策略、确认、工具目录、模型接入和 Agent 公共执行组件；领域只实现业务能力、资源权限与事实事务。

| 唯一管理方 | 数据/机制 | 领域保留什么 |
| --- | --- | --- |
| Shadow Identity | 用户登录、认证因子、OIDC issuer、用户组 | 不建立密码库或第二 issuer |
| Platform Access | 全局 user ID、应用 Session、workload/device/Agent 注册、委托、凭据、撤销、公共策略 | SDK 校验得到的 Principal；本地 user ID 映射缓存 |
| Platform Agent Runtime | 工具发现/调用适配、模型连接、运行预算、确认流程、公共错误/审计、会话运行组件 | 声明式 Skill/Prompt/Schema、领域 eval 和业务应用服务 |
| Nexus | 用户入口、当前意图、跨域协调、会话展示、Operation Journal | 领域不再各建通用聊天调度器和审核中心 |
| 各领域 | owner、成员/角色、来源可见性、事实/版本、任务、执行回执 | 无独立 Agent registry、Token 发放、Agent grant 管理页或审批策略库 |

Platform 以现有仓库中的模块提供 Access、Agent/MCP、Model 服务；第一阶段可共用一个进程与数据库的独立 schema。共享 Python middleware、TypeScript Host client、Android client 均由 Platform 发布和维护，不要求九个项目分别写协议实现。是否拆进程由实际负载决定，不新增通用微服务群。

跨领域 Agent 运行复用现有 DSH 及 Platform Adapter，不重造 Agent 框架。Domain Web 仍独立可用；其助手入口可打开 Nexus 带资源上下文的会话，或嵌入同一 Runtime 客户端，不能暗中启动第二套独立 Agent loop。

## 2. 已实现基线与需要迁出的重复内容

| 当前源码 | 当前边界 | 迁移目标 |
| --- | --- | --- |
| `shadow_sdk/identity.py` | 仅规范化已验证 OIDC claims，不负责完整登录/Session | Platform 统一 OIDC client/会话服务及可挂载路由 |
| `shadow_sdk/agent.py` | 启动时加载本地 YAML registry 与摘要 | 只在中央兼容层使用，领域改统一在线校验 |
| `shadow_sdk/service_auth.py` | 服务 Token registry 与本地摘要验证 | 中央 workload/device 注册、轮换与 audience 限定 |
| Health `app/machine_auth.py` | 独立 JSON registry、profile_grants | 中央 Agent delegation；Health 只校验 profile 所有权与数据来源 |
| Archive `archive_service/machine_auth.py` | 自定义 owner_id registry | 中央 subject 映射和委托；Archive 保留内容敏感度策略 |
| Ledger AgentGrant/ApprovalGrant、Travel ResourceGrant | 每域维护 Agent 权限及逐次审批 | 中央授权真相；领域表先作为只读兼容映射/历史证据 |
| 各域 OIDC、Session、MCP 和 LLM wrapper | 分别维护身份、模型循环与接入参数 | Platform SDK/统一入口，领域只注册 handler |

本设计撤销旧文档中“永久无 Agent Gateway”“Harness 为每域长期持有一份 Agent Token”“每个项目自行维护会话和 Agent Grant”“所有正式写入必须页面审核”作为未来架构的要求。它不撤销现有尚未迁移接口的拒绝规则。

## 3. 统一 Principal 与权限模型

### 3.1 稳定身份

中央以 `(issuer, subject)` 唯一映射 `user_id`；用户名、邮箱、组名不作为资源 owner 主键。迁移建立 `(domain_instance, legacy_user_id) → user_id` 表，不重写历史业务主键；禁止仅凭同邮箱或同用户名自动合并账号。历史无 OIDC 归属的单用户库通过受控迁移清单明确绑定，无法确定的映射不启用写权限。

统一 Principal 包含：`principal_id`、`principal_type`（user/agent/workload/device）、可选 `user_id`、`client_id`、`agent_id`、`session_ref`、`delegation_ref`、`audience`、`domain_instance`、capabilities、资源范围、披露策略、授权版本和有效期。单纯 workload 不自动附带用户身份；Agent 必须通过当前用户委托或已登记任务授权取得用户范围。

一次有效权限为以下交集，任何层都只能收紧：

```text
有效主体与会话
∩ 中央应用/能力策略
∩ 用户当前意图或 standing delegation
∩ 当前 Profile 与 Skill 选择
∩ 目标 audience / instance / resource scope
∩ 领域当前 owner / member / visibility / source 规则
```

中央不复制全部 Trip、Record、健康正文或领域成员表。资源委托只存受限选择器（exact refs、某 owner 的指定资源类型、明确 workspace 范围）、动作和版本；领域在实际事务中验证资源归属与成员角色。列表/搜索必须在分页、计数和聚合之前按授权范围过滤，不能先拿全量结果再在 Host 隐藏。

### 3.2 一份 Agent 身份，多份短时执行票据

Agent ID 表示稳定执行主体，不再为 Health/Travel 分别创建同一人格的克隆账号。不同 Host/设备/worker 保持不同 workload 身份；同一 Agent 可被中央策略允许访问多个领域，但**每张执行票据只绑定一个 audience 和 instance**。

Agent 私人记录默认基于 current_intent；计划任务基于 standing_policy；真正高影响动作基于 inline_confirmation。临时意图不会变成永久授权。用户不必每次录入授权一次领域：已有应用准入与本人记录能力策略内，当前明确意图足够；首次建立长期共享/自动化范围才在原交互点配置。

## 4. 浏览器登录和中央 Session

保留 Identity 唯一 OIDC issuer、Code + PKCE 和每应用精确 client/callback。登录流程、OIDC client secret、PKCE verifier/state/nonce、身份映射和 Session 状态由 Platform 统一实现/存储。领域只挂载 SDK 的 `/auth/login|callback|logout` 路由，不再有自写 OIDC 验证器或会话表。

1. 应用 SDK 以自身 workload 身份向 Access 创建登录 transaction；Access 按已编译 app_id 选固定 client/callback，保存 PKCE、state、nonce 和浏览器绑定随机值的摘要。
2. SDK 在本站设置短期 HttpOnly、Secure 的 transaction Cookie，并重定向 Identity。return path 只允许本站相对路径，callback/issuer 不接受请求参数覆盖。
3. 回调先由 SDK 核对浏览器 transaction Cookie，再将 code/state/transaction 通过后端认证通道交给 Access。Access 原子领取 transaction，校验其 app_id、redirect URI 与期限，用中央保存的 verifier 兑换并完整验证 OIDC 结果。
4. Access 返回仅供该应用的随机 Session handle；SDK 设置 host-only Cookie。Cookie 不共享到整个父域，不暴露给前端 JS，不把 ID Token 当业务 Bearer。
5. 后续 SDK 以 app workload 向中央校验 Session，得到可信 user_id 与准入信息，再进入本地业务权限检查。API 过期返回 JSON 401，页面才跳登录，避免 XHR 收到登录 HTML。

退出本站撤销该 app Session；退出全部应用/账号禁用提升中央 session epoch 并撤销全部相关会话、用户委托与未领取票据。各应用清自己的 Cookie/缓存，不能声称已注销 Identity SSO，除非 Identity 的会话结束也实际成功。

当前 Health 代理鉴权和 Nexus 代理头只是迁移源；新中央模式不能凭客户端 `Remote-User`/`X-Forwarded-User` 建立 Principal。受信代理必须清理这些头，SDK 不把同源/回环地址本身当登录证明。

标准依据：登录校验沿用 [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)；PKCE、精确重定向和令牌权限约束参考 [OAuth 安全最佳实践 RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)。中央 transaction/Session API 是本项目设计，不宣称它是新的 OIDC 协议。

## 5. Access 接口与执行票据

以下为拟议逻辑 API，路径均待 Schema 实现，不能在旧部署直接调用。

| API | 谁调用 | 输入与结果 |
| --- | --- | --- |
| `/access/v1/login-transactions`、`/exchange` | 应用 SDK | app_id、受限 return path/回调交易 → 授权跳转或 app-bound Session |
| `/access/v1/sessions/check`、`/revoke` | 应用 SDK | Session handle + 调用应用身份 → Principal / 撤销结果 |
| `/access/v1/delegations` | Nexus/管理入口的用户会话 | 资源、能力、期限、任务/共享范围 → delegation_ref；更改权限需原位确认 |
| `/access/v1/intents` | 获准登记意图的 Host | 真实 session/turn 来源、command/group/item、参数 hash → intent_ref |
| `/access/v1/authorize` | Host/Runtime/已登记 worker | capability、instance、目标、规范化 hash、intent/delegation → 短时票据或精确拒绝 |
| `/access/v1/introspect`、`/claim` | 目标领域 SDK | opaque ticket + 目标操作指纹 → verified Principal、decision_ref、执行许可 |
| `/access/v1/confirmations`、`/confirm` | 当前用户会话 | 精确动作/快照 → 有限期授权决定；模型不得调用 confirm |
| `/access/v1/revoke` | 授权用户/管理员 | 主体、Session 或 delegation_ref → 新 epoch 和审计事件 |

使用高熵 opaque 内部票据，中央只保存摘要，不把它包装成 Identity ID Token，也不伪称已实现 OAuth token exchange。外部 Host 首阶段通过中央登记的最小权限连接身份接入统一 MCP；需要标准 OAuth 自动发现时，由唯一 Identity 体系适配后另行验收，不在各领域造授权服务器。在线检查设计参考 [RFC 7662](https://www.rfc-editor.org/rfc/rfc7662.html#section-2)，内部扩展合同明确版本和调用方认证。

建议初始界限：执行票据最长 60 秒；确认授权最长 5 分钟；登录 transaction 5 分钟且单次；只读授权缓存不超过 30 秒且不超过原凭据期限。它们是待测的配置默认值，不是现有保证。写入及敏感原文读取每次 claim/introspect 获取新决定，不使用离线“永远有效”的签名快照。

`authorize` 不能只信任任意 `user_id` 或“用户同意”的文本。Host 登记 intent 必须持有对应有效 Session 上下文，并校验实际用户 turn/原生提交事件；票据绑定该 Host、audience、capability、instance、command ID、参数 hash、资源范围、授权/合同版本。跨域下一步重新申请票据，禁止转发上一域 Token。

## 6. 撤销、故障与单次执行

- Access 是权限唯一写入点。管理页在 Platform/Nexus，领域可深链带目标资源打开它，但不能再自己写 grant 表。中央授权受领域当前 ACL 的二次约束，成员撤销不依赖异步复制才生效。
- 写入以目标领域取得 claim 的时点为授权线性化点：在它之前撤销必拒绝；之后已开始的事务可能完成，不能承诺撤销可回滚已提交事实。claimed 许可也有短期开始期限，任务每个新副作用阶段重新授权。
- 中央以票据/decision + command_id 原子 claim；同 command 同 hash 可重复领取原决定，换键或换内容拒绝。领域以 `(owner, instance, operation, command_id)` 唯一约束，在本地事务保存变更和 Receipt，故障不造成“授权消费了但不能恢复”。
- 中央 claim 与领域业务提交不组成分布式事务。Host 崩溃后先查询领域 operation；重新领取或查询授权都沿用原命令。已成功结果的读取重新验证当前读取权限，历史成功不等于仍有权读内容。
- Access 故障：未领取写入停止并返回 `auth_unavailable`；已开始的领域事务可以完成；只读至多使用未过期且符合披露策略的缓存。静态公开页面、采集到本机加密队列和已声明的领域内部确定性维护不因此伪报失败，但不得获得新 Agent 权限。
- 撤销通知用于加速清缓存，不承担唯一正确性。明确记录只读最长 30 秒窗口；中央权限撤销完成与远端事务完成分别显示，不宣称零延迟全局回滚。
- 凭据轮换、Access 数据恢复、epoch 单调性属于 Platform 发布门禁。恢复旧授权库后必须提升全局 epoch/作废旧票据及会话，不能使已撤销权限复活。

## 7. Agent、MCP、模型和任务统一

### 7.1 工具与运行时

中央 Gateway 从版本化 Manifest/OpenAPI/Surface 编译单一目录。目录按 user/Agent、Profile、delegation、disclosure 和 contract hash 裁剪并生成 catalog_version；调用时重查权限，不信任客户端提交旧工具名或目录缓存。Skill 影响可见能力，不是授权凭证。

Nexus 会话复用 DSH loop。领域页面助手与外部 MCP 进入相同 Runtime/工具执行适配，领域只提供 deterministic handler、读取/写入参数 Schema、Prompt/Skill 和评测。已有 Health/Ledger MCP 进入兼容期，停止添加独立 OAuth、registry、Skill 授权解释器与审批流程；迁移后注销旧连接而不留下备用绕过入口。

普通动作由模型提出命令，隐藏 Host executor 验证 current_intent 并申请票据。高影响动作由同一中央确认组件处理；任何前端确认按钮只提交用户事件，不自己生成签名。Platform 复用当前 confirmation SDK，但签发权/密钥迁入中央，不让每个领域维护不同签名流程。

### 7.2 模型接入

新增统一 Model Gateway 管理 Provider 连接、秘密、模型别名、调用预算、超时、流式传输和 fallback。复用现有 `llm_client.py` 的传输能力作为中央实现；各域改用同一 SDK，请求只声明 prompt_template/version、必要输入、输出 Schema、用途、披露范围和预算上限。

领域拥有业务 Prompt 与 eval，中央不改写健康/金融口径。通用聊天历史与 Agent loop 只保留一份；研究任务可以反复调用中央模型服务，但不能各自再实现一套通用工具路由/授权循环。

与旧“Platform 不接触 Prompt”约定不同，Model/Agent Gateway 会在处理时接触获准输入和输出。默认不持久记录正文，不把全文放审计；短期恢复材料设置用途、加密与删除期限。敏感数据只能发往已授权的 provider/地域/用途；fallback 不自动扩大数据披露，缺条件直接返回不可用。确认页是否展示正文与模型是否可读是两种权限。

确定性 Health 周报、Ledger 查询、Foliant PIT/数值计算留在领域，不经模型。中央预算使用 reservation + 实际 usage 结算；取消/超时也核对已消耗请求，不双重计算已重放结果；费用上界未知时不伪造精确金额。

### 7.3 长任务

领域保留现有 worker、lease、重试和任务数据。统一 task/run 引用、状态、取消、进度及回执；中央仅记录关联和必要运行元数据，不搬走任务数据库。

Agent 发起任务绑定 user、delegation、instance、capability、输入 hash 和预算。等待模型/工具时不跨网络持有业务数据库锁；重新进入副作用阶段时重查当前授权及业务版本。全局停用 Agent 阻止新阶段，已保存原件/已完成交易记录不会被删除。

## 8. 接入协议与最小领域适配

拟议公共合同为 `shadow.command.v1`、`shadow.execution-result.v1`、`shadow.access-context.v1` 和 Runtime v2。保留旧 `effect/risk_level/confirmation` 枚举，在版本化 `execution` 对象增加 interaction、effect_scope、reversibility、result_kind、authorization_mode，禁止直接改旧枚举含义。

命令包含 command/group/item ID、capability、operation、instance、类型化 arguments、目标及 expected_revision、source_refs。可信 Access Context 由 SDK 提供，绝不作为模型可自由填写的业务字段。

结果明确 `committed/accepted/failed`；Host 的 `reconciling` 表示未知，不能用 HTTP 200、字符串 URI 或草稿 ID 推断完成。committed 持久关联 Receipt/资源版本；accepted 必须有 task/status 引用；状态查询可按 command ID 或同键重放，声明可靠的幂等窗口。

Nexus 持久化 prepared 后再发送；参数 hash 不能替代恢复参数的来源。临时规范化命令加密保存，成功后按保留期清理。旧 Proposal 语义去重不能吞掉两次字段相同的真实录入。

一个领域最低只需：共享 auth middleware + 已编译 capability handler 表 + 本地 resource_check + 事务应用服务 + operation/result 查询 + Surface。公开 health 与受保护 readiness 继续分开。新域不必实现 Review CRUD、Agent 管理 UI、MCP server 或模型 transport。

授权/调用审计统一记录 user/Agent/workload、decision_ref、command、capability、instance、时间和结果；领域保留事实审计。中央不复制 before/after 正文，领域不复制中央 policy/approval 数据库。授权、执行、数据证据分别标识，不能把确认凭证冒充执行 Receipt。

## 9. 迁移与兼容矩阵

| 阶段 | 权限唯一来源 | 领域行为 | 开放条件 |
| --- | --- | --- | --- |
| M0 现状盘点 | 现有 registry/grant | 原有拒绝照常 | 导出脱敏 ID/范围/版本映射，不导出秘密或业务正文 |
| M1 中央影子判定 | 旧模式仍权威；新模式不执行 | 同一合成请求比较两种决定，不发两次业务命令 | 无扩大授权差异；owner/audience/披露等价 |
| M2 单能力切换 | 对该 instance/capability 原子指定 central | 只接受中央票据；旧路径对该能力拒绝 | 身份映射、撤销、Receipt/恢复和合同 fixture 通过 |
| M3 域完成迁移 | 中央 | 停止写本地 Agent grant；注销旧 MCP/Token 验证，保留历史引用 | 新旧 Session/任务有截止与查询方案 |
| M4 清理 | 中央 | 删除不再使用的认证实现/配置；业务 ACL 与 Receipt 保留 | 迁移窗口结束、备份及旧主体零调用证据 |

旧允许项不自动取并集。active、资源类型、scope、expiry、owner、disclosure 的交集可迁移；过期/停用不导入为 active，审批资格不变成普通写入的无限授权。审批历史只迁移引用，不把历史批准变成新票据。

每个 capability 的 `auth_mode=legacy|central` 由受审查的 release 设置，服务启动校验与实际路由一致。不得采用“先中央失败再试旧 Token”的自动 fallback；中央故障不触发授权降级。数据库只做加法迁移，回退暂停新写能力并继续查结果，不删历史事实/批准记录。

Access 模式、SDK/runtime/合同版本、目标服务版本握手列入 Profile lock 与 capability evidence。release 原子链接切换不是跨服务器事务；先部署可同时读旧/新合同的代码，再按 capability 切换。deployed/observed 在相应阶段采集，不要求新 build 发布前具备自己的线上 observed。

## 10. 工作包与验收

| 工作包 | 交付物 | 前置/验收 |
| --- | --- | --- |
| UA-01 Identity/Access | 中央身份映射、Session、主体/委托注册、票据/claim/撤销、SDK | 错误 issuer/audience、Session 混用、过期/撤销、影子对比与恢复失效通过 |
| UA-02 Runtime/Gateway | 工具目录、共享 executor、内联确认、MCP 接入、预算 | 模型不可签发授权；目录变化/提示注入/跨域透传拒绝 |
| UA-03 Command/Receipt | Schema/生成 validator、Journal、结果查询 | 写前持久、并发双击、丢响应、同键异内容和跨 owner 测试 |
| UA-04 Health 试点 | 体重 → 一餐普通命令，统一 Session/Agent | 无二次审核，保留来源保护，真实服务读回 |
| UA-05 Ledger/Travel | 中央 delegation + 普通事实/计划执行 | 旧 428/403 不绕过；模型不扩大 owner/Trip 范围 |
| UA-06 Archive/Garden/Foliant | 正式归档、草稿/发布拆分、研究任务/交易事实接入 | accepted/committed 正确，公开发布精确确认，预算/数据披露可核验 |
| UA-07 App/模型迁移 | owner 队列、设备注册、统一模型 transport、旧入口注销 | 断网/跨日/换账号/旧 callback、provider 拒绝与预算回收 |

每个工作包都必须有未授权、权限撤回、跨实例、版本冲突、日志脱敏和 Access 不可用的负向 fixture。先合成数据和隔离数据库，再真实浏览器/设备，最后在用户明确要求部署后采集运行证据。本次文档完成不等于上述工作包已完成。

## 11. 领域设计索引

- [Nexus](https://github.com/Cylunex/shadow-nexus/blob/main/docs/nexus-integration-design.md)
- [Health](https://github.com/Cylunex/shadow-health/blob/main/docs/nexus-integration-design.md)
- [Ledger](https://github.com/Cylunex/shadow-ledger/blob/main/docs/nexus-integration-design.md)
- [Travel](https://github.com/Cylunex/shadow-travel/blob/main/docs/nexus-integration-design.md)
- [Archive](https://github.com/Cylunex/shadow-archive/blob/main/docs/nexus-integration-design.md)
- [Garden](https://github.com/Cylunex/shadow-garden/blob/main/docs/nexus-integration-design.md)
- [Foliant](https://github.com/Cylunex/shadow-foliant/blob/main/docs/nexus-integration-design.md)
- [App](https://github.com/Cylunex/shadow-app/blob/main/docs/nexus-integration-design.md)

领域文档只补业务差异，不复制或重定义本规范的 Principal、票据、确认或 Session 格式。具体实现发布时锁定合同版本/hash；GitHub main 文档链接仅作设计索引，不是运行时依赖。

## 12. 冲突处置清单

| 冲突来源 | 统一后的目标 | 兼容处理 |
| --- | --- | --- |
| Platform 旧 agent-access/unified-agent 的“无 Gateway/各域长期凭据” | 中央 Access/Agent/Model 入口，单 audience 短票据 | 旧配置归档 `docs/legacy/`；不当新接入规范 |
| 应用接入“各项目实现 OIDC/维护 Session” | 中央会话真相与共享 SDK 路由 | 旧本地表迁移/到期后停止使用，不删业务 user 外键 |
| Health JSON profile_grants 与 Archive YAML owner_id | 中央统一 Principal/delegation | 导入核验 ID/范围，不按名字猜 owner |
| Ledger ADR 0010 本地批准与旧 allow_confirm | ADR 0011 普通 current_intent、中央高影响确认 | 历史凭证保留；旧 428 在旧接口仍有效 |
| Travel v2 必须浏览器 Owner commit | 新普通命令验证中央票据并执行 v2 应用服务 | 旧 403 保留；不借 v1 commit 绕过 |
| Garden 保存草稿和发布混用 commit | 两个独立 capability/结果 | 发布仍精确确认，不把风险等级直接下调 |
| Archive capture 仅 ImportDraft | 新正式 capture 与派生 task 分开 | 旧草稿不冒充正式档案 |
| Foliant trade 导入易被当成下单/全局组合当多租户 | 历史成交事实、研究/模拟、真实执行分开 | 保持唯一组合 owner 与旧角色限制 |
| 各域内置 AI、MCP、工具目录、Provider wrapper | 中央通用 Runtime/MCP/Model + 领域 handler/template | 停止重复迭代，按能力注销旧连接 |
| 对所有写入套人工审核 / Review Center | 普通直执行、高影响原位确认、异常原位处理 | 旧协议可读，不自动执行历史 pending |
| Nexus/App 两套离线 ID、owner 缺失 | 稳定 command ID、owner/device/instance 分区 | 无 owner 旧队列隔离，不能自动归属当前账号 |
| 删除本地 grant 被理解成取消所有资源校验 | 中央 Agent 委托 + 领域当前 ACL/来源校验 | 业务 ACL 不删除，中央允许不覆盖领域拒绝 |

上述冲突文档均有目标优先级说明；涉及 SDK/Schema/Manifest/路由的运行合同等对应代码完成后同步升级，本次不把未实现 API 写进可执行配置或放宽现网权限。

## 13. 公共报文与错误约定（拟议）

下例仅说明未来合同字段；尚未提交为可执行 JSON Schema，也不是当前 endpoint 的请求体。真实权限来自 SDK 验证的独立票据，不接受把 Principal 塞在业务 JSON 里。

```json
{
  "protocol": "shadow.command.v1",
  "command_id": "cmd_example_001",
  "group_id": "group_example_001",
  "item_id": "item_1",
  "capability_ref": "shadow://capabilities/shadow-health/health-example/health.records.write",
  "operation_id": "metric_record",
  "instance_id": "health-example",
  "contract_version": "UA-1",
  "target_refs": ["shadow://health/profiles/profile-example"],
  "arguments": {
    "effective_date": "2026-09-07",
    "timezone": "Asia/Shanghai",
    "metric": "weight_kg",
    "value": "70.2",
    "source_kind": "user_reported"
  },
  "source_refs": []
}
```

创建对象可以没有 expected_revision；更新必须按领域合同提供。Host 从实际领域预览或受控规范化服务得到最终参数，冻结后再申请对应 hash 的授权。模型猜测的 hash 不可信；规范化改变含义或高影响参数时原授权失效。

hash 输入明确包含 protocol/contract、operation、instance、业务 arguments、目标和 expected_revision；不含 trace/request ID、授权票据或临时状态。Decimal 用规范字符串，时间含明确时区，列表顺序有业务含义则保留；对象键排序、UTF-8、空值/省略区别及 Unicode 处理由公共 canonical fixture 锁定，不让 Python/JS 各自实现“差不多的 JSON”。

```json
{
  "protocol": "shadow.execution-result.v1",
  "command_id": "cmd_example_001",
  "status": "committed",
  "result_kind": "record",
  "receipt_ref": "shadow://health/operations/cmd_example_001",
  "result_refs": ["shadow://health/metrics/2026-09-07"],
  "result_revision": "revision-example",
  "authorization_ref": "decision-example",
  "replayed": false,
  "completed_at": "2026-09-07T04:00:00Z",
  "display": {"summary": "已记录体重 70.2 kg"}
}
```

`receipt_ref` 只在领域真正持久记录过结果时返回；示例 URI 不授予读取权。`display` 是领域可展示投影，Host 仍校验 Schema、字节预算和敏感字段许可，不把其中的 HTML/工具建议执行为指令。领域 Result 不得包含 Token、Cookie、签名下载 URL 或未授权原文。

| code | 语义 | Host 动作 |
| --- | --- | --- |
| `unauthenticated` / `permission_denied` | 主体失效/明确不允许 | 停止；登录恢复或解释权限，不尝试别的 Token |
| `auth_unavailable` | 无法取得中央权威决定 | 新写入不执行，不误显示权限已撤销 |
| `missing_fact` / `validation_failed` | 参数不完整/领域拒绝 | 原位问必要事实，保留其他已成功项 |
| `version_conflict` / `catalog_changed` | 资源或合同上下文改变 | 刷新目标/目录，重新生成受控命令 |
| `confirmation_required` | 实际后果需要新确认 | 原位调用中央确认，模型不可消费用户按钮事件 |
| `idempotency_conflict` | 同 command/key 不同内容 | 拒绝；明确修正才创建新命令 |
| `transport_unknown` / `invalid_result` | 不能判断是否提交 | reconciling，先查询领域 operation |
| `retryable_not_applied` | 领域明确未发生副作用 | 在原键/授权有效范围内有界重试 |

HTTP status 只用于传输层处理，错误 code 与操作状态才决定能否重试。状态查询的“未受理”必须来自权威 operation store，附可重放窗口；普通 404、缓存 miss 或服务暂不可用不构成未执行证明。

每个调用实例仍需要自身 workload 身份材料，这是中央签发/轮换的一份实例凭据，不是每域维护用户/Agent registry。Access 的 introspect/claim 也要认证目标 workload 并验证其 audience；禁止任意已登录应用检查别域票据或签发另一 owner 的 Principal。初始机器登记、恢复与轮换由统一运维入口完成，不分散到九个设置页面。
