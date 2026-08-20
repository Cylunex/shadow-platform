from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import ChannelConfig
from .models import (
    ChannelPrincipal,
    ChannelTarget,
    ChatIngressEvent,
    Notification,
    NotificationAuditEvent,
    NotificationDelivery,
    OperationProbe,
)
from .schemas import (
    ChatCommand,
    ChatCommandResult,
    DeliveryActionResult,
    DeliveryFailureView,
    NotificationAccepted,
    NotificationCreate,
    OperationsSummary,
    ProbeView,
)

SEVERITY_RANK = {"info": 0, "success": 0, "warning": 1, "critical": 2}


def utcnow() -> datetime:
    return datetime.now(UTC)


def sync_channel_configuration(db: Session, config: ChannelConfig) -> None:
    configured_target_keys: set[tuple[str, str, str, str, str]] = set()
    for item in config.targets:
        key = (
            item.channel,
            item.account_id,
            item.target_kind,
            item.target_id,
            item.thread_id,
        )
        configured_target_keys.add(key)
        row = db.scalar(
            select(ChannelTarget).where(
                ChannelTarget.channel == item.channel,
                ChannelTarget.account_id == item.account_id,
                ChannelTarget.target_kind == item.target_kind,
                ChannelTarget.target_id == item.target_id,
                ChannelTarget.thread_id == item.thread_id,
            )
        )
        if row is None:
            row = ChannelTarget(
                channel=item.channel,
                account_id=item.account_id,
                target_kind=item.target_kind,
                target_id=item.target_id,
                thread_id=item.thread_id,
            )
            db.add(row)
        row.recipient_issuer = item.recipient_issuer
        row.recipient_subject = item.recipient_subject
        row.label = item.label
        row.enabled = item.enabled
        row.is_home = item.is_home
        row.min_severity = item.min_severity
        row.categories = list(item.categories)
        row.require_mention = item.require_mention
        row.command_level = item.command_level
    for row in db.scalars(select(ChannelTarget)):
        key = (row.channel, row.account_id, row.target_kind, row.target_id, row.thread_id)
        if key not in configured_target_keys:
            row.enabled = False

    configured_principal_keys: set[tuple[str, str, str]] = set()
    for item in config.principals:
        key = (item.channel, item.account_id, item.sender_id)
        configured_principal_keys.add(key)
        row = db.scalar(
            select(ChannelPrincipal).where(
                ChannelPrincipal.channel == item.channel,
                ChannelPrincipal.account_id == item.account_id,
                ChannelPrincipal.sender_id == item.sender_id,
            )
        )
        if row is None:
            row = ChannelPrincipal(
                channel=item.channel,
                account_id=item.account_id,
                sender_id=item.sender_id,
            )
            db.add(row)
        row.recipient_issuer = item.recipient_issuer
        row.recipient_subject = item.recipient_subject
        row.role = item.role
        row.enabled = item.enabled
    for row in db.scalars(select(ChannelPrincipal)):
        if (row.channel, row.account_id, row.sender_id) not in configured_principal_keys:
            row.enabled = False
    db.commit()


def _target_matches(target: ChannelTarget, body: NotificationCreate) -> bool:
    if not target.enabled:
        return False
    if body.channels and target.channel not in body.channels:
        return False
    if SEVERITY_RANK[target.min_severity] > SEVERITY_RANK[body.severity]:
        return False
    return not target.categories or body.category in target.categories


