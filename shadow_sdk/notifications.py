from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx


class NotificationClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NotificationResult:
    notification_id: str
    duplicate: bool
    deliveries: int


class NotificationClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlsplit(base_url)
        is_loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        if parsed.scheme != "https" and not is_loopback:
            raise ValueError("notification base URL must use HTTPS or loopback HTTP")
        if len(service_token) < 32:
            raise ValueError("service token must contain at least 32 characters")
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self.client.close()

    def publish(
        self,
        *,
        event_id: str,
        recipient_issuer: str,
        recipient_subject: str,
        category: str,
        title: str,
        body: str = "",
        severity: str = "info",
        resource_uri: str | None = None,
        attributes: dict[str, Any] | None = None,
        delivery_mode: str = "home",
        channels: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> NotificationResult:
        payload: dict[str, Any] = {
            "event_id": event_id,
            "recipient": {"issuer": recipient_issuer, "subject": recipient_subject},
            "category": category,
            "severity": severity,
            "title": title,
            "body": body,
            "resource_uri": resource_uri,
            "attributes": attributes or {},
            "delivery_mode": delivery_mode,
            "channels": list(channels),
        }
        if occurred_at is not None:
            payload["occurred_at"] = occurred_at.isoformat()
        if expires_at is not None:
            payload["expires_at"] = expires_at.isoformat()
        try:
            response = self.client.post(
                f"{self.base_url}/v1/notifications",
                headers={"Authorization": f"Bearer {self.service_token}"},
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            return NotificationResult(
                notification_id=str(result["notification_id"]),
                duplicate=bool(result["duplicate"]),
                deliveries=int(result["deliveries"]),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise NotificationClientError("notification publish failed") from exc
