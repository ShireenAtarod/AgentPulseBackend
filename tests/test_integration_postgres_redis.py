import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import Base, get_db
from app.main import create_app
from app.models import Agent, AgentSession, Role, SessionStatus, User, WebVitalObservation
from app.redis_client import get_redis
from app.security import CSRF_HEADER, SESSION_COOKIE, hash_password, session_key

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENTPULSE_TEST_DATABASE_URL") or not os.getenv("AGENTPULSE_TEST_REDIS_URL"),
    reason="Set AGENTPULSE_TEST_DATABASE_URL and AGENTPULSE_TEST_REDIS_URL to run integration tests.",
)

ORIGIN = "http://testserver"
ADMIN_EMAIL = "admin@example.test"
VIEWER_EMAIL = "viewer@example.test"
PASSWORD = "correct-password"


@dataclass
class IntegrationContext:
    client: TestClient
    redis_client: redis.Redis
    session_factory: Any
    agent_ids: dict[str, str]
    session_ids: dict[str, str]


def assert_error(response, status_code: int, code: str, message: str | None = None) -> None:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["error"]["code"] == code
    if message is not None:
        assert body["error"]["message"] == message
    assert body["request_id"]


@pytest.fixture()
def context(monkeypatch, request):
    options = getattr(request, "param", {})
    database_url = os.environ["AGENTPULSE_TEST_DATABASE_URL"]
    redis_url = os.environ["AGENTPULSE_TEST_REDIS_URL"]
    database_name = make_url(database_url).database or ""
    if not database_name.startswith("agentpulse_test"):
        pytest.fail("Integration tests require a database whose name starts with 'agentpulse_test'.")

    redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
    redis_database = int(redis_client.connection_pool.connection_kwargs.get("db", 0))
    if redis_database == 0:
        pytest.fail("Integration tests require a nonzero Redis database index.")

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("APP_SECRET_KEY", "integration-test-secret")
    monkeypatch.setenv("TRUSTED_ORIGINS", ORIGIN)
    monkeypatch.setenv("CORS_ORIGINS", ORIGIN)
    monkeypatch.setenv("METRICS_CACHE_ENABLED", str(options.get("cache_enabled", False)).lower())
    monkeypatch.setenv("METRICS_CACHE_TTL_SECONDS", str(options.get("cache_ttl", 30)))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    redis_client.flushdb()

    now = datetime.now(timezone.utc)
    with testing_session() as db:
        admin = User(email=ADMIN_EMAIL, password_hash=hash_password(PASSWORD), role=Role.admin)
        viewer = User(email=VIEWER_EMAIL, password_hash=hash_password(PASSWORD), role=Role.viewer)
        agent_a = Agent(name="Agent Alpha", description="First test agent", category="test", enabled=True)
        agent_b = Agent(name="Agent Beta", description="Second test agent", category="test", enabled=True)
        db.add_all([admin, viewer, agent_a, agent_b])
        db.flush()
        sessions = {
            "alpha_completed": AgentSession(
                agent_id=agent_a.id,
                synthetic_user_id="user-alpha",
                status=SessionStatus.completed,
                started_at=now - timedelta(hours=1),
                ended_at=now - timedelta(minutes=50),
                device_category="desktop",
                browser="Chrome",
                operating_system="Windows",
                country="US",
                network_profile="wifi",
                execution_count=3,
                token_usage=1200,
                estimated_cost_usd=0.003,
                duration_ms=1000,
            ),
            "alpha_failed": AgentSession(
                agent_id=agent_a.id,
                synthetic_user_id="user-beta",
                status=SessionStatus.failed,
                started_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=1, minutes=55),
                device_category="mobile",
                browser="Firefox",
                operating_system="Android",
                country="DE",
                network_profile="cellular",
                execution_count=1,
                token_usage=300,
                estimated_cost_usd=0.00075,
                duration_ms=2500,
                error_code="tool_error",
                error_message="Synthetic tool failure",
            ),
            "beta_completed": AgentSession(
                agent_id=agent_b.id,
                synthetic_user_id="user-gamma",
                status=SessionStatus.completed,
                started_at=now - timedelta(hours=3),
                ended_at=now - timedelta(hours=2, minutes=58),
                device_category="desktop",
                browser="Safari",
                operating_system="macOS",
                country="CA",
                network_profile="wifi",
                execution_count=2,
                token_usage=700,
                estimated_cost_usd=0.00175,
                duration_ms=1500,
            ),
        }
        db.add_all(sessions.values())
        db.commit()
        agent_ids = {"alpha": str(agent_a.id), "beta": str(agent_b.id)}
        session_ids = {name: str(item.id) for name, item in sessions.items()}

    app = create_app()

    def override_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        with TestClient(app) as client:
            yield IntegrationContext(client, redis_client, testing_session, agent_ids, session_ids)
    finally:
        app.dependency_overrides.clear()
        redis_client.flushdb()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