def publish_notification(
    db: Session, source_app_id: str, body: NotificationCreate
) -> NotificationAccepted:
    existing = db.scalar(
        select(Notification).where(
            Notification.source_app_id == source_app_id,
            Notification.source_event_id == body.event_id,
            Notification.recipient_issuer == body.recipient.issuer,
            Notification.recipient_subject == body.recipient.subject,
        )
    )
    if existing is not None:
        deliveries = db.scalar(
            select(func.count())
            .select_from(NotificationDelivery)
            .where(NotificationDelivery.notification_id == existing.id)
        )
        return NotificationAccepted(
            notification_id=existing.id, duplicate=True, deliveries=deliveries or 0
        )

    notification = Notification(
        source_app_id=source_app_id,
        source_event_id=body.event_id,
        recipient_issuer=body.recipient.issuer,
        recipient_subject=body.recipient.subject,
        category=body.category,
        severity=body.severity,
        title=body.title,
        body=body.body,
        resource_uri=body.resource_uri,
        attributes=body.attributes,
        delivery_mode=body.delivery_mode,
        requested_channels=list(body.channels),
        occurred_at=body.occurred_at or utcnow(),
        expires_at=body.expires_at,
    )
    try:
        db.add(notification)
        db.flush()
    except IntegrityError:
        db.rollback()
        return publish_notification(db, source_app_id, body)

    target_query = select(ChannelTarget).where(
        ChannelTarget.recipient_issuer == body.recipient.issuer,
        ChannelTarget.recipient_subject == body.recipient.subject,
    )
    targets = [] if body.delivery_mode == "inbox_only" else list(db.scalars(target_query))
    if body.delivery_mode == "home":
        targets = [target for target in targets if target.is_home]
    targets = [target for target in targets if _target_matches(target, body)]
    for target in targets:
        db.add(
            NotificationDelivery(
                notification_id=notification.id,
                channel=target.channel,
                account_id=target.account_id,
                target_kind=target.target_kind,
                target_id=target.target_id,
                thread_id=target.thread_id,
            )
        )
    db.add(
        NotificationAuditEvent(
            actor_type="service",
            actor_id=source_app_id,
            action="notification.publish",
            aggregate_type="notification",
            aggregate_id=notification.id,
            details={"delivery_count": len(targets), "severity": body.severity},
        )
    )
    db.commit()
    return NotificationAccepted(
        notification_id=notification.id, duplicate=False, deliveries=len(targets)
    )


def owner_notifications_query(issuer: str, subject: str):
    return select(Notification).where(
        Notification.recipient_issuer == issuer,
        Notification.recipient_subject == subject,
    )


