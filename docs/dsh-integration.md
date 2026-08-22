# DeepSeek Harness 接入

## 1. 定位

DSH 是 Shadow 当前的第一参考 Agent Runtime。Shadow 合同保持运行时中立，Platform 将选定
Profile 编译成 DSH 原生 Bundle；领域项目不导入 Cordis 或 DSH 依赖。

当前接入基线是 DSH `0.1.1` 版本线；已在 Linux + Node 22 环境核对的精确运行时为
`@deepseek-ai/dsh@0.1.1-rc.2` 与 `@deepseek-ai/dsh-tools@0.1.1-rc.2`。Profile 必须分别锁定
`distribution_version` 和 `tools_api_version`，实际部署前再与目标机解析版本比对。

生成的 Bundle 使用 DSH 标准 `package.json` 中的 `dsh.bundle.patch` 和 `cordis.patch.yml`，
每个领域实例对应独立 Cordis Plugin fiber：

```text
dsh-shadow-general/
├── package.json
├── cordis.patch.yml
├── agent-bundle.lock
├── domain.js
├── policy.js
├── runtime.js
├── shadow-runtime-manifest.json
└── profile.generated.js
```

`domain.js` 将 OpenAPI operation 编译成 `defineTool()`，输入和输出使用 DSH Schema DSL；Skill
通过 `ctx.skills.register()` 进入原生 Skill Catalog。`policy.js` 使用 `tools/pre-execute` 和
`ctx.tools.guard()` 实现分级策略。

Builder 会复制每个 Skill 所在目录，而不只嵌入 `SKILL.md`。Skill 中按需引用的
`references/`、`scripts/` 和 `assets/` 会随 Bundle 发布，并通过 DSH `resourceBase` 解析。

## 2. 构建

测试 fixture 的构建命令：

```bash
.venv/bin/python scripts/build_dsh_bundle.py \
  --profile fixtures/conformance-profile.yml \
  --instances fixtures/conformance-instances.yml \
  --plugin-root fixtures/conformance-plugin \
  --output-dir build/dsh
```

安装 Platform wheel 后可直接使用 `shadow-plugin-validate <plugin-root>` 与
`shadow-dsh-build`，参数与仓库脚本一致。

生产构建必须固定 DSH 发行版及 Tools API 版本，设置 `SOURCE_DATE_EPOCH`，并从部署环境注入
`base_url_env` 与 `credential_env` 指向的值。构建产物不得提交真实地址、Token 或用户数据。
生成包只把 `@deepseek-ai/dsh-tools` 声明为 peer dependency；不得改成普通 dependency，避免
Profile 本地安装第二份宿主包并破坏 Cordis 服务实例一致性。

开发环境可以安装本地预构建包进行检查：

```bash
dsh --profile shadow-general --dump-config
dsh plugin --profile shadow-general add ./dsh-shadow-general.tgz
```

生产发布不在运行中的 Profile 里临时执行安装。Platform 在隔离的 DSH_HOME 中组合固定版本
Profile，完成 `dump-config`、启动和无模型 smoke test 后，再原子切换运行目录并保留旧版本
回滚。发布使用预构建 tarball，不允许在服务器上从未知 Git 依赖执行构建脚本。

运行时本身也必须来自稳定、可复现的安装目录和锁文件。Supervisor/systemd 不应长期指向
`npx` 临时缓存路径；缓存清理或重新解析依赖会让相同命令启动不同代码。上线前记录
DSH、Tools API、Node、pnpm 和 Profile lock 摘要。

## 3. 运行期链路

```text
DSH Tool
  → Shadow Policy
  → DSH Approval（需要时只允许一次）
  → Shadow monotonic guard
  → 领域 HTTP/MCP
  → 有界结构化结果
  → DSH Session
```

领域凭据来自插件环境，不进入模型参数。请求使用 DSH call id 派生 request id 和幂等键，网络
失败不返回领域响应正文。`summary` 结果只向模型渲染摘要、可选引用和 continuation；
`reference` 结果只渲染引用及可选摘要。领域完整 JSON 受 `max_result_bytes` 限制，模型内容另受
`max_model_chars` 限制。Profile Builder 同时限制全部 Tool/Skill Catalog 的总字符预算。

