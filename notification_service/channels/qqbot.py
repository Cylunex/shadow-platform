from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from ..config import ChannelAccount
from .base import ChannelSendError, DeliveryTarget, SendResult, chunks, provider_error


class QQBotAdapter:
    def __init__(self, account: ChannelAccount, transport: httpx.BaseTransport | None = None):
        self.account = account
        self.client = httpx.Client(timeout=10.0, transport=transport)
        self._access_token = ""
        self._access_token_expires_at = datetime.min.replace(tzinfo=UTC)

    def close(self) -> None:
        self.client.close()

    @property
    def base(self) -> str:
        return (self.account.api_base or "https://api.sgroup.qq.com").rstrip("/")

    def _token(self) -> str:
        now = datetime.now(UTC)
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        response = self.client.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": self.account.app_id, "clientSecret": self.account.app_secret},
        )
        payload = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if response.status_code >= 400:
            raise provider_error(
                "qqbot_token",
                response.status_code,
                str(payload.get("message") or "QQ token failed"),
            )
        token = str(payload.get("access_token", ""))
        if not token:
            raise ChannelSendError("qqbot_token_missing", "QQ access token missing", retryable=True)
        expires_in = max(60, int(payload.get("expires_in", 7200)))
        self._access_token = token
        self._access_token_expires_at = now + timedelta(seconds=expires_in - 60)
        return token

    def _path(self, target: DeliveryTarget) -> str:
        if target.kind == "direct":
            return f"/v2/users/{target.target_id}/messages"
        if target.kind == "group":
            return f"/v2/groups/{target.target_id}/messages"
        if target.kind == "channel":
            return f"/channels/{target.target_id}/messages"
        raise ValueError("unsupported QQ target kind")

    def send(self, target: DeliveryTarget, message: str) -> SendResult:
        ids: list[str] = []
        for sequence, part in enumerate(chunks(message, 1800), start=1):
            body: dict[str, object] = {"content": part}
            if target.kind in {"direct", "group"}:
                body.update({"msg_type": 0, "msg_seq": sequence})
            response = self.client.post(
                f"{self.base}{self._path(target)}",
                headers={"Authorization": f"QQBot {self._token()}"},
                json=body,
            )
            payload = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400:
                detail = str(payload.get("message") or payload.get("msg") or "QQ send failed")
                raise provider_error("qqbot", response.status_code, detail)
            ids.append(str(payload.get("id") or payload.get("message_id") or ""))
        return SendResult(provider_message_id=",".join(value for value in ids if value))
