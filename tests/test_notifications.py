from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from notification_service.app import create_app
from notification_service.channels.base import ChannelSendError, DeliveryTarget
from notification_service.channels.feishu import FeishuAdapter
from notification_service.channels.qqbot import QQBotAdapter
from notification_service.channels.telegram import TelegramAdapter
from notification_service.config import (
    ChannelAccount,
    ChannelConfig,
    PrincipalConfig,
    Settings,
    TargetConfig,
    load_channel_config,
)
from notification_service.database import Base, create_database
from notification_service.models import NotificationAuditEvent, NotificationDelivery
from notification_service.schemas import NotificationCreate, Recipient
from notification_service.service import publish_notification, sync_channel_configuration
from notification_service.worker import deliver_one
from shadow_sdk.notifications import NotificationClient
from shadow_sdk.service_auth import hash_service_token

APP_TOKEN = "garden-notification-token-at-least-32-bytes"
GATEWAY_TOKEN = "hermes-notification-token-at-least-32-bytes"


def channel_config(*, command_level: str = "operator") -> ChannelConfig:
    return ChannelConfig(
        accounts={("telegram", "default"): ChannelAccount("telegram", "default", enabled=False)},
        targets=(
            TargetConfig(
                recipient_issuer="dev://shadow",
                recipient_subject="dev-user",
                channel="telegram",
                account_id="default",
                target_kind="group",
                target_id="-100123",
                label="Test group",
                enabled=True,
                is_home=True,
                require_mention=True,
                command_level=command_level,
            ),
        ),
        principals=(
            PrincipalConfig(
                recipient_issuer="dev://shadow",
                recipient_subject="dev-user",
                channel="telegram",
                account_id="default",
                sender_id="123",
                role="operator",
            ),
        ),
    )


def make_client(tmp_path, *, config: ChannelConfig | None = None) -> TestClient:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'notifications.db'}",
        service_token_hashes={
            "garden": (hash_service_token(APP_TOKEN),),
            "hermes": (hash_service_token(GATEWAY_TOKEN),),
        },
        channel_config=config or channel_config(),
        dev_auth=True,
    )
    return TestClient(create_app(settings))


def notification_payload(event_id: str = "health-reminder-1") -> dict:
    return {
        "event_id": event_id,
        "recipient": {"issuer": "dev://shadow", "subject": "dev-user"},
        "category": "health.reminder",
        "severity": "warning",
        "title": "起来活动一下了",
        "body": "今天还没有完成活动目标。",
        "resource_uri": "shadow://health/daily/2026-08-20",
        "delivery_mode": "home",
    }


def test_publish_is_durable_idempotent_and_visible_in_inbox(tmp_path):
    headers = {"Authorization": f"Bearer {APP_TOKEN}"}
    with make_client(tmp_path) as client:
        first = client.post("/v1/notifications", headers=headers, json=notification_payload())
        duplicate = client.post("/v1/notifications", headers=headers, json=notification_payload())
        inbox = client.get("/v1/inbox")
        notification_id = first.json()["notification_id"]
        read = client.post(f"/v1/inbox/{notification_id}/read")
        operations = client.get("/v1/operations")

    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert first.json()["deliveries"] == 1
    assert duplicate.json() == {
        "notification_id": notification_id,
        "duplicate": True,
        "deliveries": 1,
    }
    assert inbox.status_code == 200
    assert inbox.json()["unread_count"] == 1
    assert inbox.json()["items"][0]["source_app_id"] == "garden"
    assert read.json() == {"id": notification_id, "state": "read"}
    assert operations.json()["deliveries_pending"] == 1


def test_inbox_uses_stable_owner_scoped_cursor(tmp_path):
    headers = {"Authorization": f"Bearer {APP_TOKEN}"}
    with make_client(tmp_path) as client:
        for index in range(3):
            payload = notification_payload(f"page-event-{index}")
            payload["occurred_at"] = f"2026-08-20T10:0{index}:00+08:00"
            client.post("/v1/notifications", headers=headers, json=payload)
        first = client.get("/v1/inbox?limit=2")
        second = client.get(f"/v1/inbox?limit=2&cursor={first.json()['next_cursor']}")
        invalid = client.get("/v1/inbox?limit=2&cursor=00000000-0000-0000-0000-000000000000")

    assert len(first.json()["items"]) == 2
    assert first.json()["next_cursor"] is not None
    assert len(second.json()["items"]) == 1
    assert second.json()["next_cursor"] is None
    assert invalid.status_code == 422


