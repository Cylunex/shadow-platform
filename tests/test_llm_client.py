import asyncio
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from shadow_sdk.llm import LLMConfigError
from shadow_sdk.llm_client import (
    AsyncLLMClient,
    JsonlUsageSink,
    LLMClient,
    LLMRequestError,
    RetryPolicy,
)

REGISTRY = """
version: 1
providers:
  primary:
    protocol: openai-compatible
    api: responses
    base_url: https://primary.example/v1
    credential_file: llm/{app_id}/primary-key
  backup:
    protocol: openai-compatible
    api: responses
    base_url: https://backup.example/v1
    credential_file: llm/{app_id}/backup-key
  claude:
    protocol: anthropic
    api: messages
    base_url: https://claude.example
    credential_file: llm/{app_id}/claude-key
models:
  chat-default:
    provider: primary
    model: primary-model
    fallbacks: [chat-backup]
  chat-backup:
    provider: backup
    model: backup-model
  claude-default:
    provider: claude
    model: claude-model
apps: {}
"""


class MemorySink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class BrokenSink:
    def emit(self, event):
        del event
        raise RuntimeError("collector unavailable")


def prepare(tmp_path: Path):
    registry = tmp_path / "registry.yml"
    registry.write_text(REGISTRY, encoding="utf-8")
    secrets = tmp_path / "secrets" / "llm" / "travel"
    secrets.mkdir(parents=True)
    (secrets / "primary-key").write_text("primary-secret", encoding="utf-8")
    (secrets / "backup-key").write_text("backup-secret", encoding="utf-8")
    (secrets / "claude-key").write_text("claude-secret", encoding="utf-8")
    return registry, tmp_path / "secrets"


def test_sync_client_calls_provider_directly_and_emits_metadata(tmp_path):
    registry, secrets = prepare(tmp_path)
    sink = MemorySink()
    captured = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "model": "provider-model-snapshot",
                "output": [],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 3},
                },
            },
        )

    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="chat-default",
        usage_sink=sink,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.create(
            input="private itinerary",
            instructions="private system prompt",
            request_id="travel-req-1",
            agent_id="travel-agent",
        )

    assert result["id"] == "resp_123"
    assert captured == {
        "url": "https://primary.example/v1/responses",
        "authorization": "Bearer primary-secret",
        "body": {
            "input": "private itinerary",
            "instructions": "private system prompt",
            "model": "primary-model",
        },
    }
    event = sink.events[0].as_dict()
    assert event["request_id"] == "travel-req-1"
    assert event["app_id"] == "travel"
    assert event["agent_id"] == "travel-agent"
    assert event["actual_model"] == "provider-model-snapshot"
    assert event["input_tokens"] == 10
    assert event["output_tokens"] == 4
    assert event["cached_tokens"] == 3
    assert "private itinerary" not in json.dumps(event)
    assert "private system prompt" not in json.dumps(event)
    assert "primary-secret" not in json.dumps(event)
    schema = json.loads(
        (Path(__file__).parents[1] / "contracts" / "llm-usage-event.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(
        event
    )


def test_retry_then_same_api_fallback(tmp_path):
    registry, secrets = prepare(tmp_path)
    sink = MemorySink()
    hosts = []

    def handler(request: httpx.Request):
        hosts.append(request.url.host)
        if request.url.host == "primary.example":
            return httpx.Response(503, json={"error": {"message": "do not log this"}})
        return httpx.Response(
            200,
            json={
                "model": "backup-snapshot",
                "output": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )

    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="chat-default",
        usage_sink=sink,
        retry_policy=RetryPolicy(max_retries=1, base_delay_seconds=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.create(input="hello")

    assert result["model"] == "backup-snapshot"
    assert hosts == ["primary.example", "primary.example", "backup.example"]
    assert sink.events[0].model_alias == "chat-default"
    assert sink.events[0].retry_count == 2


def test_nonretryable_error_is_sanitized_and_not_sent_to_fallback(tmp_path):
    registry, secrets = prepare(tmp_path)
    sink = MemorySink()
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "private prompt echoed"}})

    with (
        LLMClient.from_registry(
            registry,
            secrets_dir=secrets,
            app_id="travel",
            alias="chat-default",
            usage_sink=sink,
            transport=httpx.MockTransport(handler),
        ) as client,
        pytest.raises(LLMRequestError) as raised,
    ):
        client.create(input="private prompt")

    assert calls == 1
    assert str(raised.value) == "LLM provider returned HTTP 400"
    assert sink.events[0].status == "http_status"
    assert "private" not in json.dumps(sink.events[0].as_dict())


