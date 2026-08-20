from __future__ import annotations

import argparse
import socket
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .channels import ChannelRegistry, ChannelSendError, DeliveryTarget
from .config import ProbeConfig, Settings
from .database import Base, create_database
from .models import (
    BrowserSession,
    ChatIngressEvent,
    Notification,
    NotificationAuditEvent,
    NotificationDelivery,
    OidcTransaction,
    OperationProbe,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def render_notification(notification: Notification) -> str:
    labels = {
        "info": "通知",
        "success": "完成",
        "warning": "注意",
        "critical": "紧急",
    }
    lines = [f"[{labels.get(notification.severity, '通知')}] {notification.title}"]
    if notification.body:
        lines.extend(("", notification.body))
    lines.extend(("", f"来源：{notification.source_app_id}"))
    if notification.resource_uri:
        lines.append(f"打开：{notification.resource_uri}")
    lines.append(f"编号：{notification.id[:8]}")
    return "\n".join(lines)


def _claim_delivery(db: Session, worker_id: str, now: datetime) -> str | None:
    stale_lock = now - timedelta(minutes=5)
    row = db.scalar(
        select(NotificationDelivery)
        .where(
            or_(
                (
                    NotificationDelivery.state.in_(("pending", "retrying"))
                    & (NotificationDelivery.available_at <= now)
                ),
                (
                    (NotificationDelivery.state == "delivering")
                    & (NotificationDelivery.locked_at <= stale_lock)
                ),
            )
        )
        .order_by(NotificationDelivery.available_at, NotificationDelivery.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        return None
    row.state = "delivering"
    row.locked_at = now
    row.locked_by = worker_id
    row.attempts += 1
    db.commit()
    return row.id


def deliver_one(
    session_factory,
    registry: ChannelRegistry,
    *,
    worker_id: str,
    max_attempts: int,
    now: datetime | None = None,
) -> bool:
    now = now or utcnow()
    with session_factory() as db:
        delivery_id = _claim_delivery(db, worker_id, now)
    if delivery_id is None:
        return False

    with session_factory() as db:
        delivery = db.get(NotificationDelivery, delivery_id)
        notification = db.get(Notification, delivery.notification_id) if delivery else None
        if delivery is None or notification is None:
            return True
        expires_at = notification.expires_at
        if expires_at is not None and expires_at.replace(tzinfo=expires_at.tzinfo or UTC) <= now:
            delivery.state = "suppressed"
            delivery.last_error_code = "notification_expired"
            delivery.locked_at = None
            delivery.locked_by = None
            db.commit()
            return True
        try:
            adapter = registry.get(delivery.channel, delivery.account_id)
            result = adapter.send(
                DeliveryTarget(delivery.target_kind, delivery.target_id, delivery.thread_id),
                render_notification(notification),
            )
        except (httpx.HTTPError, ChannelSendError, ValueError) as exc:
            if isinstance(exc, ChannelSendError):
                retryable = exc.retryable
                error_code = exc.code
                detail = exc.detail
            elif isinstance(exc, httpx.HTTPError):
                retryable = True
                error_code = "channel_transport"
                detail = str(exc)[:500]
            else:
                retryable = False
                error_code = "channel_not_configured"
                detail = str(exc)[:500]
            final = not retryable or delivery.attempts >= max_attempts
            delivery.state = "dead_letter" if final else "retrying"
            delivery.last_error_code = error_code
            delivery.last_error_detail = detail
            if not final:
                delay = min(3600, 15 * (2 ** min(delivery.attempts - 1, 8)))
                delivery.available_at = now + timedelta(seconds=delay)
            db.add(
                NotificationAuditEvent(
                    actor_type="worker",
                    actor_id=worker_id,
                    action=("delivery.dead_letter" if final else "delivery.retry"),
                    aggregate_type="delivery",
                    aggregate_id=delivery.id,
                    details={"error_code": error_code, "attempts": delivery.attempts},
                )
            )
        else:
            delivery.state = "delivered"
            delivery.provider_message_id = result.provider_message_id
            delivery.delivered_at = now
            delivery.last_error_code = None
            delivery.last_error_detail = None
        delivery.locked_at = None
        delivery.locked_by = None
        db.commit()
    return True


def probe_service(db: Session, probe: ProbeConfig, now: datetime | None = None) -> OperationProbe:
    now = now or utcnow()
    row = db.get(OperationProbe, probe.service_id)
    if row is None:
        row = OperationProbe(service_id=probe.service_id)
        db.add(row)
    started = time.monotonic()
    try:
        response = httpx.get(probe.url, timeout=probe.timeout_seconds)
        latency_ms = int((time.monotonic() - started) * 1000)
        row.status_code = response.status_code
        row.latency_ms = latency_ms
        if response.status_code == probe.expected_status:
            row.status = "healthy"
            row.consecutive_failures = 0
            row.error_code = None
        else:
            row.status = "unhealthy"
            row.consecutive_failures += 1
            row.error_code = f"http_{response.status_code}"
    except httpx.HTTPError as exc:
        row.status = "unreachable"
        row.status_code = None
        row.latency_ms = int((time.monotonic() - started) * 1000)
        row.consecutive_failures += 1
        row.error_code = type(exc).__name__
    row.checked_at = now
    db.commit()
    return row


def cleanup(db: Session, retention_days: int, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    session_cutoff = now - timedelta(days=1)
    event_cutoff = now - timedelta(days=retention_days)
    sessions = db.execute(
        delete(BrowserSession).where(
            (BrowserSession.expires_at < session_cutoff)
            | (
                BrowserSession.revoked_at.is_not(None)
                & (BrowserSession.revoked_at < session_cutoff)
            )
        )
    ).rowcount
    transactions = db.execute(
        delete(OidcTransaction).where(OidcTransaction.expires_at < session_cutoff)
    ).rowcount
    chat_events = db.execute(
        delete(ChatIngressEvent).where(ChatIngressEvent.created_at < event_cutoff)
    ).rowcount
    audit_events = db.execute(
        delete(NotificationAuditEvent).where(NotificationAuditEvent.occurred_at < event_cutoff)
    ).rowcount
    notifications = db.execute(
        delete(Notification).where(
            ((Notification.state == "archived") & (Notification.archived_at < event_cutoff))
            | (Notification.expires_at < event_cutoff)
        )
    ).rowcount
    db.commit()
    return {
        "sessions": sessions or 0,
        "oidc_transactions": transactions or 0,
        "chat_events": chat_events or 0,
        "audit_events": audit_events or 0,
        "notifications": notifications or 0,
    }


def run(settings: Settings | None = None, *, once: bool = False) -> None:
    resolved = settings or Settings.from_env()
    engine, session_factory = create_database(resolved.database_url)
    if resolved.environment != "production":
        Base.metadata.create_all(engine)
    registry = ChannelRegistry(resolved.channel_config)
    worker_id = f"{socket.gethostname()}:{__import__('os').getpid()}"
    last_probe = datetime.min.replace(tzinfo=UTC)
    try:
        while True:
            worked = deliver_one(
                session_factory,
                registry,
                worker_id=worker_id,
                max_attempts=resolved.max_attempts,
            )
            now = utcnow()
            if now - last_probe >= timedelta(seconds=resolved.probe_interval_seconds):
                with session_factory() as db:
                    for probe in resolved.channel_config.probes:
                        probe_service(db, probe, now)
                last_probe = now
            if once:
                break
            if not worked:
                time.sleep(1)
    finally:
        registry.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow notification delivery worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(once=args.once)


if __name__ == "__main__":
    main()
