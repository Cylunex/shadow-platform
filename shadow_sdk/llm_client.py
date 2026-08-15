from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from .llm import ID_PATTERN, LLMConfigError, ResolvedLLMConfig, resolve_llm_config

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= 8:
            raise ValueError("max_retries must be between 0 and 8")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")


@dataclass(frozen=True, slots=True)
class LLMUsageEvent:
    request_id: str
    app_id: str
    agent_id: str | None
    model_alias: str
    provider: str
    actual_model: str
    protocol: str
    api: str
    status: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    retry_count: int
    streamed: bool
    started_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UsageSink(Protocol):
    """A metadata-only sink. Implementations must never expect prompt/response bodies."""

    def emit(self, event: LLMUsageEvent) -> None: ...


class NullUsageSink:
    def emit(self, event: LLMUsageEvent) -> None:
        del event


class JsonlUsageSink:
    """Append metadata to a local outbox that a separate collector can batch-upload."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, event: LLMUsageEvent) -> None:
        line = json.dumps(event.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{line}\n")


class LLMRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        request_id: str,
        model_alias: str,
        provider: str,
        kind: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.model_alias = model_alias
        self.provider = provider
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class LLMStreamEvent:
    event: str | None
    data: dict[str, Any] | str


@dataclass(slots=True)
class _Target:
    config: ResolvedLLMConfig
    client: httpx.Client | httpx.AsyncClient


def _resolve_chain(
    registry_path: str | Path,
    *,
    secrets_dir: str | Path,
    app_id: str,
    alias: str,
) -> list[ResolvedLLMConfig]:
    resolved: list[ResolvedLLMConfig] = []
    visited: set[str] = set()

    def visit(current: str) -> None:
        if current in visited:
            raise LLMConfigError(f"cyclic LLM fallback detected at alias: {current}")
        if len(visited) >= 8:
            raise LLMConfigError("LLM fallback chain cannot exceed 8 aliases")
        visited.add(current)
        config = resolve_llm_config(
            registry_path,
            secrets_dir=secrets_dir,
            app_id=app_id,
            alias=current,
        )
        resolved.append(config)
        for fallback in config.fallbacks:
            visit(fallback)

    visit(alias)
    primary_api = resolved[0].api
    incompatible = [item.alias for item in resolved[1:] if item.api != primary_api]
    if incompatible:
        aliases = ", ".join(incompatible)
        raise LLMConfigError(
            f"fallbacks must use the same native API as {alias} ({primary_api}): {aliases}"
        )
    return [replace(item, alias=alias) for item in resolved]


def _headers(config: ResolvedLLMConfig) -> dict[str, str]:
    api_key = config.read_api_key()
    if config.protocol == "anthropic":
        return {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "x-api-key": api_key,
        }
    return {"authorization": f"Bearer {api_key}", "content-type": "application/json"}


def _endpoint(config: ResolvedLLMConfig) -> str:
    if config.api == "responses":
        return "responses"
    if config.api == "chat-completions":
        return "chat/completions"
    return "v1/messages"


def _payload(
    config: ResolvedLLMConfig, values: Mapping[str, Any], *, stream: bool
) -> dict[str, Any]:
    if "model" in values:
        raise ValueError("model is controlled by the Shadow model alias")
    if "stream" in values:
        raise ValueError("use create() or stream() instead of setting stream directly")
    result = dict(values)
    result["model"] = config.model
    if stream:
        result["stream"] = True
        if config.api == "chat-completions":
            options = result.setdefault("stream_options", {})
            if isinstance(options, dict):
                options.setdefault("include_usage", True)
    return result


def _usage(data: Mapping[str, Any], api: str) -> tuple[int | None, int | None, int | None]:
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        return None, None, None
    if api == "chat-completions":
        input_tokens = _integer(usage.get("prompt_tokens"))
        output_tokens = _integer(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
    else:
        input_tokens = _integer(usage.get("input_tokens"))
        output_tokens = _integer(usage.get("output_tokens"))
        details = usage.get("input_tokens_details")
    cached_tokens = None
    if isinstance(details, Mapping):
        cached_tokens = _integer(details.get("cached_tokens"))
    if cached_tokens is None:
        cached_tokens = _integer(usage.get("cache_read_input_tokens"))
    return input_tokens, output_tokens, cached_tokens


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _actual_model(data: Mapping[str, Any], fallback: str) -> str:
    value = data.get("model")
    return value if isinstance(value, str) and value else fallback


def _delay(policy: RetryPolicy, retry_index: int, response: httpx.Response | None) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), policy.max_delay_seconds)
            except ValueError:
                pass
    base = min(policy.base_delay_seconds * (2**retry_index), policy.max_delay_seconds)
    return base * random.uniform(0.8, 1.2)


def _validate_agent_id(agent_id: str | None) -> None:
    if agent_id is not None and not ID_PATTERN.fullmatch(agent_id):
        raise ValueError(f"invalid agent_id: {agent_id!r}")


def _request_id(value: str | None) -> str:
    result = value or uuid.uuid4().hex
    if not REQUEST_ID_PATTERN.fullmatch(result):
        raise ValueError("request_id must be 1-128 safe identifier characters")
    return result


def _status_error(
    response: httpx.Response,
    *,
    request_id: str,
    config: ResolvedLLMConfig,
) -> LLMRequestError:
    retryable = response.status_code in RETRYABLE_STATUS_CODES
    return LLMRequestError(
        f"LLM provider returned HTTP {response.status_code}",
        request_id=request_id,
        model_alias=config.alias,
        provider=config.provider_id,
        kind="http_status",
        retryable=retryable,
        status_code=response.status_code,
    )


def _transport_error(
    exc: httpx.HTTPError,
    *,
    request_id: str,
    config: ResolvedLLMConfig,
) -> LLMRequestError:
    kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
    return LLMRequestError(
        f"LLM provider {kind} error",
        request_id=request_id,
        model_alias=config.alias,
        provider=config.provider_id,
        kind=kind,
        retryable=True,
    )


class LLMClient:
    """Synchronous in-process client; all model traffic goes directly to the provider."""

    def __init__(
        self,
        configs: list[ResolvedLLMConfig],
        *,
        usage_sink: UsageSink | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not configs:
            raise ValueError("at least one LLM config is required")
        self._targets = [
            _Target(
                config,
                httpx.Client(
                    base_url=f"{config.base_url}/",
                    headers=_headers(config),
                    timeout=config.timeout_seconds,
                    transport=transport,
                ),
            )
            for config in configs
        ]
        self._sink = usage_sink or NullUsageSink()
        self._retry = retry_policy or RetryPolicy()

    @classmethod
    def from_registry(
        cls,
        registry_path: str | Path,
        *,
        secrets_dir: str | Path,
        app_id: str,
        alias: str,
        usage_sink: UsageSink | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> LLMClient:
        return cls(
            _resolve_chain(registry_path, secrets_dir=secrets_dir, app_id=app_id, alias=alias),
            usage_sink=usage_sink,
            retry_policy=retry_policy,
            transport=transport,
        )

    @property
    def raw(self) -> httpx.Client:
        """Primary configured HTTP client for provider-specific endpoints and features."""
        client = self._targets[0].client
        assert isinstance(client, httpx.Client)
        return client

    def create(
        self,
        *,
        agent_id: str | None = None,
        request_id: str | None = None,
        **provider_payload: Any,
    ) -> dict[str, Any]:
        _validate_agent_id(agent_id)
        request_id = _request_id(request_id)
        started = datetime.now(UTC)
        started_monotonic = time.monotonic()
        retry_count = 0
        last_error: LLMRequestError | None = None

        for target_index, target in enumerate(self._targets):
            config = target.config
            client = target.client
            assert isinstance(client, httpx.Client)
            for retry_index in range(self._retry.max_retries + 1):
                response: httpx.Response | None = None
                try:
                    response = client.post(
                        _endpoint(config), json=_payload(config, provider_payload, stream=False)
                    )
                    if response.is_success:
                        try:
                            data = response.json()
                        except ValueError as exc:
                            raise LLMRequestError(
                                "LLM provider returned invalid JSON",
                                request_id=request_id,
                                model_alias=config.alias,
                                provider=config.provider_id,
                                kind="invalid_response",
                                retryable=False,
                                status_code=response.status_code,
                            ) from exc
                        if not isinstance(data, dict):
                            raise LLMRequestError(
                                "LLM provider returned a non-object JSON response",
                                request_id=request_id,
                                model_alias=config.alias,
                                provider=config.provider_id,
                                kind="invalid_response",
                                retryable=False,
                                status_code=response.status_code,
                            )
                        self._emit(
                            _event(
                                request_id=request_id,
                                agent_id=agent_id,
                                config=config,
                                data=data,
                                status="success",
                                latency_ms=_elapsed_ms(started_monotonic),
                                retry_count=retry_count,
                                streamed=False,
                                started=started,
                            )
                        )
                        return data
                    last_error = _status_error(response, request_id=request_id, config=config)
                except httpx.HTTPError as exc:
                    last_error = _transport_error(exc, request_id=request_id, config=config)
                except LLMRequestError as exc:
                    last_error = exc

                if not last_error.retryable:
                    self._emit_error(
                        last_error, agent_id, config, started, started_monotonic, retry_count, False
                    )
                    raise last_error
                if retry_index < self._retry.max_retries:
                    time.sleep(_delay(self._retry, retry_index, response))
                    retry_count += 1
                    continue
                if target_index < len(self._targets) - 1:
                    retry_count += 1
                break

        assert last_error is not None
        final_config = self._targets[-1].config
        self._emit_error(
            last_error, agent_id, final_config, started, started_monotonic, retry_count, False
        )
        raise last_error

    def stream(
        self,
        *,
        agent_id: str | None = None,
        request_id: str | None = None,
        **provider_payload: Any,
    ) -> LLMEventStream:
        _validate_agent_id(agent_id)
        return LLMEventStream(
            self,
            provider_payload,
            agent_id=agent_id,
            request_id=_request_id(request_id),
        )

    def close(self) -> None:
        for target in self._targets:
            target.client.close()

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _emit(self, event: LLMUsageEvent) -> None:
        with contextlib.suppress(Exception):
            self._sink.emit(event)

    def _emit_error(
        self,
        error: LLMRequestError,
        agent_id: str | None,
        config: ResolvedLLMConfig,
        started: datetime,
        started_monotonic: float,
        retry_count: int,
        streamed: bool,
    ) -> None:
        self._emit(
            _event(
                request_id=error.request_id,
                agent_id=agent_id,
                config=config,
                data={},
                status=error.kind,
                latency_ms=_elapsed_ms(started_monotonic),
                retry_count=retry_count,
                streamed=streamed,
                started=started,
            )
        )


class AsyncLLMClient:
    """Async in-process client; all model traffic goes directly to the provider."""

    def __init__(
        self,
        configs: list[ResolvedLLMConfig],
        *,
        usage_sink: UsageSink | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not configs:
            raise ValueError("at least one LLM config is required")
        self._targets = [
            _Target(
                config,
                httpx.AsyncClient(
                    base_url=f"{config.base_url}/",
                    headers=_headers(config),
                    timeout=config.timeout_seconds,
                    transport=transport,
                ),
            )
            for config in configs
        ]
        self._sink = usage_sink or NullUsageSink()
        self._retry = retry_policy or RetryPolicy()

    @classmethod
    def from_registry(
        cls,
        registry_path: str | Path,
        *,
        secrets_dir: str | Path,
        app_id: str,
        alias: str,
        usage_sink: UsageSink | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> AsyncLLMClient:
        return cls(
            _resolve_chain(registry_path, secrets_dir=secrets_dir, app_id=app_id, alias=alias),
            usage_sink=usage_sink,
            retry_policy=retry_policy,
            transport=transport,
        )

    @property
    def raw(self) -> httpx.AsyncClient:
        client = self._targets[0].client
        assert isinstance(client, httpx.AsyncClient)
        return client

    async def create(
        self,
        *,
        agent_id: str | None = None,
        request_id: str | None = None,
        **provider_payload: Any,
    ) -> dict[str, Any]:
        _validate_agent_id(agent_id)
        request_id = _request_id(request_id)
        started = datetime.now(UTC)
        started_monotonic = time.monotonic()
        retry_count = 0
        last_error: LLMRequestError | None = None

        for target_index, target in enumerate(self._targets):
            config = target.config
            client = target.client
            assert isinstance(client, httpx.AsyncClient)
            for retry_index in range(self._retry.max_retries + 1):
                response: httpx.Response | None = None
                try:
                    response = await client.post(
                        _endpoint(config), json=_payload(config, provider_payload, stream=False)
                    )
                    if response.is_success:
                        try:
                            data = response.json()
                        except ValueError as exc:
                            raise LLMRequestError(
                                "LLM provider returned invalid JSON",
                                request_id=request_id,
                                model_alias=config.alias,
                                provider=config.provider_id,
                                kind="invalid_response",
                                retryable=False,
                                status_code=response.status_code,
                            ) from exc
                        if not isinstance(data, dict):
                            raise LLMRequestError(
                                "LLM provider returned a non-object JSON response",
                                request_id=request_id,
                                model_alias=config.alias,
                                provider=config.provider_id,
                                kind="invalid_response",
                                retryable=False,
                                status_code=response.status_code,
                            )
                        self._emit(
                            _event(
                                request_id=request_id,
                                agent_id=agent_id,
                                config=config,
                                data=data,
                                status="success",
                                latency_ms=_elapsed_ms(started_monotonic),
                                retry_count=retry_count,
                                streamed=False,
                                started=started,
                            )
                        )
                        return data
                    last_error = _status_error(response, request_id=request_id, config=config)
                except httpx.HTTPError as exc:
                    last_error = _transport_error(exc, request_id=request_id, config=config)
                except LLMRequestError as exc:
                    last_error = exc

                if not last_error.retryable:
                    self._emit_error(
                        last_error, agent_id, config, started, started_monotonic, retry_count, False
                    )
                    raise last_error
                if retry_index < self._retry.max_retries:
                    await asyncio.sleep(_delay(self._retry, retry_index, response))
                    retry_count += 1
                    continue
                if target_index < len(self._targets) - 1:
                    retry_count += 1
                break

        assert last_error is not None
        final_config = self._targets[-1].config
        self._emit_error(
            last_error, agent_id, final_config, started, started_monotonic, retry_count, False
        )
        raise last_error

    def stream(
        self,
        *,
        agent_id: str | None = None,
        request_id: str | None = None,
        **provider_payload: Any,
    ) -> AsyncLLMEventStream:
        _validate_agent_id(agent_id)
        return AsyncLLMEventStream(
            self,
            provider_payload,
            agent_id=agent_id,
            request_id=_request_id(request_id),
        )

    async def aclose(self) -> None:
        for target in self._targets:
            await target.client.aclose()

    async def __aenter__(self) -> AsyncLLMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _emit(self, event: LLMUsageEvent) -> None:
        with contextlib.suppress(Exception):
            self._sink.emit(event)

    def _emit_error(
        self,
        error: LLMRequestError,
        agent_id: str | None,
        config: ResolvedLLMConfig,
        started: datetime,
        started_monotonic: float,
        retry_count: int,
        streamed: bool,
    ) -> None:
        self._emit(
            _event(
                request_id=error.request_id,
                agent_id=agent_id,
                config=config,
                data={},
                status=error.kind,
                latency_ms=_elapsed_ms(started_monotonic),
                retry_count=retry_count,
                streamed=streamed,
                started=started,
            )
        )


class LLMEventStream:
    def __init__(
        self,
        owner: LLMClient,
        payload: Mapping[str, Any],
        *,
        agent_id: str | None,
        request_id: str,
    ) -> None:
        self._owner = owner
        self._payload = payload
        self._agent_id = agent_id
        self._request_id = request_id
        self._started = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._response: httpx.Response | None = None
        self._config: ResolvedLLMConfig | None = None
        self._retry_count = 0
        self._done = False
        self._last_data: dict[str, Any] = {}

    def __enter__(self) -> LLMEventStream:
        last_error: LLMRequestError | None = None
        for target_index, target in enumerate(self._owner._targets):
            config = target.config
            client = target.client
            assert isinstance(client, httpx.Client)
            for retry_index in range(self._owner._retry.max_retries + 1):
                response: httpx.Response | None = None
                try:
                    request = client.build_request(
                        "POST",
                        _endpoint(config),
                        json=_payload(config, self._payload, stream=True),
                    )
                    response = client.send(request, stream=True)
                    if response.is_success:
                        self._response = response
                        self._config = config
                        return self
                    last_error = _status_error(response, request_id=self._request_id, config=config)
                except httpx.HTTPError as exc:
                    last_error = _transport_error(exc, request_id=self._request_id, config=config)
                if response is not None:
                    response.close()
                assert last_error is not None
                if not last_error.retryable:
                    self._emit_error(last_error, config)
                    raise last_error
                if retry_index < self._owner._retry.max_retries:
                    time.sleep(_delay(self._owner._retry, retry_index, response))
                    self._retry_count += 1
                    continue
                if target_index < len(self._owner._targets) - 1:
                    self._retry_count += 1
                break
        assert last_error is not None
        self._emit_error(last_error, self._owner._targets[-1].config)
        raise last_error

    def __iter__(self) -> Iterator[LLMStreamEvent]:
        if self._response is None or self._config is None:
            raise RuntimeError("use LLMEventStream as a context manager")
        try:
            for event in _parse_sse(self._response.iter_lines()):
                self._capture(event)
                yield event
            self._finish("success")
        except httpx.HTTPError as exc:
            error = _transport_error(exc, request_id=self._request_id, config=self._config)
            self._emit_error(error, self._config)
            raise error from exc

    def _capture(self, event: LLMStreamEvent) -> None:
        if isinstance(event.data, dict):
            _merge_stream_data(self._last_data, event.data)

    def _finish(self, status: str) -> None:
        if self._done or self._config is None:
            return
        self._done = True
        self._owner._emit(
            _event(
                request_id=self._request_id,
                agent_id=self._agent_id,
                config=self._config,
                data=self._last_data,
                status=status,
                latency_ms=_elapsed_ms(self._started_monotonic),
                retry_count=self._retry_count,
                streamed=True,
                started=self._started,
            )
        )

    def _emit_error(self, error: LLMRequestError, config: ResolvedLLMConfig) -> None:
        self._done = True
        self._owner._emit_error(
            error,
            self._agent_id,
            config,
            self._started,
            self._started_monotonic,
            self._retry_count,
            True,
        )

    def close(self) -> None:
        if self._response is not None:
            self._response.close()
        self._finish("cancelled")

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncLLMEventStream:
    def __init__(
        self,
        owner: AsyncLLMClient,
        payload: Mapping[str, Any],
        *,
        agent_id: str | None,
        request_id: str,
    ) -> None:
        self._owner = owner
        self._payload = payload
        self._agent_id = agent_id
        self._request_id = request_id
        self._started = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._response: httpx.Response | None = None
        self._config: ResolvedLLMConfig | None = None
        self._retry_count = 0
        self._done = False
        self._last_data: dict[str, Any] = {}

    async def __aenter__(self) -> AsyncLLMEventStream:
        last_error: LLMRequestError | None = None
        for target_index, target in enumerate(self._owner._targets):
            config = target.config
            client = target.client
            assert isinstance(client, httpx.AsyncClient)
            for retry_index in range(self._owner._retry.max_retries + 1):
                response: httpx.Response | None = None
                try:
                    request = client.build_request(
                        "POST",
                        _endpoint(config),
                        json=_payload(config, self._payload, stream=True),
                    )
                    response = await client.send(request, stream=True)
                    if response.is_success:
                        self._response = response
                        self._config = config
                        return self
                    last_error = _status_error(response, request_id=self._request_id, config=config)
                except httpx.HTTPError as exc:
                    last_error = _transport_error(exc, request_id=self._request_id, config=config)
                if response is not None:
                    await response.aclose()
                assert last_error is not None
                if not last_error.retryable:
                    self._emit_error(last_error, config)
                    raise last_error
                if retry_index < self._owner._retry.max_retries:
                    await asyncio.sleep(_delay(self._owner._retry, retry_index, response))
                    self._retry_count += 1
                    continue
                if target_index < len(self._owner._targets) - 1:
                    self._retry_count += 1
                break
        assert last_error is not None
        self._emit_error(last_error, self._owner._targets[-1].config)
        raise last_error

    async def __aiter__(self) -> AsyncIterator[LLMStreamEvent]:
        if self._response is None or self._config is None:
            raise RuntimeError("use AsyncLLMEventStream as an async context manager")
        try:
            async for event in _parse_sse_async(self._response.aiter_lines()):
                self._capture(event)
                yield event
            self._finish("success")
        except httpx.HTTPError as exc:
            error = _transport_error(exc, request_id=self._request_id, config=self._config)
            self._emit_error(error, self._config)
            raise error from exc

    def _capture(self, event: LLMStreamEvent) -> None:
        if isinstance(event.data, dict):
            _merge_stream_data(self._last_data, event.data)

    def _finish(self, status: str) -> None:
        if self._done or self._config is None:
            return
        self._done = True
        self._owner._emit(
            _event(
                request_id=self._request_id,
                agent_id=self._agent_id,
                config=self._config,
                data=self._last_data,
                status=status,
                latency_ms=_elapsed_ms(self._started_monotonic),
                retry_count=self._retry_count,
                streamed=True,
                started=self._started,
            )
        )

    def _emit_error(self, error: LLMRequestError, config: ResolvedLLMConfig) -> None:
        self._done = True
        self._owner._emit_error(
            error,
            self._agent_id,
            config,
            self._started,
            self._started_monotonic,
            self._retry_count,
            True,
        )

    async def aclose(self) -> None:
        if self._response is not None:
            await self._response.aclose()
        self._finish("cancelled")

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


def _parse_sse(lines: Iterator[str]) -> Iterator[LLMStreamEvent]:
    event_type: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if not line:
            if data_lines:
                yield _make_stream_event(event_type, data_lines)
            event_type = None
            data_lines = []
        elif line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield _make_stream_event(event_type, data_lines)


async def _parse_sse_async(lines: AsyncIterator[str]) -> AsyncIterator[LLMStreamEvent]:
    event_type: str | None = None
    data_lines: list[str] = []
    async for line in lines:
        if not line:
            if data_lines:
                yield _make_stream_event(event_type, data_lines)
            event_type = None
            data_lines = []
        elif line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield _make_stream_event(event_type, data_lines)


def _make_stream_event(event_type: str | None, data_lines: list[str]) -> LLMStreamEvent:
    raw = "\n".join(data_lines)
    if raw == "[DONE]":
        return LLMStreamEvent(event=event_type or "done", data=raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return LLMStreamEvent(event=event_type, data=value)


def _merge_stream_data(target: dict[str, Any], event_data: Mapping[str, Any]) -> None:
    nested = event_data.get("response")
    if not isinstance(nested, Mapping):
        nested = event_data.get("message")
    if isinstance(nested, Mapping):
        target.update(nested)
    model = event_data.get("model")
    if isinstance(model, str):
        target["model"] = model
    usage = event_data.get("usage")
    if isinstance(usage, Mapping):
        current = target.setdefault("usage", {})
        if isinstance(current, dict):
            current.update(usage)


def _event(
    *,
    request_id: str,
    agent_id: str | None,
    config: ResolvedLLMConfig,
    data: Mapping[str, Any],
    status: str,
    latency_ms: int,
    retry_count: int,
    streamed: bool,
    started: datetime,
) -> LLMUsageEvent:
    input_tokens, output_tokens, cached_tokens = _usage(data, config.api)
    return LLMUsageEvent(
        request_id=request_id,
        app_id=config.app_id,
        agent_id=agent_id,
        model_alias=config.alias,
        provider=config.provider_id,
        actual_model=_actual_model(data, config.model),
        protocol=config.protocol,
        api=config.api,
        status=status,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        retry_count=retry_count,
        streamed=streamed,
        started_at=started.isoformat(),
    )


def _elapsed_ms(started_monotonic: float) -> int:
    return max(0, round((time.monotonic() - started_monotonic) * 1000))
