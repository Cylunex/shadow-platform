from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import and_, func, or_, select, text

from .auth import (
    AdminDep,
    DbDep,
    ServiceDep,
    UserDep,
    current_user,
    require_csrf,
)
from .config import Settings
from .database import Base, create_database
from .models import Notification
from .oidc import router as oidc_router
from .schemas import (
    ChatCommand,
    ChatCommandResult,
    DeliveryActionResult,
    InboxActionResult,
    InboxPage,
    NotificationAccepted,
    NotificationCreate,
    NotificationView,
    OperationsSummary,
)
from .service import (
    handle_chat_command,
    operations_summary,
    owner_notifications_query,
    publish_notification,
    retry_dead_letter,
    set_inbox_state,
    sync_channel_configuration,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(PACKAGE_ROOT / "templates"),
    autoescape=select_autoescape(("html", "xml")),
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    engine, session_factory = create_database(resolved.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if resolved.environment != "production":
            Base.metadata.create_all(engine)
        with session_factory() as db:
            sync_channel_configuration(db, resolved.channel_config)
        yield
        engine.dispose()

    expose_docs = resolved.environment != "production"
    app = FastAPI(
        title="Shadow Notifications",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = resolved
    app.state.session_factory = session_factory
    app.include_router(oidc_router)
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith(("/inbox", "/operations", "/v1/inbox", "/v1/operations")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(db: DbDep):
        try:
            db.execute(text("SELECT 1 FROM notifications LIMIT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {
            "status": "ready",
            "channel_accounts": len(resolved.channel_config.accounts),
        }

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/inbox", status_code=302)

    @app.post(
        "/v1/notifications",
        response_model=NotificationAccepted,
        status_code=202,
    )
    def publish(body: NotificationCreate, app_id: ServiceDep, db: DbDep):
        return publish_notification(db, app_id, body)

    @app.get("/v1/inbox", response_model=InboxPage)
    def inbox_api(
        user: UserDep,
        db: DbDep,
        state: Annotated[str | None, Query(pattern="^(unread|read|archived)$")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ):
        query = owner_notifications_query(user.issuer, user.subject).where(
            or_(Notification.expires_at.is_(None), Notification.expires_at > utcnow())
        )
        if state:
            query = query.where(Notification.state == state)
        else:
            query = query.where(Notification.state != "archived")
        if cursor:
            cursor_row = db.scalar(
                owner_notifications_query(user.issuer, user.subject).where(
                    Notification.id == cursor
                )
            )
            if cursor_row is None:
                raise HTTPException(status_code=422, detail="invalid inbox cursor")
            query = query.where(
                or_(
                    Notification.occurred_at < cursor_row.occurred_at,
                    and_(
                        Notification.occurred_at == cursor_row.occurred_at,
                        Notification.id < cursor_row.id,
                    ),
                )
            )
        rows = list(
            db.scalars(
                query.order_by(Notification.occurred_at.desc(), Notification.id.desc()).limit(
                    limit + 1
                )
            )
        )
        next_cursor = rows[limit - 1].id if len(rows) > limit else None
        rows = rows[:limit]
        unread = (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.recipient_issuer == user.issuer,
                    Notification.recipient_subject == user.subject,
                    Notification.state == "unread",
                    or_(Notification.expires_at.is_(None), Notification.expires_at > utcnow()),
                )
            )
            or 0
        )
        return InboxPage(
            items=[NotificationView.model_validate(row) for row in rows],
            unread_count=unread,
            next_cursor=next_cursor,
        )

    @app.post("/v1/inbox/{notification_id}/read", response_model=InboxActionResult)
    def mark_read(
        notification_id: str,
        request: Request,
        user: UserDep,
        db: DbDep,
        _: None = Depends(require_csrf),
    ):
        row = set_inbox_state(db, user.issuer, user.subject, notification_id, "read")
        return InboxActionResult(id=row.id, state=row.state)

    @app.post("/v1/inbox/{notification_id}/archive", response_model=InboxActionResult)
    def archive(
        notification_id: str,
        request: Request,
        user: UserDep,
        db: DbDep,
        _: None = Depends(require_csrf),
    ):
        row = set_inbox_state(db, user.issuer, user.subject, notification_id, "archived")
        return InboxActionResult(id=row.id, state=row.state)

    @app.post("/v1/chat/commands", response_model=ChatCommandResult)
    def chat_command(body: ChatCommand, gateway_app_id: ServiceDep, db: DbDep):
        return handle_chat_command(db, gateway_app_id, body, resolved.chat_gateway_apps)

    @app.get("/v1/operations", response_model=OperationsSummary)
    def operations(_: AdminDep, db: DbDep):
        return operations_summary(db)

    @app.post(
        "/v1/operations/deliveries/{delivery_id}/retry",
        response_model=DeliveryActionResult,
    )
    def retry_delivery(
        delivery_id: str,
        request: Request,
        user: AdminDep,
        db: DbDep,
        _: None = Depends(require_csrf),
    ):
        return retry_dead_letter(db, delivery_id, user.subject)

    def page_user(request: Request, db):
        try:
            return current_user(request, db)
        except HTTPException as exc:
            if exc.status_code == 401:
                return None
            raise

    @app.get("/inbox", response_class=HTMLResponse, include_in_schema=False)
    def inbox_page(request: Request, db: DbDep):
        user = page_user(request, db)
        if user is None:
            return RedirectResponse("/login?return_to=/inbox", status_code=302)
        rows = list(
            db.scalars(
                owner_notifications_query(user.issuer, user.subject)
                .where(
                    Notification.state != "archived",
                    or_(Notification.expires_at.is_(None), Notification.expires_at > utcnow()),
                )
                .order_by(Notification.occurred_at.desc(), Notification.id.desc())
                .limit(100)
            )
        )
        html = templates.get_template("inbox.html").render(
            user=user,
            items=rows,
            unread_count=sum(1 for row in rows if row.state == "unread"),
            is_admin=resolved.admin_group in user.groups,
        )
        return HTMLResponse(html)

    @app.get("/operations", response_class=HTMLResponse, include_in_schema=False)
    def operations_page(request: Request, db: DbDep):
        user = page_user(request, db)
        if user is None:
            return RedirectResponse("/login?return_to=/operations", status_code=302)
        if resolved.admin_group not in user.groups:
            raise HTTPException(status_code=403, detail="platform administrator group required")
        html = templates.get_template("operations.html").render(
            user=user, summary=operations_summary(db)
        )
        return HTMLResponse(html)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("notification_service.app:app", host="127.0.0.1", port=8420)
