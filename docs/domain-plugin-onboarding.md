# 领域项目插件化接入清单

## 1. 接入目标

领域项目先是一套可以独立登录、部署、升级和使用的完整应用，再通过 Shadow Plugin 合同暴露
有限能力。Platform 只校验、注册和编译合同；DSH 使用领域专属凭据直接访问项目 API，不经过
Platform 转发业务流量。

首批顺序为 Travel、Archive、Health、Ledger。Verse 与 Wingman 不在当前范围。

## 2. 项目目录

```text
shadow-<domain>/
├── shadow-plugin.yaml
├── agent/
│   ├── manifest.yaml
│   ├── skills/
│   │   └── <skill-id>/
│   │       ├── SKILL.md
│   │       ├── references/
│   │       ├── scripts/
│   │       └── assets/
│   ├── prompts/
│   └── evals/
└── contracts/
    ├── agent.openapi.yaml
    ├── events.yaml
    ├── resources.yaml
    ├── captures.yaml
    ├── confirmations.yaml
    └── permissions.yaml
```

MCP 项目另提供静态 `mcp-tools.json`；Composition Plugin 则提供
`contracts/workflow.yaml`，不提供自己的数据库或通用转发服务。

`prompts/` 和 `evals/` 仍由项目自己运行，默认不进入 DSH Bundle。Skill 目录会被整体打包，
相对资源通过 DSH `resourceBase` 读取，不能引用开发机绝对路径或目录外文件。Builder 拒绝
符号链接、隐藏文件、常见私钥扩展名、超过 10 MiB 的单文件和超过 50 MiB 的 Skill 目录。

## 3. 能力设计顺序

1. 先定义稳定的业务 capability，例如 `travel.routes.draft`，不要从现有函数名反推能力。
2. 给 capability 标注 audience、scope、数据等级、effect、L0-L4、确认、可撤销性和幂等性。
3. 用 OpenAPI operation 描述确定性 HTTP 输入输出；模型不能直接拼内部数据库请求。
4. 再编写 Skill，说明何时调用、调用顺序、失败降级和事实边界。
5. 最后配置 Profile，只选择当前场景需要的插件实例与能力。

`hidden` 工具不会进入 DSH；`on-demand` 工具只在对应 Skill 成功载入后进入当前 Agent 的可见
工具集合，并能从 Session 历史恢复。它仍只是模型可见性控制，不能替代 scope、资源授权或
服务端权限检查。

## 4. DSH 适配要求

领域项目不手写或发布 DSH npm 包。Platform 为一个 Profile 生成一份通用 Bundle，每个领域
实例只形成一条 Cordis 配置。生成产物必须满足：

- `package.json` 声明 `dsh.bundle.patch`，patch 只插入本插件拥有的 Cordis 行；
- 插件入口使用命名导出 `name`、`inject`、`Config`（需要配置时）和 `apply`；
- 通过 `ctx.tools.register()`、`ctx.skills.register()` 等 effect-backed API 注册，卸载时自动回收；
- `@deepseek-ai/dsh-tools` 宿主包放 `peerDependencies`；官方 MCP Client 在使用 MCP 时作为精确
  锁定的普通 dependency；
- 声明 Node engine，首期基线为 `^22.19.0 || >=24.0.0`；
- 发布预构建 tarball，生产安装不依赖 `prepare`、Git checkout 或在线 TypeScript 编译；
- Profile 精确锁定 DSH 与 Tools API，Plugin 声明兼容范围，Builder 在生成前强制校验；
- `agent-bundle.lock` 记录输入摘要、插件版本、实例、运行时版本、确定性 build id 和包版本。
- `shadow-runtime-manifest.json` 记录 capability、Shadow Tool 与 DSH Tool 的可审计映射；
- Profile 对 Tool Catalog 和 Skill Catalog 设置总字符预算，每个 Tool 另设模型结果预算。

Profile 的 Bundle 层先于 Profile 自己的 `cordis.patch.yml` 应用，因此默认配置应安全且最小，
环境差异留给 Profile 覆盖。真实地址和凭据只由环境变量或受限配置文件提供。

## 5. Tool 实现要求