def set_inbox_state(
    db: Session,
    issuer: str,
    subject: str,
    notification_id: str,
    state: str,
    *,
    actor_type: str = "user",
    actor_id: str | None = None,
) -> Notification:
    row = db.scalar(
        owner_notifications_query(issuer, subject).where(Notification.id == notification_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    now = utcnow()
    if state == "read":
        if row.state != "archived":
            row.state = "read"
            row.read_at = row.read_at or now
    elif state == "archived":
        row.state = "archived"
        row.read_at = row.read_at or now
        row.archived_at = now
    else:
        raise ValueError("unsupported inbox state")
    db.add(
        NotificationAuditEvent(
            actor_type=actor_type,
            actor_id=actor_id or subject,
            action=f"notification.{state}",
            aggregate_type="notification",
            aggregate_id=row.id,
            details={},
        )
    )
    db.commit()
    return row


def operations_summary(db: Session) -> OperationsSummary:
    counts = dict(
        db.execute(
            select(NotificationDelivery.state, func.count()).group_by(NotificationDelivery.state)
        ).all()
    )
    oldest = db.scalar(
        select(func.min(NotificationDelivery.created_at)).where(
            NotificationDelivery.state.in_(("pending", "retrying"))
        )
    )
    configured_targets = db.scalar(select(func.count()).select_from(ChannelTarget)) or 0
    enabled_targets = (
        db.scalar(
            select(func.count()).select_from(ChannelTarget).where(ChannelTarget.enabled.is_(True))
        )
        or 0
    )
    unread = (
        db.scalar(
            select(func.count()).select_from(Notification).where(Notification.state == "unread")
        )
        or 0
    )
    failures = db.execute(
        select(NotificationDelivery, Notification, ChannelTarget.label)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .outerjoin(
            ChannelTarget,
            (ChannelTarget.channel == NotificationDelivery.channel)
            & (ChannelTarget.account_id == NotificationDelivery.account_id)
            & (ChannelTarget.target_kind == NotificationDelivery.target_kind)
            & (ChannelTarget.target_id == NotificationDelivery.target_id)
            & (ChannelTarget.thread_id == NotificationDelivery.thread_id),
        )
        .where(NotificationDelivery.state == "dead_letter")
        .order_by(NotificationDelivery.updated_at.desc())
        .limit(20)
    ).all()
    return OperationsSummary(
        generated_at=utcnow(),
        inbox_unread=unread,
        deliveries_pending=counts.get("pending", 0),
        deliveries_retrying=counts.get("retrying", 0),
        deliveries_dead_letter=counts.get("dead_letter", 0),
        oldest_pending_at=oldest,
        configured_targets=configured_targets,
        enabled_targets=enabled_targets,
        probes=[ProbeView.model_validate(row) for row in db.scalars(select(OperationProbe))],
        recent_dead_letters=[
            DeliveryFailureView(
                id=delivery.id,
                notification_id=notification.id,
                title=notification.title,
                source_app_id=notification.source_app_id,
                channel=delivery.channel,
                account_id=delivery.account_id,
                target_label=label or delivery.target_kind,
                attempts=delivery.attempts,
                last_error_code=delivery.last_error_code,
                last_error_detail=delivery.last_error_detail,
                updated_at=delivery.updated_at,
            )
            for delivery, notification, label in failures
        ],
    )


def retry_dead_letter(db: Session, delivery_id: str, actor_id: str) -> DeliveryActionResult:
    delivery = db.get(NotificationDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    if delivery.state != "dead_letter":
        raise HTTPException(status_code=409, detail="delivery is not a dead letter")
    previous_attempts = delivery.attempts
    delivery.state = "pending"
    delivery.attempts = 0
    delivery.available_at = utcnow()
    delivery.locked_at = None
    delivery.locked_by = None
    db.add(
        NotificationAuditEvent(
            actor_type="user",
            actor_id=actor_id,
            action="delivery.retry_requested",
            aggregate_type="delivery",
            aggregate_id=delivery.id,
            details={"previous_attempts": previous_attempts},
        )
    )
    db.commit()
    return DeliveryActionResult(id=delivery.id, state="pending")


def _resolve_short_id(db: Session, issuer: str, subject: str, value: str) -> Notification:
    value = value.strip()
    if len(value) < 6:
        raise HTTPException(status_code=422, detail="notification id prefix is too short")
    rows = list(
        db.scalars(
            owner_notifications_query(issuer, subject).where(Notification.id.like(f"{value}%"))
        )
    )
    if not rows:
        raise HTTPException(status_code=404, detail="notification not found")
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="notification id prefix is ambiguous")
    return rows[0]


def _chat_inbox(db: Session, principal: ChannelPrincipal) -> str:
    rows = list(
        db.scalars(
            owner_notifications_query(principal.recipient_issuer, principal.recipient_subject)
            .where(Notification.state == "unread")
            .order_by(Notification.occurred_at.desc(), Notification.id.desc())
            .limit(5)
        )
    )
    if not rows:
        return "收件箱没有未读通知。"
    lines = [f"未读通知（最近 {len(rows)} 条）："]
    for row in rows:
        lines.append(f"• {row.id[:8]} [{row.source_app_id}] {row.title}")
    lines.append("使用 /read <编号> 标记已读，/archive <编号> 归档。")
    return "\n".join(lines)


def _chat_status(db: Session, detailed: bool) -> str:
    summary = operations_summary(db)
    base = (
        f"Platform：未读 {summary.inbox_unread}，待投递 {summary.deliveries_pending}，"
        f"重试 {summary.deliveries_retrying}，死信 {summary.deliveries_dead_letter}。"
    )
    if not detailed:
        return base
    probe_lines = [
        f"• {probe.service_id}: {probe.status}"
        + (f" {probe.latency_ms}ms" if probe.latency_ms is not None else "")
        for probe in summary.probes
    ]
    return base + ("\n" + "\n".join(probe_lines) if probe_lines else "\n尚无探活结果。")


def handle_chat_command(
    db: Session, gateway_app_id: str, body: ChatCommand, allowed_gateway_apps: tuple[str, ...]
) -> ChatCommandResult:
    if gateway_app_id not in allowed_gateway_apps:
        raise HTTPException(status_code=403, detail="service is not an approved chat gateway")
    previous = db.scalar(
        select(ChatIngressEvent).where(
            ChatIngressEvent.gateway_app_id == gateway_app_id,
            ChatIngressEvent.source_event_id == body.event_id,
        )
    )
    if previous:
        return ChatCommandResult(
            accepted=True, duplicate=True, response_text=previous.response_text
        )
    if body.sender_is_bot:
        raise HTTPException(status_code=403, detail="bot-authored commands are rejected")
    principal = db.scalar(
        select(ChannelPrincipal).where(
            ChannelPrincipal.channel == body.channel,
            ChannelPrincipal.account_id == body.account_id,
            ChannelPrincipal.sender_id == body.sender_id,
            ChannelPrincipal.enabled.is_(True),
        )
    )
    if principal is None:
        raise HTTPException(status_code=403, detail="chat sender is not paired")

    target: ChannelTarget | None = None
    if body.peer_kind != "direct":
        target = db.scalar(
            select(ChannelTarget).where(
                ChannelTarget.channel == body.channel,
                ChannelTarget.account_id == body.account_id,
                ChannelTarget.target_kind == body.peer_kind,
                ChannelTarget.target_id == body.peer_id,
                ChannelTarget.enabled.is_(True),
                ChannelTarget.recipient_issuer == principal.recipient_issuer,
                ChannelTarget.recipient_subject == principal.recipient_subject,
                or_(
                    ChannelTarget.thread_id == body.thread_id,
                    ChannelTarget.thread_id == "",
                ),
            )
        )
        if target is None:
            raise HTTPException(status_code=403, detail="group or channel is not allowlisted")
        if target.require_mention and not body.mentioned:
            raise HTTPException(status_code=403, detail="bot mention required in this group")
        if body.command in {"inbox", "read", "archive"} and target.command_level != "operator":
            raise HTTPException(status_code=403, detail="personal inbox commands are disabled here")

    if body.command == "help":
        response_text = "命令：/inbox、/read <编号>、/archive <编号>、/status。运维私聊可用 /ops。"
    elif body.command == "inbox":
        response_text = _chat_inbox(db, principal)
    elif body.command in {"read", "archive"}:
        row = _resolve_short_id(
            db, principal.recipient_issuer, principal.recipient_subject, body.argument
        )
        set_inbox_state(
            db,
            principal.recipient_issuer,
            principal.recipient_subject,
            row.id,
            body.command,
            actor_type="chat",
            actor_id=f"{body.channel}:{body.sender_id}",
        )
        response_text = (
            f"已{('读' if body.command == 'read' else '归档')}：{row.id[:8]} {row.title}"
        )
    elif body.command == "status":
        response_text = _chat_status(db, detailed=False)
    elif body.command == "ops":
        if body.peer_kind != "direct" or principal.role != "operator":
            raise HTTPException(status_code=403, detail="ops is private-chat operator only")
        response_text = _chat_status(db, detailed=True)
    else:
        raise HTTPException(status_code=422, detail="unsupported command")

    db.add(
        ChatIngressEvent(
            gateway_app_id=gateway_app_id,
            source_event_id=body.event_id,
            channel=body.channel,
            account_id=body.account_id,
            peer_kind=body.peer_kind,
            peer_id=body.peer_id,
            thread_id=body.thread_id,
            sender_id=body.sender_id,
            command=body.command,
            response_text=response_text,
        )
    )
    db.commit()
    return ChatCommandResult(accepted=True, response_text=response_text)
