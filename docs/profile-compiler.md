# Shadow Profile Compiler

Shadow 的部署真相由四类输入共同组成：Canonical Deployment、App Catalog、Plugin Instance
Registry 和 Agent Profile。领域仓库只声明一次 Plugin Definition、能力合同和 Surface；Platform
将同一份来源编译为不同运行端需要的投影，不再为 Nexus 或 App 人工维护第二套领域清单。

```text
Plugin Definition + Deployment + Catalog + Instance + Profile
                              │
                              ▼
                    Shadow Profile Compiler
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
       DSH Runtime       Nexus Runtime      App Runtime
             └────────────────┼────────────────┘
                              ▼
                    shadow-deployment.lock
```

## 输入边界

- `shadow-plugin.yaml`：稳定插件身份、版本和描述符引用；
- `agent/manifest.yaml` 与 OpenAPI/MCP：能力语义和机器接口；
- `contracts/surfaces.yaml`：展示信息、Summary、Capture、Review、Search 和 App Link；
- Deployment：Canonical Product ID，以及该产品进入 DSH、Nexus、App 中的哪些通道；
- Instance：只保存真实地址和凭据对应的环境变量名；
- App Catalog：移动入口、认证模式、健康检查和可信别名；
- Profile：只选择允许模型看见的能力。Nexus Profile 默认只包含读取/分析能力，写入走隐藏
  Host Review 通道。

真实地址、Token、IP、端口和证书不属于任何编译输入文件，运行时仍从仓库外环境注入。

## 编译产物

```text
<output>/<deployment-id>/<build-id>/
├── dsh/<profile-id>/
├── shadow-dsh-runtime.json
├── shadow-nexus-runtime.json
├── shadow-app-runtime.json
└── shadow-deployment.lock
```

- DSH 投影包含 Tools、Skills、Policy 和运行时映射；
- Nexus 投影包含动态领域、连接变量名、Surface、标准 Review 操作和 Catalog 提供的应用入口；
- App 投影包含可信 Web 入口、别名、图标、顺序和原生能力边界；
- Lock 记录所有输入摘要、Canonical Identity Map、三个输出摘要和 DSH Bundle 树摘要。

构建目录以 `build-id` 不可变保存。相同输入复用同一 Release；不同输入创建新 Release，不覆盖
旧版本，因此回滚不需要重新构建。

## 构建、校验与激活

```bash
shadow-profile-build \
  --deployment agents/deployment.yml \
  --catalog catalog/apps.yml \
  --profile agents/profiles/shadow-nexus.yml \
  --instances agents/plugin-instances.yml \
  --plugin-root /srv/shadow-health \
  --plugin-root /srv/shadow-ledger \
  --output-dir /srv/shadow/releases

shadow-profile-activate \
  --release-dir /srv/shadow/releases/<deployment>/<build-id>

shadow-profile-activate \
  --release-dir /srv/shadow/releases/<deployment>/<build-id> \
  --current-link /srv/shadow/runtime/current
```

不传 `--current-link` 时只做完整性校验。激活前会校验 Lock、三个投影和 DSH Bundle；通过后用
同一文件系统内的符号链接原子切换 `current`。回滚就是对上一 Release 重复执行激活命令。
运行进程应从 `current` 读取，但启动日志必须记录解析后的 `deployment_id` 与 `build_id`。

## 一致性与降级

- Product/Plugin/Instance/App/Module ID 漂移在构建期失败；
- 插件版本与 Instance 不一致在构建期失败；
- 声明 Nexus 通道却没有 Surface，或 Surface 引用不存在的 Capability/Operation 时失败；
- Profile 移除一个产品后，三个投影会按通道同步移除；
- 凭据缺失是单领域运行时 `degraded`，不允许回退成匿名调用，也不阻断其他领域；
- `shadow://` URI 与部署拓扑无关，投影不保存领域业务事实。

生产切换前仍需完成 DSH `dump-config`、无模型 smoke test、Nexus Runtime 加载、App Catalog
校验和领域 `/healthz`/`/readyz` 检查。编译成功只证明装配一致，不替代服务健康检查。