def login(context: IntegrationContext, role: Role = Role.admin) -> str:
    email = ADMIN_EMAIL if role == Role.admin else VIEWER_EMAIL
    response = context.client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def mutation_headers(csrf_token: str) -> dict[str, str]:
    return {"origin": ORIGIN, CSRF_HEADER: csrf_token}


def event_payload(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "client_event_id": "event-1",
        "event_type": "tool_call",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 250,
        "outcome": "success",
        "token_usage": 40,
        "details": {"tool": "search"},
    }


def vital_payload(
    *, observation_id: str = "obs-1", metric_id: str = "metric-1", value: float = 1200
) -> dict[str, Any]:
    return {
        "observations": [
            {
                "observation_id": observation_id,
                "metric_id": metric_id,
                "metric_name": "LCP",
                "value": value,
                "route_path": "/dashboard",
                "browser_session_id": "browser-1",
                "device_category": "desktop",
                "browser": "Chrome",
                "operating_system": "Windows",
                "app_release": "test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "actual",
            }
        ]
    }


def test_unauthenticated_requests_are_rejected(context):
    for path in ["/api/v1/auth/me", "/api/v1/agents", "/api/v1/sessions", "/api/v1/metrics/summary"]:
        assert_error(context.client.get(path), 401, "unauthorized")


def test_viewer_can_read_but_admin_mutations_require_admin(context):
    csrf = login(context, Role.viewer)
    assert context.client.get("/api/v1/agents").status_code == 200

    patch = context.client.patch(
        f"/api/v1/agents/{context.agent_ids['alpha']}",
        json={"enabled": False},
        headers=mutation_headers(csrf),
    )
    assert_error(patch, 403, "forbidden", "Admin role required")

    event = context.client.post(
        "/api/v1/events",
        json=event_payload(context.session_ids["alpha_completed"]),
        headers=mutation_headers(csrf),
    )
    assert_error(event, 403, "forbidden", "Admin role required")


def test_missing_invalid_and_valid_csrf_tokens(context):
    csrf = login(context)
    path = f"/api/v1/agents/{context.agent_ids['alpha']}"
    payload = {"enabled": False}

    missing = context.client.patch(path, json=payload, headers={"origin": ORIGIN})
    assert_error(missing, 403, "forbidden", "Missing CSRF token")

    invalid = context.client.patch(path, json=payload, headers=mutation_headers("invalid-token"))
    assert_error(invalid, 403, "forbidden", "Invalid CSRF token")

    valid = context.client.patch(path, json=payload, headers=mutation_headers(csrf))
    assert valid.status_code == 200, valid.text
    assert valid.json()["enabled"] is False


def test_logout_invalidates_server_side_session(context):
    csrf = login(context)
    session_token = context.client.cookies.get(SESSION_COOKIE)
    assert session_token
    assert context.redis_client.exists(session_key(session_token)) == 1

    logout = context.client.post("/api/v1/auth/logout", headers=mutation_headers(csrf))
    assert logout.status_code == 200, logout.text
    assert context.redis_client.exists(session_key(session_token)) == 0
    assert context.client.cookies.get(SESSION_COOKIE) is None

    replay = context.client.get(
        "/api/v1/auth/me",
        headers={"cookie": f"{SESSION_COOKIE}={session_token}"},
    )
    assert_error(replay, 401, "unauthorized", "Invalid or expired session")


def test_metrics_cache_bypass_does_not_write_cache_entries(context):
    login(context)
    first = context.client.get("/api/v1/metrics/summary")
    second = context.client.get("/api/v1/metrics/summary")
    assert first.json()["cache"]["status"] == "BYPASS"
    assert second.json()["cache"]["status"] == "BYPASS"
    assert list(context.redis_client.scan_iter("metrics:*")) == []


