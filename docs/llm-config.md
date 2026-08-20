# LLM 统一配置与直连 SDK（无代理）

## 边界

Shadow Platform 统一模型别名、供应商 Base URL、密钥文件、客户端基础行为和用量事件格式，但不部署 LLM Gateway：

```text
业务项目 -> 同进程 Shadow LLM SDK -> registry 中的供应商
                                  -> 本地 metadata outbox
```

模型请求和响应不会经过 Shadow Platform 服务。各项目继续维护自己的提示词、messages、tools、RAG、业务上下文和结果存储。

## Registry

`llm/registry.yml` 只保存非敏感配置。供应商密钥保存在 secrets 目录，并优先按项目隔离：

```text
$SHADOW_PLATFORM_SECRETS_DIR/llm/health/primary-api-key
$SHADOW_PLATFORM_SECRETS_DIR/llm/foliant/primary-api-key
$SHADOW_PLATFORM_SECRETS_DIR/llm/travel/primary-api-key
```

业务代码引用 `chat-default`、`reasoning-default`、`vision-default` 等稳定别名。别名解析为：

```text
provider + protocol + api + base_url + model + key file + timeout + fallbacks
```

支持的原生 API：

- `openai-compatible + responses`：`POST {base_url}/responses`，新项目首选；
- `openai-compatible + chat-completions`：`POST {base_url}/chat/completions`，兼容只实现 Chat Completions 的服务；
- `anthropic + messages`：`POST {base_url}/v1/messages`。

SDK 不翻译请求正文，因此 fallback 必须使用相同 `api`。这能避免工具、结构化输出或多模态参数在供应商之间被静默改写。需要跨 API 降级时，由业务项目显式转换正文和处理语义差异。

## 同步调用

`create()` 接收供应商原生参数；`model` 和 `stream` 由 SDK 管理，不能在正文里覆盖。

```python
import os

from shadow_sdk import JsonlUsageSink, LLMClient

with LLMClient.from_registry(
    os.environ["SHADOW_LLM_REGISTRY_FILE"],
    secrets_dir=os.environ["SHADOW_PLATFORM_SECRETS_DIR"],
    app_id="travel",
    alias="chat-default",
    usage_sink=JsonlUsageSink(os.environ["SHADOW_LLM_USAGE_OUTBOX"]),
) as client:
    response = client.create(
        request_id="travel-plan-019",
        agent_id="travel-planner",
        instructions="Travel 项目自己的提示词",
        input="规划杭州三日游",
        tools=[...],
    )
```

对 Anthropic `messages` 别名直接传其原生字段：

```python
response = client.create(
    system="Travel 项目自己的提示词",
    messages=[{"role": "user", "content": "规划杭州三日游"}],
    max_tokens=2048,
)
```

## 异步与流式

异步应用使用 `AsyncLLMClient`：

```python
async with AsyncLLMClient.from_registry(...) as client:
    response = await client.create(input="用户输入")

    async with client.stream(input="用户输入") as stream:
        async for event in stream:
            await send_to_app(event.event, event.data)
```

同步流式调用使用 `with client.stream(...)`。SDK 透传供应商 SSE 的 `event` 和 JSON `data`，不会把内容写入统计。只有在收到响应头之前，SDK 才允许重试或切换 fallback；开始向业务代码交付流事件后不再自动重试，避免重复输出。

## 重试、错误与原生逃生口

- 默认对连接错误、超时以及 `408/409/429/5xx` 最多重试两次，再尝试同 API fallback；
- `400/401/403/404` 等非临时错误不会切换 fallback；
- `LLMRequestError` 只暴露 `kind`、状态码、alias、provider 和 request ID，不复制供应商错误正文；
- 统计 sink 抛错会被隔离，不改变调用结果；
- `client.raw` 是已配置 Base URL、密钥和超时的 `httpx` 客户端，可访问供应商特有端点；通过 `raw` 发出的请求不自动统计。

OpenAI 官方目前建议新项目优先使用 Responses API，流式响应采用语义化 SSE 事件；SDK 的 `responses` 模式按这一边界透传事件：[Responses API 迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Streaming Responses](https://developers.openai.com/api/docs/guides/streaming-responses)。

## 用量事件

SDK 只允许生成以下固定字段：

```text
request_id, app_id, agent_id, model_alias, provider, actual_model,
protocol, api, status, latency_ms, input_tokens, output_tokens,
cached_tokens, retry_count, streamed, started_at
```

事件中没有 prompt、response、messages、tools、API Key、健康数据或媒体 URL。`JsonlUsageSink` 只写本地 outbox；`scripts/flush_llm_usage.py` 由独立 timer 批量上传，收集失败时保留 `.sending` 文件等待重试，不能阻塞 LLM 调用。

字段契约见 `contracts/llm-usage-event.schema.json`。`telemetry_service` 按 `(app_id, request_id)` 幂等入库，并通过 `/v1/llm-usage/summary` 提供当前应用自己的 Token、延迟和状态聚合。

## 配置渲染

已有项目仍可只使用配置解析器或部署期 env：

```bash
.venv/Scripts/python.exe scripts/render_llm_env.py \
  --registry llm/registry.yml \
  --secrets-dir "$SHADOW_PLATFORM_SECRETS_DIR" \
  --app health \
  --binding CHAT=chat-default \
  --output "$SHADOW_LLM_RENDER_OUTPUT"
```

输出包含 provider、protocol、api、Base URL、model、超时、fallback 和密钥文件路径，不包含密钥值。浏览器与 ShadowApp 永远不能读取这些文件。
