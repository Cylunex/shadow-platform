from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Id = str


class LLMUsageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    app_id: Id = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    agent_id: Id | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,63}$")
    model_alias: Id = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    provider: Id = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    actual_model: str = Field(min_length=1, max_length=255)
    protocol: Literal["openai-compatible", "anthropic"]
    api: Literal["responses", "chat-completions", "messages"]
    status: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    retry_count: int = Field(ge=0)
    streamed: bool
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("started_at must include a timezone")
        return value


class UsageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[LLMUsageIn] = Field(min_length=1, max_length=100)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int


class UsageSummaryBucket(BaseModel):
    model_alias: str
    provider: str
    actual_model: str
    status: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    total_latency_ms: int
    retry_count: int


class UsageSummary(BaseModel):
    app_id: str
    start: datetime
    end: datetime
    buckets: list[UsageSummaryBucket]