- `defineTool()` 的参数和 canonical output 必须与 OpenAPI 一致；
- `execute()` 转发 `exec.signal`，超时后必须收敛，不能遗留后台请求；
- 只有无共享可变状态的读取才设置 `isConcurrencySafe: true`；
- L1-L4 写操作使用 DSH pre-execute 与 monotonic guard，并由领域服务再次授权；
- 幂等写入使用 DSH call id 派生请求 ID 和幂等键；
- 错误只返回稳定分类和 HTTP 状态，不回显响应正文、Token、查询实值或个人数据；
- `summary` 和 `reference` 分别只渲染摘要或资源引用，同时执行响应字节和模型字符上限；
- 需要长期任务时使用领域任务资源或 DSH task seam，不让普通 Tool promise 无限运行。

MCP 不靠运行时发现结果生成合同。项目提交静态 Tool Catalog，`operation_id` 与 MCP 原始工具名
一致；每个 Server 在部署 Instance 中声明 streamable-http 或 stdio 配置。Shadow Wrapper 隐藏
底层 `mcp__*` 工具并统一应用 Profile、结果预算与确认策略。

L3/L4 领域接口必须验证 `ConfirmationReceipt`：校验签名算法和可信 key id、issuer、最长
15 分钟有效期、audience/plugin/capability/tool/effect、规范化参数 SHA-256、可选资源 URI，
再以持久化唯一 nonce 原子消费。相同 receipt + 相同幂等键可返回既有结果；换幂等键重放必须
拒绝。HTTP 从 `X-Shadow-Confirmation` 读取，MCP 从 Manifest 声明的保留参数读取。

参数摘要针对 Adapter 收到的完整 Tool 输入：HTTP 的 path/query 参数保持顶层，JSON 请求体放在
`body`；MCP 在注入保留回执参数前计算摘要，服务端验签时必须先移除该保留参数。双方统一使用
UTF-8、对象键排序、无空白、禁止 NaN/Infinity 的 JSON，再计算小写 SHA-256。

删除能力还要在服务端事务中根据真实 `total_before` 检查 `min_remaining >= 1`。确认回执只证明
用户批准了这组参数，不替代“不能删空”、外键约束、资源授权或业务审计。

## 6. 各项目首期边界

### Travel

- 先提供地点、地图、路线和到访记录的读取与草案能力；
- 地图访问除 scope 外必须校验资源级 grant；
- 坐标、地图供应商结果和路线事实由服务端校验，模型只给建议；
- 正式发布行程、共享地图和删除到访记录保持 L3。

### Archive

- 先提供归档搜索、条目摘要、来源追溯和导入草案；
- 原文件和大正文以 `shadow://` 资源引用返回，不直接灌入 Session；
- 导入、合并、移动和删除必须保留来源、幂等键及可恢复状态；
- 跨项目采集使用 Capture 合同，不让 Archive 直接写其他领域数据库。

### Health

- 使用独立 Profile 与独立会话存储，不与通用旅行会话混放；
- 默认只读、趋势分析和记录草案，敏感原始指标执行最小披露；
- 不把模型输出描述为诊断或治疗结论；
- 正式写入、批量修正和对外导出分别执行资源授权与明确确认。

### Ledger

- 使用独立财务 Profile，首期只做账户、账目、预算读取与记账草案；
- 金额、币种、账户和汇率由确定性代码校验；
- 正式入账至少 L2，导出或难撤销调整至少 L3；
- 资金执行保持 L4 且首期不向普通 Profile 暴露。

## 7. 每个项目的完成标准

项目接入不能只以“DSH 能看到工具”为完成。至少验证：

1. 独立应用在没有 DSH 时仍能完整运行；
2. Plugin Definition、Manifest、所有引用和兼容范围通过 Platform 校验；
3. 合并 Profile Bundle 两次构建逐字节一致，Skill 资源完整进入包；
4. `npm pack --dry-run` 只包含预期文件，且没有第二份 DSH 宿主依赖；
5. DSH `--dump-config` 能组成预期 Cordis 树，且每个领域实例故障彼此隔离；
6. 无模型 smoke test 覆盖工具注册、参数错误、超时、取消和结果边界；
7. 真实模型只读测试不会越权选择其他领域凭据；
8. L1-L4、401/403、资源 grant、确认拒绝、幂等重试和日志脱敏均有负向测试；
9. Profile 重启、Session 恢复、插件升级和回滚均可复现；
10. 生产运行时来自稳定安装目录和锁文件，不依赖临时包缓存。

通过以上检查后，才把该项目加入 Shadow 通用或领域专用 Profile。
