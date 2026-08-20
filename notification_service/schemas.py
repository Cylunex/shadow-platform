from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["info", "success", "warning", "critical"]
DeliveryMode = Literal["inbox_only", "home", "all"]


class Recipient(BaseModel):
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=255)


class NotificationCreate(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    recipient: Recipient
    category: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    severity: Severity = "info"
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=8000)
    resource_uri: str | None = Field(default=None, max_length=1024)
    attributes: dict[str, Any] = Field(default_factory=dict)
    delivery_mode: DeliveryMode = "home"
    channels: list[Literal["telegram", "feishu", "qqbot"]] = Field(default_factory=list)
    occurred_at: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("resource_uri")
    @classmethod
    def resource_uri_is_shadow(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("shadow://"):
            raise ValueError("resource_uri must use shadow://")
        return value

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.occurred_at and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include timezone")
        if self.expires_at and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        if self.occurred_at and self.expires_at and self.expires_at <= self.occurred_at:
            raise ValueError("expires_at must be after occurred_at")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must be unique")
        return self


class NotificationAccepted(BaseModel):
    notification_id: str
    duplicate: bool
    deliveries: int


class NotificationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_app_id: str
    source_event_id: str
    category: str
    severity: Severity
    title: str
    body: str
    resource_uri: str | None
    attributes: dict[str, Any]
    state: Literal["unread", "read", "archived"]
    occurred_at: datetime
    expires_at: datetime | None
    read_at: datetime | None
    archived_at: datetime | None
    created_at: datetime


class InboxPage(BaseModel):
    items: list[NotificationView]
    unread_count: int
    next_cursor: str | None = None


class InboxActionResult(BaseModel):
    id: str
    state: Literal["unread", "read", "archived"]


class ChatCommand(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    channel: Literal["telegram", "feishu", "qqbot"]
    account_id: str = Field(default="default", min_length=1, max_length=64)
    peer_kind: Literal["direct", "group", "channel"]
    peer_id: str = Field(min_length=1, max_length=255)
    thread_id: str = Field(default="", max_length=255)
    sender_id: str = Field(min_length=1, max_length=255)
    sender_is_bot: bool = False
    mentioned: bool = False
    command: Literal["help", "inbox", "read", "archive", "status", "ops"]
    argument: str = Field(default="", max_length=255)


class ChatCommandResult(BaseModel):
    accepted: bool
    duplicate: bool = False
    response_text: str
    resource_uri: str | None = None


class ProbeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_id: str
    status: str
    status_code: int | None
    latency_ms: int | None
    consecutive_failures: int
    error_code: str | None
    checked_at: datetime | None


class DeliveryFailureView(BaseModel):
    id: str
    notification_id: str
    title: str
    source_app_id: str
    channel: str
    account_id: str
    target_label: str
    attempts: int
    last_error_code: str | None
    last_error_detail: str | None
    updated_at: datetime


class DeliveryActionResult(BaseModel):
    id: str
    state: Literal["pending"]


class OperationsSummary(BaseModel):
    generated_at: datetime
    inbox_unread: int
    deliveries_pending: int
    deliveries_retrying: int
    deliveries_dead_letter: int
    oldest_pending_at: datetime | None
    configured_targets: int
    enabled_targets: int
    probes: list[ProbeView]
    recent_dead_letters: list[DeliveryFailureView]
