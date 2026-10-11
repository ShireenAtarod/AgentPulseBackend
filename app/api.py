import asyncio
import json
import math
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from redis import Redis
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import (
    Agent,
    AgentSession,
    ExecutionEvent,
    SessionStatus,
    User,
    WebVitalObservation,
    WebVitalSource,
    utcnow,
)
from app.redis_client import get_redis
from app.schemas import (
    AgentOut,
    AgentPatch,
    AgentSessionOut,
    CsrfOut,
    EventCreateOut,
    EventIn,
    ExperimentRecord,
    IngestOut,
    LoginIn,
    LoginOut,
    PaginatedAgents,
    PaginatedSessions,
    SessionDetail,
    SummaryOut,
    TimeseriesOut,
    TimeseriesPoint,
    UserOut,
    WebVitalBatch,
    WebVitalsSummaryItem,
    WebVitalsSummaryOut,
)
from app.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    clear_auth_cookies,
    create_session,
    csrf_for_session,
    current_user,
    require_admin,
    require_csrf,
    require_trusted_origin,
    set_auth_cookies,
    verify_password,
)

router = APIRouter()
api = APIRouter(prefix="/api/v1")


def parse_date_range(date_from: datetime | None, date_to: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = date_from or (now - timedelta(days=7))
    end = date_to or now
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Dates must include timezone")
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be before date_to")
    if end - start > timedelta(days=90):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Date range may not exceed 90 days")
    return start, end


def paginate(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "page must be >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "page_size must be 1..100")
    return (page - 1) * page_size, page_size


def rate_limit(redis_client: Redis, key: str, limit: int, window: int) -> None:
    current = redis_client.incr(key)
    if current == 1:
        redis_client.expire(key, window)
    if current > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")


def safe_cache_key(prefix: str, data: dict[str, Any]) -> str:
    return f"{prefix}:{json.dumps(data, sort_keys=True, default=str)}"


def cache_get(redis_client: Redis, settings: Settings, key: str) -> tuple[str, Any | None, float]:
    if not settings.metrics_cache_enabled:
        return "BYPASS", None, 0
    start = time.perf_counter()
    raw = redis_client.get(key)
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    if raw is None:
        return "MISS", None, elapsed
    return "HIT", json.loads(raw), elapsed


def cache_set(redis_client: Redis, settings: Settings, key: str, value: Any) -> None:
    if settings.metrics_cache_enabled:
        redis_client.setex(key, settings.metrics_cache_ttl_seconds, json.dumps(value, default=str))


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db), redis_client: Redis = Depends(get_redis)) -> dict[str, Any]:
    problems: list[str] = []
    try:
        db.execute(select(1))
    except Exception as exc:
        problems.append(f"postgres: {exc.__class__.__name__}")
    try:
        redis_client.ping()
    except Exception as exc:
        problems.append(f"redis: {exc.__class__.__name__}")
    if problems:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"status": "not_ready", "problems": problems})
    return {"status": "ready"}