## 4. 确认边界

DSH 原生 Approval 只提供一次性的允许或拒绝，所以长期预授权由 Shadow Profile Policy 编译：

- L0/L1 继续执行，不弹窗；
- L2 在 capability 已预授权时继续执行，否则返回 `ask`；
- L3 始终 `ask`；
- L4 在普通 Profile 中由 guard 拒绝。

普通 Bundle 对发布、批量删除和资金执行优先暴露 `*.propose`，最终应用仍由领域服务或
Shadow App 持有的确认凭证完成。

通用 `ConfirmationReceipt` 合同位于 `contracts/confirmation-receipt.schema.json`。它只用于
L3/L4 最终执行并绑定参数摘要、资源、actor、有效期和单次 nonce。当前 Adapter 只落实 DSH
会话级 Approval；在第一个 L3 最终执行能力开放前，必须完成回执签发、传递、验签与防重放。

## 5. 参考插件结论

对照 DSH 现有插件后，Shadow 采用以下组合：

| 参考类型 | 可复用做法 | Shadow 取舍 |
| --- | --- | --- |
| QQBot / Lark 渠道 | `inject` 宿主服务、按聊天隔离 Session、把审批送回用户通道 | 只作为未来入口插件；不承载领域事实或共用万能凭据 |
| SpecFlow 工作流 | Bundle patch 极小、Skill 与资源随包发布、命令/Goal/Tool 分工、预构建 tarball | 领域 Skill 使用相同发布方式，Prompt 与 Eval 仍留在项目 |
| Guardian 策略 | pre-execute 取最严格决策、post-execute 处理 canonical value、策略不冒充沙箱 | Shadow 分级策略独立于 Tool；领域服务继续做最终授权和脱敏 |
| DSH 官方 Tool | `defineTool` canonical output、cooperative timeout、effect-backed 注册 | Builder 直接生成原生 Tool，不建立自有 Agent loop 或代理协议 |

因此领域插件不是一个新的聊天机器人，也不是把业务服务搬进 DSH 进程。它是可卸载的
Cordis 适配层：注册 Skill 和 Tool、应用 Profile 策略，然后直接调用独立领域 API。

## 6. 当前实现边界

P0 Builder 已实现 HTTP/OpenAPI 到 DSH 原生 Tool 的确定性编译，并为同一 Profile 生成单一
通用 Bundle；每个领域由 `instanceId` 精确绑定 Cordis 配置。合同已经预留 MCP，但 MCP
Adapter 尚未实现，构建时会明确拒绝 MCP 工具，不能静默降级为通用 HTTP 调用。Health 等项目
接入前应完成每 MCP Server 一个 Cordis Plugin 实例的适配和相同的策略、结果边界测试。

`hidden` 工具不会注册进 DSH。`on-demand` 工具当前仍会注册；按 Skill 动态替换
agent-scope 的 `ctx.tools.restrict()` 仍属于下一阶段，因此 `on-demand` 只是发现策略，不能
被当作权限边界。

正式 Profile 不安装 Dynamic Extension 或外部市场入口。这里通过 Profile 包组成和内部
allowlist 实现，而不是依赖尚未验证的假定配置字段。领域不可用不会阻止 Adapter 注册，失败
只发生在对应 Tool 调用；构建期合同错误则在发布前整体拒绝。

## 7. 升级策略

DSH 处于快速演进阶段，Profile 必须分别固定准确的发行版与宿主 API 版本。升级流程为：确认
安装实际解析的宿主包版本、生成新 Bundle、合同测试、无模型 smoke test、真实模型只读测试、
Session 恢复测试，再原子切换。`agent-bundle.lock` 同时记录两个版本；旧 Bundle 与 Session
备份保留到回滚窗口结束，禁止直接跟随 `latest`。
