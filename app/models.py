import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    viewer = "viewer"
    admin = "admin"


class SessionStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class EventOutcome(str, enum.Enum):
    success = "success"
    error = "error"
    skipped = "skipped"


class WebVitalSource(str, enum.Enum):
    actual = "actual"
    synthetic_fixture = "synthetic_fixture"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), nullable=False, default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sessions: Mapped[list["AgentSession"]] = relationship(back_populates="agent")


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    synthetic_user_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus, name="session_status"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    device_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    browser: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    operating_system: Mapped[str] = mapped_column(String(80), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    network_profile: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_usage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agent: Mapped[Agent] = relationship(back_populates="sessions")
    events: Mapped[list["ExecutionEvent"]] = relationship(back_populates="session")

    __table_args__ = (
        Index("ix_sessions_filters_time", "status", "device_category", "browser", "started_at"),
        Index("ix_sessions_agent_time", "agent_id", "started_at"),
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    client_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[EventOutcome] = mapped_column(Enum(EventOutcome, name="event_outcome"))
    token_usage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    session: Mapped[AgentSession] = relationship(back_populates="events")

    __table_args__ = (UniqueConstraint("session_id", "client_event_id", name="uq_event_dedupe"),)


class WebVitalObservation(Base):
    __tablename__ = "web_vital_observations"

    observation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    metric_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    route_path: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    browser_session_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    device_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operating_system: Mapped[str | None] = mapped_column(String(80), nullable=True)
    app_release: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[WebVitalSource] = mapped_column(
        Enum(WebVitalSource, name="web_vital_source"), default=WebVitalSource.actual
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("metric_id", "browser_session_id", name="uq_vital_metric_session"),
        Index("ix_vitals_summary", "metric_name", "source", "timestamp", "route_path", "device_category"),
    )

