from __future__ import annotations

import httpx

from ..config import ChannelAccount, ChannelConfig
from .feishu import FeishuAdapter
from .qqbot import QQBotAdapter
from .telegram import TelegramAdapter


class ChannelRegistry:
    def __init__(
        self,
        config: ChannelConfig,
        transports: dict[tuple[str, str], httpx.BaseTransport] | None = None,
    ):
        transports = transports or {}
        self.adapters = {}
        for key, account in config.accounts.items():
            if not account.enabled:
                continue
            self.adapters[key] = self._adapter(account, transports.get(key))

    @staticmethod
    def _adapter(account: ChannelAccount, transport: httpx.BaseTransport | None):
        if account.channel == "telegram":
            return TelegramAdapter(account, transport)
        if account.channel == "feishu":
            return FeishuAdapter(account, transport)
        if account.channel == "qqbot":
            return QQBotAdapter(account, transport)
        raise ValueError(f"unsupported notification channel: {account.channel}")

    def get(self, channel: str, account_id: str):
        try:
            return self.adapters[(channel, account_id)]
        except KeyError as exc:
            raise ValueError(f"channel account is not configured: {channel}/{account_id}") from exc

    def close(self) -> None:
        for adapter in self.adapters.values():
            adapter.close()