def test_chat_bridge_enforces_pairing_mentions_private_ops_and_event_deduplication(tmp_path):
    headers = {"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    base = {
        "event_id": "tg-event-1",
        "channel": "telegram",
        "account_id": "default",
        "peer_kind": "group",
        "peer_id": "-100123",
        "sender_id": "123",
        "command": "inbox",
    }
    with make_client(tmp_path) as client:
        client.post(
            "/v1/notifications",
            headers={"Authorization": f"Bearer {APP_TOKEN}"},
            json=notification_payload(),
        )
        missing_mention = client.post("/v1/chat/commands", headers=headers, json=base)
        accepted = client.post(
            "/v1/chat/commands", headers=headers, json={**base, "mentioned": True}
        )
        duplicate = client.post(
            "/v1/chat/commands", headers=headers, json={**base, "mentioned": True}
        )
        group_ops = client.post(
            "/v1/chat/commands",
            headers=headers,
            json={**base, "event_id": "tg-event-2", "command": "ops", "mentioned": True},
        )
        direct_ops = client.post(
            "/v1/chat/commands",
            headers=headers,
            json={
                **base,
                "event_id": "tg-event-3",
                "peer_kind": "direct",
                "peer_id": "123",
                "command": "ops",
            },
        )
        bot_command = client.post(
            "/v1/chat/commands",
            headers=headers,
            json={
                **base,
                "event_id": "tg-event-4",
                "peer_kind": "direct",
                "peer_id": "123",
                "sender_is_bot": True,
            },
        )

    assert missing_mention.status_code == 403
    assert accepted.status_code == 200
    assert "起来活动一下了" in accepted.json()["response_text"]
    assert duplicate.json()["duplicate"] is True
    assert group_ops.status_code == 403
    assert direct_ops.status_code == 200
    assert "Platform" in direct_ops.json()["response_text"]
    assert bot_command.status_code == 403


def test_safe_group_does_not_expose_personal_inbox(tmp_path):
    headers = {"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    with make_client(tmp_path, config=channel_config(command_level="safe")) as client:
        denied = client.post(
            "/v1/chat/commands",
            headers=headers,
            json={
                "event_id": "safe-group-event",
                "channel": "telegram",
                "account_id": "default",
                "peer_kind": "group",
                "peer_id": "-100123",
                "sender_id": "123",
                "mentioned": True,
                "command": "inbox",
            },
        )
        status = client.post(
            "/v1/chat/commands",
            headers=headers,
            json={
                "event_id": "safe-group-status",
                "channel": "telegram",
                "account_id": "default",
                "peer_kind": "group",
                "peer_id": "-100123",
                "sender_id": "123",
                "mentioned": True,
                "command": "status",
            },
        )

    assert denied.status_code == 403
    assert status.status_code == 200


def test_native_channel_adapters_send_to_group_and_thread_targets():
    seen: list[httpx.Request] = []

    def telegram_handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    telegram = TelegramAdapter(
        ChannelAccount("telegram", "default", token="token", api_base="https://tg.test"),
        httpx.MockTransport(telegram_handler),
    )
    telegram_result = telegram.send(DeliveryTarget("group", "-100", "42"), "hello")
    assert telegram_result.provider_message_id == "7"
    assert json.loads(seen[-1].content)["message_thread_id"] == "42"

    def feishu_handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "tenant", "expire": 7200}
            )
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

    feishu = FeishuAdapter(
        ChannelAccount(
            "feishu", "default", app_id="app", app_secret="secret", api_base="https://fs.test"
        ),
        httpx.MockTransport(feishu_handler),
    )
    assert feishu.send(DeliveryTarget("group", "oc_group"), "hello").provider_message_id == "om_1"
    assert seen[-1].url.params["receive_id_type"] == "chat_id"

    def qq_handler(request: httpx.Request):
        seen.append(request)
        if request.url.host == "bots.qq.com":
            return httpx.Response(200, json={"access_token": "qq-token", "expires_in": "7200"})
        return httpx.Response(200, json={"id": "qq_1"})

    qq = QQBotAdapter(
        ChannelAccount(
            "qqbot", "default", app_id="app", app_secret="secret", api_base="https://qq.test"
        ),
        httpx.MockTransport(qq_handler),
    )
    assert qq.send(DeliveryTarget("group", "group_openid"), "hello").provider_message_id == "qq_1"
    assert seen[-1].url.path == "/v2/groups/group_openid/messages"
    assert seen[-1].headers["authorization"] == "QQBot qq-token"


def test_worker_moves_non_retryable_provider_error_to_dead_letter(tmp_path):
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    config = channel_config()
    with session_factory() as db:
        sync_channel_configuration(db, config)
        publish_notification(
            db,
            "garden",
            NotificationCreate(
                event_id="event",
                recipient=Recipient(issuer="dev://shadow", subject="dev-user"),
                category="garden.publish",
                severity="warning",
                title="Published",
            ),
        )

    class FailingAdapter:
        def send(self, _target, _message):
            raise ChannelSendError("telegram_http_400", "bad target", retryable=False)

    class Registry:
        def get(self, _channel, _account_id):
            return FailingAdapter()

    assert deliver_one(
        session_factory, Registry(), worker_id="test", max_attempts=8, now=datetime.now(UTC)
    )
    with session_factory() as db:
        delivery = db.scalar(select(NotificationDelivery))
        assert delivery.state == "dead_letter"
        assert delivery.attempts == 1
        assert delivery.last_error_code == "telegram_http_400"
    engine.dispose()