@pytest.mark.parametrize("context", [{"cache_enabled": True}], indirect=True)
def test_metrics_cache_keys_isolate_agent_filters(context):
    login(context)
    responses = {}
    for name, expected_tokens in [("alpha", 1500), ("beta", 700)]:
        response = context.client.get(
            "/api/v1/metrics/summary",
            params={"agent_id": context.agent_ids[name]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["token_usage"] == expected_tokens
        assert response.json()["cache"]["status"] == "MISS"
        responses[name] = context.client.get(
            "/api/v1/metrics/summary",
            params={"agent_id": context.agent_ids[name]},
        )

    assert responses["alpha"].json()["cache"]["status"] == "HIT"
    assert responses["beta"].json()["cache"]["status"] == "HIT"
    assert len(list(context.redis_client.scan_iter("metrics:summary:*"))) == 2


@pytest.mark.parametrize("context", [{"cache_enabled": True, "cache_ttl": 1}], indirect=True)
def test_metrics_cache_expires_and_refreshes_from_postgres(context):
    login(context)
    params = {"agent_id": context.agent_ids["alpha"]}
    first = context.client.get("/api/v1/metrics/summary", params=params)
    assert first.json()["cache"]["status"] == "MISS"
    assert first.json()["token_usage"] == 1500

    with context.session_factory() as db:
        session = db.get(AgentSession, context.session_ids["alpha_completed"])
        session.token_usage = 1300
        db.commit()

    stale = context.client.get("/api/v1/metrics/summary", params=params)
    assert stale.json()["cache"]["status"] == "HIT"
    assert stale.json()["token_usage"] == 1500

    key = next(context.redis_client.scan_iter("metrics:summary:*"))
    deadline = time.monotonic() + 3
    while context.redis_client.exists(key) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert context.redis_client.exists(key) == 0

    refreshed = context.client.get("/api/v1/metrics/summary", params=params)
    assert refreshed.json()["cache"]["status"] == "MISS"
    assert refreshed.json()["token_usage"] == 1600


def test_event_deduplication_returns_the_original_event(context):
    csrf = login(context)
    payload = event_payload(context.session_ids["alpha_completed"])
    first = context.client.post("/api/v1/events", json=payload, headers=mutation_headers(csrf))
    second = context.client.post("/api/v1/events", json=payload, headers=mutation_headers(csrf))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["deduplicated"] is False
    assert second.json() == {"id": first.json()["id"], "deduplicated": True}


def test_web_vital_deduplication_updates_repeated_metric(context):
    csrf = login(context)
    first = context.client.post(
        "/api/v1/web-vitals",
        json=vital_payload(),
        headers=mutation_headers(csrf),
    )
    repeated = context.client.post(
        "/api/v1/web-vitals",
        json=vital_payload(observation_id="obs-2", value=1800),
        headers=mutation_headers(csrf),
    )
    same_observation = context.client.post(
        "/api/v1/web-vitals",
        json=vital_payload(observation_id="obs-2", value=2100),
        headers=mutation_headers(csrf),
    )

    assert first.json() == {"accepted": 1, "deduplicated": 0}
    assert repeated.json() == {"accepted": 0, "deduplicated": 1}
    assert same_observation.json() == {"accepted": 0, "deduplicated": 1}

    summary = context.client.get(
        "/api/v1/web-vitals/summary",
        params={"metric_name": "LCP", "route_path": "/dashboard"},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["items"] == [{"metric_name": "LCP", "sample_count": 1, "p75": 2100.0}]
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(WebVitalObservation)) == 1
        observation = db.scalar(select(WebVitalObservation))
        assert observation.observation_id == "obs-2"
        assert observation.value == 2100


def test_payload_limits_and_query_validation(context):
    csrf = login(context)
    event = event_payload(context.session_ids["alpha_completed"])
    event["token_usage"] = 1_000_001
    assert_error(
        context.client.post("/api/v1/events", json=event, headers=mutation_headers(csrf)),
        422,
        "validation_error",
    )

    oversized_vitals = vital_payload()["observations"] * 101
    assert_error(
        context.client.post(
            "/api/v1/web-vitals",
            json={"observations": oversized_vitals},
            headers=mutation_headers(csrf),
        ),
        422,
        "validation_error",
    )

    invalid_queries = [
        ("/api/v1/sessions", {"page_size": 101}),
        ("/api/v1/sessions", {"status": "unknown"}),
        ("/api/v1/sessions", {"sort": "unknown"}),
        ("/api/v1/sessions", {"date_from": "2026-01-01T00:00:00", "date_to": "2026-01-02T00:00:00"}),
        (
            "/api/v1/sessions",
            {"date_from": "2026-01-02T00:00:00Z", "date_to": "2026-01-01T00:00:00Z"},
        ),
        (
            "/api/v1/metrics/summary",
            {"date_from": "2026-01-01T00:00:00Z", "date_to": "2026-04-02T00:00:00Z"},
        ),
        (
            "/api/v1/metrics/timeseries",
            {
                "granularity": "minute",
                "date_from": "2026-01-01T00:00:00Z",
                "date_to": "2026-01-02T00:00:00Z",
            },
        ),
    ]
    for path, params in invalid_queries:
        assert_error(context.client.get(path, params=params), 422, "validation_error")


def test_session_filters_are_combined(context):
    login(context)
    now = datetime.now(timezone.utc)
    response = context.client.get(
        "/api/v1/sessions",
        params={
            "agent_id": context.agent_ids["alpha"],
            "status": "failed",
            "device": "mobile",
            "browser": "Firefox",
            "search": "tool failure",
            "date_from": (now - timedelta(days=1)).isoformat(),
            "date_to": (now + timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == context.session_ids["alpha_failed"]