def test_usage_sink_failure_never_breaks_model_call(tmp_path):
    registry, secrets = prepare(tmp_path)

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"model": "ok", "output": []})

    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="chat-default",
        usage_sink=BrokenSink(),
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.create(input="hello")["model"] == "ok"


def test_jsonl_sink_writes_only_usage_event(tmp_path):
    registry, secrets = prepare(tmp_path)
    outbox = tmp_path / "outbox" / "llm.jsonl"

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={"model": "ok", "usage": {"input_tokens": 1, "output_tokens": 2}},
        )

    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="chat-default",
        usage_sink=JsonlUsageSink(outbox),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.create(input="never-store-this")

    line = outbox.read_text(encoding="utf-8")
    assert json.loads(line)["input_tokens"] == 1
    assert "never-store-this" not in line


def test_stream_exposes_native_sse_and_collects_terminal_usage(tmp_path):
    registry, secrets = prepare(tmp_path)
    sink = MemorySink()
    body = (
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"model":"stream-model",'
        '"usage":{"input_tokens":5,"output_tokens":2}}}\n\n'
    )

    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    with (
        LLMClient.from_registry(
            registry,
            secrets_dir=secrets,
            app_id="travel",
            alias="chat-default",
            usage_sink=sink,
            transport=httpx.MockTransport(handler),
        ) as client,
        client.stream(input="hello") as stream,
    ):
        events = list(stream)

    assert events[0].event == "response.output_text.delta"
    assert events[0].data["delta"] == "hi"
    assert sink.events[0].streamed is True
    assert sink.events[0].actual_model == "stream-model"
    assert sink.events[0].input_tokens == 5


def test_async_client_calls_provider_directly(tmp_path):
    registry, secrets = prepare(tmp_path)
    sink = MemorySink()

    async def handler(request: httpx.Request):
        assert str(request.url) == "https://primary.example/v1/responses"
        return httpx.Response(
            200,
            json={"model": "async-model", "usage": {"input_tokens": 7, "output_tokens": 3}},
        )

    async def run():
        async with AsyncLLMClient.from_registry(
            registry,
            secrets_dir=secrets,
            app_id="travel",
            alias="chat-default",
            usage_sink=sink,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await client.create(input="hello")

    assert asyncio.run(run())["model"] == "async-model"
    assert sink.events[0].input_tokens == 7


def test_rejects_cross_api_fallback(tmp_path):
    registry, secrets = prepare(tmp_path)
    registry.write_text(
        REGISTRY.replace("fallbacks: [chat-backup]", "fallbacks: [claude-default]"),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigError, match="same native API"):
        LLMClient.from_registry(
            registry,
            secrets_dir=secrets,
            app_id="travel",
            alias="chat-default",
        )


def test_anthropic_uses_messages_endpoint_and_headers(tmp_path):
    registry, secrets = prepare(tmp_path)

    def handler(request: httpx.Request):
        assert str(request.url) == "https://claude.example/v1/messages"
        assert request.headers["x-api-key"] == "claude-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert json.loads(request.content)["model"] == "claude-model"
        return httpx.Response(
            200,
            json={"model": "claude-model", "usage": {"input_tokens": 2, "output_tokens": 3}},
        )

    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="claude-default",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.create(max_tokens=128, messages=[{"role": "user", "content": "hello"}])


def test_model_and_stream_cannot_bypass_alias(tmp_path):
    registry, secrets = prepare(tmp_path)
    with LLMClient.from_registry(
        registry,
        secrets_dir=secrets,
        app_id="travel",
        alias="chat-default",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    ) as client:
        with pytest.raises(ValueError, match="model is controlled"):
            client.create(model="unregistered", input="hello")
        with pytest.raises(ValueError, match="use create"):
            client.create(stream=True, input="hello")
