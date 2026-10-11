import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import EventOutcome, Role, SessionStatus, WebVitalSource


class ErrorResponse(BaseModel):
    error: dict[str, str]
    request_id: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    role: Role


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=256)


class LoginOut(BaseModel):
    user: UserOut
    csrf_token: str


class CsrfOut(BaseModel):
    csrf_token: str
    header_name: str = "x-csrf-token"


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str
    category: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentPatch(BaseModel):
    enabled: bool


class ExecutionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    session_id: uuid.UUID
    client_event_id: str
    event_type: str
    timestamp: datetime
    duration_ms: int | None
    outcome: EventOutcome
    token_usage: int
    details: dict[str, Any]


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    agent_id: uuid.UUID
    synthetic_user_id: str
    status: SessionStatus
    started_at: datetime
    ended_at: datetime | None
    device_category: str
    browser: str
    operating_system: str
    country: str
    network_profile: str
    execution_count: int
    token_usage: int
    estimated_cost_usd: float
    duration_ms: int | None
    error_code: str | None
    error_message: str | None


class SessionDetail(AgentSessionOut):
    events: list[ExecutionEventOut]
    events_total: int
    events_page: int
    events_page_size: int


class PaginatedSessions(BaseModel):
    items: list[AgentSessionOut]
    total: int
    page: int
    page_size: int


class PaginatedAgents(BaseModel):
    items: list[AgentOut]
    total: int
    page: int
    page_size: int


class EventIn(BaseModel):
    session_id: uuid.UUID
    client_event_id: str = Field(min_length=1, max_length=160)
    event_type: str = Field(min_length=1, max_length=80)
    timestamp: datetime
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    outcome: EventOutcome
    token_usage: int = Field(ge=0, le=1_000_000)
    details: dict[str, Any] = Field(default_factory=dict)


class EventCreateOut(BaseModel):
    id: uuid.UUID
    deduplicated: bool


MetricName = Literal["LCP", "CLS", "INP", "FCP", "TTFB"]


class WebVitalIn(BaseModel):
    observation_id: str = Field(min_length=1, max_length=160)
    metric_id: str = Field(min_length=1, max_length=160)
    metric_name: MetricName
    value: float = Field(ge=0)
    route_path: str = Field(min_length=1, max_length=240)
    browser_session_id: str = Field(min_length=1, max_length=160)
    device_category: str | None = Field(default=None, max_length=40)
    browser: str | None = Field(default=None, max_length=80)
    operating_system: str | None = Field(default=None, max_length=80)
    app_release: str = Field(min_length=1, max_length=80)
    timestamp: datetime
    source: WebVitalSource = WebVitalSource.actual

    @field_validator("route_path")
    @classmethod
    def route_has_no_query(cls, value: str) -> str:
        if "?" in value or "://" in value:
            raise ValueError("route_path must be a path without query strings or origins")
        return value

    @field_validator("value")
    @classmethod
    def cls_reasonable(cls, value: float, info) -> float:
        if info.data.get("metric_name") == "CLS" and value > 10:
            raise ValueError("CLS is unitless and must be within a reasonable bound")
        if info.data.get("metric_name") != "CLS" and value > 120_000:
            raise ValueError("timing metrics are milliseconds and capped at 120000")
        return value


class WebVitalBatch(BaseModel):
    observations: list[WebVitalIn] = Field(min_length=1, max_length=100)


class IngestOut(BaseModel):
    accepted: int
    deduplicated: int


class SummaryOut(BaseModel):
    active_sessions: int
    execution_rate_per_minute: float
    success_rate: float
    error_rate: float
    latency_ms: dict[str, float | None]
    token_usage: int
    estimated_cost_usd: float
    device_distribution: dict[str, int]
    browser_distribution: dict[str, int]
    cache: dict[str, Any]


class TimeseriesPoint(BaseModel):
    bucket_start: datetime
    sessions: int
    executions: int
    errors: int
    p95_latency_ms: float | None


class TimeseriesOut(BaseModel):
    granularity: str
    items: list[TimeseriesPoint]
    cache: dict[str, Any]


class WebVitalsSummaryItem(BaseModel):
    metric_name: str
    sample_count: int
    p75: float | None


class WebVitalsSummaryOut(BaseModel):
    items: list[WebVitalsSummaryItem]


class ExperimentRecord(BaseModel):
    session_index: int
    started_epoch_ms: int
    duration_ms: int
    status_code: int
    device_code: int
    browser_code: int
    token_usage: int
    estimated_cost_micros: int

