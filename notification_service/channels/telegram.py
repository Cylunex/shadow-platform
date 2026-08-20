from __future__ import annotations

import httpx

from ..config import ChannelAccount
from .base import DeliveryTarget, SendResult, chunks, provider_error


class TelegramAdapter:
    def __init__(self, account: ChannelAccount, transport: httpx.BaseTransport | None = None):
        self.account = account
        self.client = httpx.Client(timeout=10.0, transport=transport)

    def close(self) -> None:
        self.client.close()

    def send(self, target: DeliveryTarget, message: str) -> SendResult:
        if target.kind not in {"direct", "group", "channel"}:
            raise ValueError("unsupported Telegram target kind")
        ids: list[str] = []
        base = self.account.api_base or "https://api.telegram.org"
        endpoint = f"{base.rstrip('/')}/bot{self.account.token}/sendMessage"
        for part in chunks(message, 4000):
            body: dict[str, object] = {
                "chat_id": target.target_id,
                "text": part,
                "disable_web_page_preview": True,
            }
            if target.thread_id:
                body["message_thread_id"] = target.thread_id
            response = self.client.post(endpoint, json=body)
            payload = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400 or not payload.get("ok", False):
                detail = str(payload.get("description") or "Telegram send failed")
                raise provider_error("telegram", response.status_code, detail)
            ids.append(str(payload.get("result", {}).get("message_id", "")))
        return SendResult(provider_message_id=",".join(value for value in ids if value))