def test_admin_can_requeue_dead_letter_and_worker_reclaims_stale_lease(tmp_path):
    with make_client(tmp_path) as client:
        published = client.post(
            "/v1/notifications",
            headers={"Authorization": f"Bearer {APP_TOKEN}"},
            json=notification_payload("recoverable-event"),
        )
        with client.app.state.session_factory() as db:
            delivery = db.scalar(select(NotificationDelivery))
            delivery.state = "dead_letter"
            delivery.last_error_code = "telegram_http_400"
            delivery_id = delivery.id
            db.commit()

        summary = client.get("/v1/operations")
        retried = client.post(f"/v1/operations/deliveries/{delivery_id}/retry")

        assert summary.json()["recent_dead_letters"][0]["id"] == delivery_id
        assert retried.json() == {"id": delivery_id, "state": "pending"}

        with client.app.state.session_factory() as db:
            delivery = db.get(NotificationDelivery, delivery_id)
            assert delivery.attempts == 0
            delivery.state = "delivering"
            delivery.locked_at = datetime.now(UTC) - timedelta(minutes=6)
            delivery.locked_by = "dead-worker"
            db.commit()

        class SuccessfulAdapter:
            def send(self, _target, _message):
                return type("Result", (), {"provider_message_id": "provider-id"})()

        class Registry:
            def get(self, _channel, _account_id):
                return SuccessfulAdapter()

        assert deliver_one(
            client.app.state.session_factory,
            Registry(),
            worker_id="new-worker",
            max_attempts=8,
            now=datetime.now(UTC),
        )
        with client.app.state.session_factory() as db:
            delivery = db.get(NotificationDelivery, delivery_id)
            assert delivery.state == "delivered"
            assert delivery.provider_message_id == "provider-id"
            audit = db.scalar(
                select(NotificationAuditEvent).where(
                    NotificationAuditEvent.action == "delivery.retry_requested"
                )
            )
            assert audit is not None

    assert published.status_code == 202


def test_worker_retries_transport_errors(tmp_path):
    engine, session_factory = create_database(f"sqlite:///{tmp_path / 'transport.db'}")
    Base.metadata.create_all(engine)
    with session_factory() as db:
        sync_channel_configuration(db, channel_config())
        publish_notification(
            db,
            "garden",
            NotificationCreate(
                event_id="transport-event",
                recipient=Recipient(issuer="dev://shadow", subject="dev-user"),
                category="garden.publish",
                title="Published",
            ),
        )

    class FailingAdapter:
        def send(self, _target, _message):
            raise httpx.ConnectError("network unavailable")

    class Registry:
        def get(self, _channel, _account_id):
            return FailingAdapter()

    assert deliver_one(
        session_factory, Registry(), worker_id="test", max_attempts=8, now=datetime.now(UTC)
    )
    with session_factory() as db:
        delivery = db.scalar(select(NotificationDelivery))
        assert delivery.state == "retrying"
        assert delivery.last_error_code == "channel_transport"
    engine.dispose()


def test_channel_config_reads_secrets_and_rejects_non_loopback_probes(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("telegram-secret", encoding="utf-8")
    config = tmp_path / "channels.yml"
    config.write_text(
        f"""version: 1
accounts:
  telegram:
    default:
      token_file: {secret}
targets: []
principals: []
probes:
  - service_id: health
    url: http://127.0.0.1:8801/readyz
""",
        encoding="utf-8",
    )
    loaded = load_channel_config(config)
    assert loaded.accounts[("telegram", "default")].token == "telegram-secret"

    config.write_text(config.read_text().replace("127.0.0.1:8801", "169.254.169.254"))
    with pytest.raises(ValueError, match="loopback"):
        load_channel_config(config)


def test_production_settings_fail_closed_for_origins_and_session_secrets():
    base = {
        "environment": "production",
        "database_url": "postgresql+psycopg://user:password@db/notifications",
        "service_token_hashes": {"garden": ("a" * 64,)},
        "oidc_issuer": "https://auth.example.com",
        "oidc_client_secret": "oidc-secret",
        "oidc_callbacks": ("https://notify.example.com/auth/callback",),
        "allowed_origins": ("https://notify.example.com",),
    }
    with pytest.raises(ValueError, match="at least 32"):
        Settings(**base, session_secret="short")
    with pytest.raises(ValueError, match="exact HTTPS origins"):
        Settings(
            **{**base, "allowed_origins": ("https://notify.example.com/untrusted",)},
            session_secret="s" * 32,
        )


def test_notification_sdk_does_not_require_callers_to_supply_source_app():
    def handler(request: httpx.Request):
        payload = json.loads(request.content)
        assert "source_app_id" not in payload
        assert request.headers["authorization"] == f"Bearer {APP_TOKEN}"
        return httpx.Response(
            202, json={"notification_id": "id-1", "duplicate": False, "deliveries": 2}
        )

    client = NotificationClient(
        "https://notify.example.com",
        APP_TOKEN,
        transport=httpx.MockTransport(handler),
    )
    result = client.publish(
        event_id="event",
        recipient_issuer="https://auth.example.com",
        recipient_subject="subject",
        category="travel.reminder",
        title="Trip",
    )
    assert result.notification_id == "id-1"
    assert result.deliveries == 2
