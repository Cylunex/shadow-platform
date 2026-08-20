from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from ..config import ChannelAccount
from .base import ChannelSendError, DeliveryTarget, SendResult, chunks, provider_error


class FeishuAdapter:
    def __init__(self, account: ChannelAccount, transport: httpx.BaseTransport | None = None):
        self.account = account
        self.client = httpx.Client(timeout=10.0, transport=transport)
        self._access_token = ""
        self._access_token_expires_at = datetime.min.replace(tzinfo=UTC)

    def close(self) -> None:
        self.client.close()

    @property
    def base(self) -> str:
        if self.account.api_base:
            return self.account.api_base.rstrip("/")
        return (
            "https://open.larksuite.com"
            if self.account.domain == "lark"
            else "https://open.feishu.cn"
        )

    def _token(self) -> str:
        now = datetime.now(UTC)
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        response = self.client.post(
            f"{self.base}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.account.app_id, "app_secret": self.account.app_secret},
        )
        payload = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if response.status_code >= 400 or payload.get("code", 0) != 0:
            detail = str(payload.get("msg") or "Feishu token request failed")
            raise provider_error("feishu_token", response.status_code, detail)
        token = str(payload.get("tenant_access_token", ""))
        if not token:
            raise ChannelSendError("feishu_token_missing", "Feishu token missing", retryable=True)
        expires_in = max(60, int(payload.get("expire", 7200)))
        self._access_token = token
        self._access_token_expires_at = now + timedelta(seconds=expires_in - 60)
        return token

    def send(self, target: DeliveryTarget, message: str) -> SendResult:
        receive_type = "open_id" if target.kind == "direct" else "chat_id"
        ids: list[str] = []
        for part in chunks(message, 3900):
            response = self.client.post(
                f"{self.base}/open-apis/im/v1/messages",
                params={"receive_id_type": receive_type},
                headers={"Authorization": f"Bearer {self._token()}"},
                json={
                    "receive_id": target.target_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": part}, ensure_ascii=False),
                },
            )
            payload = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400 or payload.get("code", 0) != 0:
                detail = str(payload.get("msg") or "Feishu send failed")
                raise provider_error("feishu", response.status_code, detail)
            ids.append(str(payload.get("data", {}).get("message_id", "")))
        return SendResult(provider_message_id=",".join(value for value in ids if value))
