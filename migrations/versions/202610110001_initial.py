"""Initial AgentPulse schema.

Revision ID: 202610110001
Revises:
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202610110001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = postgresql.ENUM("viewer", "admin", name="role")
    session_status = postgresql.ENUM("running", "completed", "failed", "cancelled", name="session_status")
    event_outcome = postgresql.ENUM("success", "error", "skipped", name="event_outcome")
    web_vital_source = postgresql.ENUM("actual", "synthetic_fixture", name="web_vital_source")
    role.create(op.get_bind(), checkfirst=True)
    session_status.create(op.get_bind(), checkfirst=True)
    event_outcome.create(op.get_bind(), checkfirst=True)
    web_vital_source.create(op.get_bind(), checkfirst=True)
    role = postgresql.ENUM("viewer", "admin", name="role", create_type=False)
    session_status = postgresql.ENUM(
        "running", "completed", "failed", "cancelled", name="session_status", create_type=False
    )
    event_outcome = postgresql.ENUM("success", "error", "skipped", name="event_outcome", create_type=False)
    web_vital_source = postgresql.ENUM(
        "actual", "synthetic_fixture", name="web_vital_source", create_type=False
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agents_category", "agents", ["category"])
    op.create_index("ix_agents_enabled", "agents", ["enabled"])

    op.create_table(
        "agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("synthetic_user_id", sa.String(80), nullable=False),
        sa.Column("status", session_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_category", sa.String(40), nullable=False),
        sa.Column("browser", sa.String(80), nullable=False),
        sa.Column("operating_system", sa.String(80), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("network_profile", sa.String(40), nullable=False),
        sa.Column("execution_count", sa.Integer(), nullable=False),
        sa.Column("token_usage", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
    )
    op.create_index("ix_agent_sessions_agent_id", "agent_sessions", ["agent_id"])
    op.create_index("ix_agent_sessions_synthetic_user_id", "agent_sessions", ["synthetic_user_id"])
    op.create_index("ix_agent_sessions_status", "agent_sessions", ["status"])
    op.create_index("ix_agent_sessions_started_at", "agent_sessions", ["started_at"])
    op.create_index("ix_agent_sessions_ended_at", "agent_sessions", ["ended_at"])
    op.create_index("ix_agent_sessions_device_category", "agent_sessions", ["device_category"])
    op.create_index("ix_agent_sessions_browser", "agent_sessions", ["browser"])
    op.create_index("ix_agent_sessions_country", "agent_sessions", ["country"])
    op.create_index("ix_sessions_filters_time", "agent_sessions", ["status", "device_category", "browser", "started_at"])
    op.create_index("ix_sessions_agent_time", "agent_sessions", ["agent_id", "started_at"])

    op.create_table(
        "execution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_sessions.id"), nullable=False),
        sa.Column("client_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", event_outcome, nullable=False),
        sa.Column("token_usage", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("session_id", "client_event_id", name="uq_event_dedupe"),
    )
    op.create_index("ix_execution_events_session_id", "execution_events", ["session_id"])
    op.create_index("ix_execution_events_event_type", "execution_events", ["event_type"])
    op.create_index("ix_execution_events_timestamp", "execution_events", ["timestamp"])

    op.create_table(
        "web_vital_observations",
        sa.Column("observation_id", sa.String(160), primary_key=True),
        sa.Column("metric_id", sa.String(160), nullable=False),
        sa.Column("metric_name", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("route_path", sa.String(240), nullable=False),
        sa.Column("browser_session_id", sa.String(160), nullable=False),
        sa.Column("device_category", sa.String(40), nullable=True),
        sa.Column("browser", sa.String(80), nullable=True),
        sa.Column("operating_system", sa.String(80), nullable=True),
        sa.Column("app_release", sa.String(80), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", web_vital_source, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("metric_id", "browser_session_id", name="uq_vital_metric_session"),
    )
    op.create_index("ix_web_vital_observations_metric_id", "web_vital_observations", ["metric_id"])
    op.create_index("ix_web_vital_observations_metric_name", "web_vital_observations", ["metric_name"])
    op.create_index("ix_web_vital_observations_route_path", "web_vital_observations", ["route_path"])
    op.create_index("ix_web_vital_observations_browser_session_id", "web_vital_observations", ["browser_session_id"])
    op.create_index("ix_web_vital_observations_app_release", "web_vital_observations", ["app_release"])
    op.create_index("ix_web_vital_observations_timestamp", "web_vital_observations", ["timestamp"])
    op.create_index(
        "ix_vitals_summary",
        "web_vital_observations",
        ["metric_name", "source", "timestamp", "route_path", "device_category"],
    )


def downgrade() -> None:
    op.drop_table("web_vital_observations")
    op.drop_table("execution_events")
    op.drop_table("agent_sessions")
    op.drop_table("agents")
    op.drop_table("users")
    for name in ["web_vital_source", "event_outcome", "session_status", "role"]:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)