@api.post("/auth/login", response_model=LoginOut, dependencies=[Depends(require_trusted_origin)])
def login(
    payload: LoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> LoginOut:
    rate_limit(
        redis_client,
        f"rate:login:{request.client.host if request.client else 'unknown'}",
        settings.login_rate_limit,
        settings.rate_limit_window_seconds,
    )
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    token = create_session(redis_client, user, settings)
    csrf = set_auth_cookies(response, token, settings)
    return LoginOut(user=UserOut.model_validate(user), csrf_token=csrf)


@api.post("/auth/logout", dependencies=[Depends(require_trusted_origin), Depends(require_csrf)])
def logout(
    request: Request, response: Response, redis_client: Redis = Depends(get_redis)
) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        redis_client.delete(f"session:{token}")
    clear_auth_cookies(response)
    return {"ok": True}


@api.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@api.get("/auth/csrf", response_model=CsrfOut)
def csrf(request: Request, settings: Settings = Depends(get_settings), _: User = Depends(current_user)) -> CsrfOut:
    token = request.cookies.get(SESSION_COOKIE) or ""
    return CsrfOut(csrf_token=csrf_for_session(token, settings.app_secret_key), header_name=CSRF_HEADER)


@api.get("/agents", response_model=PaginatedAgents)
def list_agents(
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> PaginatedAgents:
    offset, limit = paginate(page, page_size)
    stmt = select(Agent).order_by(Agent.name.asc(), Agent.id.asc())
    total = db.scalar(select(func.count()).select_from(Agent)) or 0
    items = db.scalars(stmt.offset(offset).limit(limit)).all()
    return PaginatedAgents(items=items, total=total, page=page, page_size=page_size)


@api.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(current_user)) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


@api.patch(
    "/agents/{agent_id}",
    response_model=AgentOut,
    dependencies=[Depends(require_trusted_origin), Depends(require_csrf)],
)
def patch_agent(
    agent_id: uuid.UUID,
    payload: AgentPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    agent.enabled = payload.enabled
    agent.updated_at = utcnow()
    db.commit()
    db.refresh(agent)
    return agent


def session_filters(
    agent_id: uuid.UUID | None,
    status_value: SessionStatus | None,
    device: str | None,
    browser: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    search: str | None,
) -> list[Any]:
    start, end = parse_date_range(date_from, date_to)
    filters: list[Any] = [AgentSession.started_at >= start, AgentSession.started_at < end]
    if agent_id:
        filters.append(AgentSession.agent_id == agent_id)
    if status_value:
        filters.append(AgentSession.status == status_value)
    if device:
        filters.append(AgentSession.device_category == device)
    if browser:
        filters.append(AgentSession.browser == browser)
    if search:
        like = f"%{search}%"
        filters.append(
            or_(AgentSession.synthetic_user_id.ilike(like), AgentSession.error_message.ilike(like))
        )
    return filters


@api.get("/sessions", response_model=PaginatedSessions)
def list_sessions(
    agent_id: uuid.UUID | None = None,
    status_value: SessionStatus | None = Query(None, alias="status"),
    device: str | None = None,
    browser: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    search: str | None = Query(None, max_length=120),
    sort: str = Query("-started_at"),
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> PaginatedSessions:
    offset, limit = paginate(page, page_size)
    filters = session_filters(agent_id, status_value, device, browser, date_from, date_to, search)
    sort_map = {
        "started_at": AgentSession.started_at,
        "duration_ms": AgentSession.duration_ms,
        "token_usage": AgentSession.token_usage,
        "estimated_cost_usd": AgentSession.estimated_cost_usd,
        "status": AgentSession.status,
    }
    descending = sort.startswith("-")
    sort_name = sort[1:] if descending else sort
    if sort_name not in sort_map:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unsupported sort field")
    sort_col = sort_map[sort_name]
    order = desc(sort_col) if descending else asc(sort_col)
    total = db.scalar(select(func.count()).select_from(AgentSession).where(and_(*filters))) or 0
    items = db.scalars(
        select(AgentSession).where(and_(*filters)).order_by(order, AgentSession.id.asc()).offset(offset).limit(limit)
    ).all()
    return PaginatedSessions(items=items, total=total, page=page, page_size=page_size)


@api.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: uuid.UUID,
    events_page: int = 1,
    events_page_size: int = 25,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> SessionDetail:
    _, limit = paginate(events_page, events_page_size)
    session = db.get(AgentSession, session_id)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    total = db.scalar(select(func.count()).select_from(ExecutionEvent).where(ExecutionEvent.session_id == session_id)) or 0
    events = db.scalars(
        select(ExecutionEvent)
        .where(ExecutionEvent.session_id == session_id)
        .order_by(ExecutionEvent.timestamp.asc(), ExecutionEvent.id.asc())
        .offset((events_page - 1) * limit)
        .limit(limit)
    ).all()
    base = AgentSessionOut.model_validate(session).model_dump()
    return SessionDetail(**base, events=events, events_total=total, events_page=events_page, events_page_size=limit)


def percentile(values: list[int | float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[int(index)])
    return float(ordered[lower] * (upper - index) + ordered[upper] * (index - lower))


def build_summary(db: Session, filters: list[Any], start: datetime, end: datetime) -> dict[str, Any]:
    rows = db.scalars(select(AgentSession).where(and_(*filters))).all()
    completed = [row for row in rows if row.status == SessionStatus.completed]
    failed = [row for row in rows if row.status == SessionStatus.failed]
    durations = [row.duration_ms for row in rows if row.duration_ms is not None]
    minutes = max((end - start).total_seconds() / 60, 1)
    return {
        "active_sessions": sum(1 for row in rows if row.status == SessionStatus.running),
        "execution_rate_per_minute": round(sum(row.execution_count for row in rows) / minutes, 4),
        "success_rate": round(len(completed) / len(rows), 4) if rows else 0,
        "error_rate": round(len(failed) / len(rows), 4) if rows else 0,
        "latency_ms": {
            "p50": percentile(durations, 0.50),
            "p75": percentile(durations, 0.75),
            "p95": percentile(durations, 0.95),
        },
        "token_usage": sum(row.token_usage for row in rows),
        "estimated_cost_usd": round(sum(row.estimated_cost_usd for row in rows), 6),
        "device_distribution": dict(Counter(row.device_category for row in rows)),
        "browser_distribution": dict(Counter(row.browser for row in rows)),
    }


@api.get("/metrics/summary", response_model=SummaryOut)
def metrics_summary(
    agent_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    _: User = Depends(current_user),
) -> SummaryOut:
    start, end = parse_date_range(date_from, date_to)
    filters = session_filters(agent_id, None, None, None, start, end, None)
    key = safe_cache_key(
        "metrics:summary",
        {"agent_id": agent_id, "date_from": date_from, "date_to": date_to},
    )
    cache_status, cached, cache_ms = cache_get(redis_client, settings, key)
    if cached is not None:
        cached["cache"] = {"status": cache_status, "cache_ms": cache_ms}
        return SummaryOut(**cached)
    query_start = time.perf_counter()
    data = build_summary(db, filters, start, end)
    query_ms = round((time.perf_counter() - query_start) * 1000, 2)
    cache_set(redis_client, settings, key, data)
    data["cache"] = {"status": cache_status, "cache_ms": cache_ms, "query_ms": query_ms}
    return SummaryOut(**data)


@api.get("/metrics/timeseries", response_model=TimeseriesOut)
def metrics_timeseries(
    granularity: str = Query("hour", pattern="^(minute|hour|day)$"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    _: User = Depends(current_user),
) -> TimeseriesOut:
    start, end = parse_date_range(date_from, date_to)
    step = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1)}[granularity]
    points = math.ceil((end - start) / step)
    if points > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Timeseries may not exceed 500 points")
    key = safe_cache_key(
        "metrics:timeseries",
        {"granularity": granularity, "date_from": date_from, "date_to": date_to},
    )
    cache_status, cached, cache_ms = cache_get(redis_client, settings, key)
    if cached is not None:
        return TimeseriesOut(granularity=granularity, items=cached, cache={"status": cache_status, "cache_ms": cache_ms})
    rows = db.scalars(
        select(AgentSession).where(AgentSession.started_at >= start, AgentSession.started_at < end)
    ).all()
    buckets: list[TimeseriesPoint] = []
    cursor = start
    while cursor < end:
        next_cursor = min(cursor + step, end)
        bucket_rows = [row for row in rows if cursor <= row.started_at < next_cursor]
        durations = [row.duration_ms for row in bucket_rows if row.duration_ms is not None]
        buckets.append(
            TimeseriesPoint(
                bucket_start=cursor,
                sessions=len(bucket_rows),
                executions=sum(row.execution_count for row in bucket_rows),
                errors=sum(1 for row in bucket_rows if row.status == SessionStatus.failed),
                p95_latency_ms=percentile(durations, 0.95),
            )
        )
        cursor = next_cursor
    raw = [item.model_dump() for item in buckets]
    cache_set(redis_client, settings, key, raw)
    return TimeseriesOut(granularity=granularity, items=buckets, cache={"status": cache_status, "cache_ms": cache_ms})


@api.get("/metrics/live")
async def metrics_live(
    request: Request,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    _: User = Depends(current_user),
) -> StreamingResponse:
    async def events():
        while not await request.is_disconnected():
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=15)
            data = metrics_summary(None, start, end, db, redis_client, settings, _)
            yield f"event: summary\ndata: {data.model_dump_json()}\n\n"
            yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(events(), media_type="text/event-stream")


@api.post(
    "/events",
    response_model=EventCreateOut,
    dependencies=[Depends(require_trusted_origin), Depends(require_csrf)],
)
def create_event(
    payload: EventIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> EventCreateOut:
    if not db.get(AgentSession, payload.session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    event = ExecutionEvent(**payload.model_dump())
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(ExecutionEvent).where(
                ExecutionEvent.session_id == payload.session_id,
                ExecutionEvent.client_event_id == payload.client_event_id,
            )
        )
        return EventCreateOut(id=existing.id, deduplicated=True)
    db.refresh(event)
    return EventCreateOut(id=event.id, deduplicated=False)


@api.post(
    "/web-vitals",
    response_model=IngestOut,
    dependencies=[Depends(require_trusted_origin), Depends(require_csrf)],
)
def web_vitals(
    payload: WebVitalBatch,
    request: Request,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    _: User = Depends(current_user),
) -> IngestOut:
    rate_limit(
        redis_client,
        f"rate:ingest:{request.client.host if request.client else 'unknown'}",
        settings.ingest_rate_limit,
        settings.rate_limit_window_seconds,
    )
    accepted = 0
    deduped = 0
    for item in payload.observations:
        existing = db.get(WebVitalObservation, item.observation_id) or db.scalar(
            select(WebVitalObservation).where(
                WebVitalObservation.metric_id == item.metric_id,
                WebVitalObservation.browser_session_id == item.browser_session_id,
            )
        )
        if existing:
            for key, value in item.model_dump().items():
                setattr(existing, key, value)
            existing.updated_at = utcnow()
            deduped += 1
        else:
            db.add(WebVitalObservation(**item.model_dump()))
            accepted += 1
    db.commit()
    return IngestOut(accepted=accepted, deduplicated=deduped)


@api.get("/web-vitals/summary", response_model=WebVitalsSummaryOut)
def web_vitals_summary(
    metric_name: str | None = None,
    route_path: str | None = None,
    device: str | None = None,
    include_synthetic: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> WebVitalsSummaryOut:
    start, end = parse_date_range(date_from, date_to)
    filters: list[Any] = [WebVitalObservation.timestamp >= start, WebVitalObservation.timestamp < end]
    if not include_synthetic:
        filters.append(WebVitalObservation.source == WebVitalSource.actual)
    if metric_name:
        filters.append(WebVitalObservation.metric_name == metric_name)
    if route_path:
        filters.append(WebVitalObservation.route_path == route_path)
    if device:
        filters.append(WebVitalObservation.device_category == device)
    rows = db.scalars(select(WebVitalObservation).where(and_(*filters))).all()
    items = []
    for name in ["LCP", "CLS", "INP", "FCP", "TTFB"]:
        values = [row.value for row in rows if row.metric_name == name]
        if values or not metric_name:
            items.append(WebVitalsSummaryItem(metric_name=name, sample_count=len(values), p75=percentile(values, 0.75)))
    return WebVitalsSummaryOut(items=items)


@api.get("/experiments/session-metrics", response_model=list[ExperimentRecord])
def experiment_records(
    limit: int = Query(50_000, ge=1),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: User = Depends(current_user),
) -> list[ExperimentRecord]:
    if not settings.experiments_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Experiment endpoint disabled")
    limit = min(limit, settings.experiment_max_records)
    rows = db.scalars(select(AgentSession).order_by(AgentSession.started_at.desc()).limit(limit)).all()
    status_codes = {"running": 0, "completed": 1, "failed": 2, "cancelled": 3}
    device_codes = {"desktop": 0, "mobile": 1, "tablet": 2}
    browser_codes = {"Chrome": 0, "Safari": 1, "Firefox": 2, "Edge": 3}
    return [
        ExperimentRecord(
            session_index=index,
            started_epoch_ms=int(row.started_at.timestamp() * 1000),
            duration_ms=row.duration_ms or 0,
            status_code=status_codes[row.status.value],
            device_code=device_codes.get(row.device_category, 9),
            browser_code=browser_codes.get(row.browser, 9),
            token_usage=row.token_usage,
            estimated_cost_micros=int(row.estimated_cost_usd * 1_000_000),
        )
        for index, row in enumerate(rows)
    ]


router.include_router(api)

