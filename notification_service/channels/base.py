from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    kind: str
    target_id: str
    thread_id: str = ""


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_message_id: str


class ChannelSendError(RuntimeError):
    def __init__(self, code: str, detail: str, *, retryable: bool):
        super().__init__(detail)
        self.code = code[:128]
        self.detail = detail[:500]
        self.retryable = retryable


def chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    result: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            result.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, limit)
        if boundary < limit // 2:
            boundary = limit
        result.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    return result


def provider_error(prefix: str, status: int, detail: str) -> ChannelSendError:
    return ChannelSendError(
        f"{prefix}_http_{status}",
        detail or f"provider returned HTTP {status}",
        retryable=status == 429 or status >= 500,
    )
