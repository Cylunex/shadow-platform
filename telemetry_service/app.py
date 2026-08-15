from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shadow_sdk.service_auth import ServiceAuthError, authenticate_service_token

from .config import Settings
from .database import Base, create_database
from .models import LLMUsage
from .schemas import IngestResult, UsageBatch, UsageSummary, UsageSummaryBucket


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def require_service(request: Request) -> str:
    try:
        return authenticate_service_token(
            request.headers.get("authorization", ""),
            request.app.state.settings.service_token_hashes,
        )
    except ServiceAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


DbDep = Annotated[Session, Depends(get_db)]
ServiceDep = Annotated[str, Depends(require_service)]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    engine, session_factory = create_database(resolved.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    expose_docs = resolved.environment != "production"
    app = FastAPI(
        title="Shadow LLM Telemetry",
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = resolved
    app.state.session_factory = session_factory

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(db: DbDep):
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    @app.post("/v1/llm-usage/events", response_model=IngestResult, status_code=202)
    def ingest_usage(body: UsageBatch, app_id: ServiceDep, db: DbDep):
        if any(event.app_id != app_id for event in body.events):
            raise HTTPException(status_code=403, detail="event app_id does not match token")

        accepted = 0
        duplicates = 0
        received_at = utcnow()
        for event in body.events:
            try:
                with db.begin_nested():
                    db.add(LLMUsage(**event.model_dump(), received_at=received_at))
                    db.flush()
            except IntegrityError:
                duplicates += 1
            else:
                accepted += 1
        db.commit()
        return IngestResult(accepted=accepted, duplicates=duplicates)

    @app.get("/v1/llm-usage/summary", response_model=UsageSummary)
    def usage_summary(
        app_id: ServiceDep,
        db: DbDep,
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
    ):
        end = end or utcnow()
        start = start or end - timedelta(days=30)
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(status_code=400, detail="start and end must include timezones")
        if start >= end or end - start > timedelta(days=366):
            raise HTTPException(status_code=400, detail="invalid summary time range")
        rows = db.scalars(
            select(LLMUsage).where(
                LLMUsage.app_id == app_id,
                LLMUsage.started_at >= start,
                LLMUsage.started_at < end,
            )
        )
        totals: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for row in rows:
            key = (row.model_alias, row.provider, row.actual_model, row.status)
            bucket = totals[key]
            bucket["request_count"] += 1
            bucket["input_tokens"] += row.input_tokens or 0
            bucket["output_tokens"] += row.output_tokens or 0
            bucket["cached_tokens"] += row.cached_tokens or 0
            bucket["total_latency_ms"] += row.latency_ms
            bucket["retry_count"] += row.retry_count
        buckets = [
            UsageSummaryBucket(
                model_alias=key[0],
                provider=key[1],
                actual_model=key[2],
                status=key[3],
                **counts,
            )
            for key, counts in sorted(totals.items())
        ]
        return UsageSummary(app_id=app_id, start=start, end=end, buckets=buckets)

    return app


app = create_app()
