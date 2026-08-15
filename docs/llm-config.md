# LLM 统一配置（无代理）

## 目标

Shadow Platform 统一模型别名、供应商 Base URL、协议和密钥文件位置，但不接收或转发任何 LLM 请求。

```text
业务项目后端 -> registry 中的 provider base_url -> LLM 供应商
```

平台看不到提示词、图片、工具参数、流式响应或模型回答，也不会成为推理链路的单点故障。

## 配置与密钥

`llm/registry.yml` 保存非敏感配置并可进入 Git；真实文件由 `llm/registry.yml.example` 复制后维护。供应商密钥只保存在 secrets 目录。

默认采用每项目独立密钥：

```text
/etc/shadow-platform/secrets/llm/health/primary-api-key
/etc/shadow-platform/secrets/llm/foliant/primary-api-key
/etc/shadow-platform/secrets/llm/travel/primary-api-key
```

同一 Base URL 也应优先使用不同 Key，以便独立限额、撤销和统计。只有供应商确实无法创建多个 Key 时，才把 `credential_file` 配成显式共享路径。

## 模型别名

业务代码只引用稳定别名：

```text
chat-default
reasoning-default
vision-default
embedding-default
```

registry 把别名解析为 `protocol + base_url + model + key file + timeout`。可以为单个应用覆盖 model，但不能在 registry 写入 API Key。

## 部署时渲染

以下命令只把配置和密钥文件路径写入目标 env，不复制密钥值：

```bash
.venv/Scripts/python.exe scripts/render_llm_env.py \
  --registry llm/registry.yml \
  --secrets-dir /etc/shadow-platform/secrets \
  --app health \
  --binding CHAT=chat-default \
  --binding VISION=vision-default \
  --output /etc/shadow/health/llm.env
```

输出示意：

```text
SHADOW_LLM_CHAT_PROTOCOL="openai-compatible"
SHADOW_LLM_CHAT_BASE_URL="https://api.example.com/v1"
SHADOW_LLM_CHAT_MODEL="model-name"
SHADOW_LLM_CHAT_API_KEY_FILE="/etc/shadow-platform/secrets/llm/health/primary-api-key"
```

项目从文件读取 Key 后直接创建自己的供应商客户端。浏览器和 ShadowApp 永远不能读取这些文件或配置值。

## 可观测性

不设代理后，统一平台无法天然统计 Token。各项目可以选择异步写入不含正文的使用事件：

```text
app_id, model_alias, actual_model, input_tokens, output_tokens,
latency_ms, status, request_id
```

统计失败不得影响正常模型调用。默认禁止上报提示词、回答、健康数据和媒体访问地址。
