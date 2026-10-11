# AgentPulse Backend

FastAPI backend for the AgentPulse frontend learning project. It uses PostgreSQL for durable data, Redis for server-side sessions/rate limits/optional metrics caching, and Docker Compose for local development.

## Shortest Local Startup

1. Copy `.env.example` to `.env` and replace `APP_SECRET_KEY`, `INITIAL_USER_EMAIL`, and `INITIAL_USER_PASSWORD`.
2. Start dependencies and API:

```powershell
docker compose up --build
```

3. In another terminal, create the first user and seed synthetic data:

```powershell
docker compose exec api agentpulse bootstrap-user
docker compose exec api agentpulse seed
```

Open Swagger at http://127.0.0.1:8000/docs, readiness at http://127.0.0.1:8000/health/ready, and liveness at http://127.0.0.1:8000/health/live.

If another local project already uses port 8000, set `API_PUBLISHED_PORT=8001` in `.env`, use `http://127.0.0.1:8001`, and add that exact origin to `TRUSTED_ORIGINS`.

## Common Commands

```powershell
# Apply migrations
docker compose exec api alembic upgrade head

# Run tests locally after installing dev dependencies
python -m pip install -r requirements-dev.txt
ruff check .
pytest

# Start optional simulator
docker compose --profile simulator up simulator

# Stop while preserving PostgreSQL/Redis volumes
docker compose stop

# Restart
docker compose up
```

Do not use `docker compose down -v` unless you intentionally want to delete local data volumes.

## Authentication And CSRF

Login uses `POST /api/v1/auth/login` with JSON `{ "email": "...", "password": "..." }`. A successful login sets an HttpOnly `agentpulse_session` cookie and a readable `agentpulse_csrf` cookie, and also returns `csrf_token`.

For cookie-authenticated state-changing requests, send:

```http
x-csrf-token: <csrf token from /api/v1/auth/csrf or login response>
```

Login and state-changing routes reject untrusted `Origin` headers. Configure trusted browser origins with `TRUSTED_ORIGINS`. Credentialed CORS uses explicit `CORS_ORIGINS`; wildcard origins are intentionally not used.

## Data And Units

Synthetic agent sessions are clearly synthetic platform activity. Timing values are milliseconds. CLS is unitless. Estimated cost is synthetic USD using a documented seed assumption of `$2.50 / 1M tokens`; it is not a vendor bill or benchmark.

Date filters use `date_from` inclusive and `date_to` exclusive. Defaults use the previous 7 days. Normal metrics and list endpoints reject ranges over 90 days. Session pagination defaults to 25 and caps at 100.

## Cache Experiment

Metrics cache-aside is disabled by default:

```env
METRICS_CACHE_ENABLED=false
METRICS_CACHE_TTL_SECONDS=30
```

When enabled, `/api/v1/metrics/summary` and `/api/v1/metrics/timeseries` include cache metadata with `HIT`, `MISS`, or `BYPASS`. Keys include every filter/date/granularity field used by the response. Authentication sessions always require Redis regardless of metrics cache mode. Cache entries never include cookies, session tokens, or user-specific data.

Freshness policy: cached aggregate responses may be stale for up to `METRICS_CACHE_TTL_SECONDS`. Ingestion does not synchronously invalidate aggregate caches; keep TTL low for experiments.

## Large Dataset Experiment

`GET /api/v1/experiments/session-metrics` is disabled unless `EXPERIMENTS_ENABLED=true`. It returns compact numeric session records for browser worker aggregation/sorting experiments, bounded by `EXPERIMENT_MAX_RECORDS` and the `limit` query parameter.

## Simulator

The optional simulator creates platform activity through the same database model as API ingestion. Configure:

```env
SIMULATOR_RATE_PER_MINUTE=30
SIMULATOR_SCENARIO=normal
```

Supported scenarios: `normal`, `elevated-error`, and `slow-execution`.

## Troubleshooting

- `ready` reports Redis unavailable: authentication will not work; check `docker compose ps redis`.
- Browser login fails with 403: add the frontend origin to `TRUSTED_ORIGINS` and `CORS_ORIGINS`.
- Cookies are missing on localhost: keep `COOKIE_SECURE=false` for HTTP-only local development.
- Swagger works but protected routes return 401: use `/api/v1/auth/login` first.

## Dependency Choices

The implementation follows current official guidance checked during setup: FastAPI credentialed CORS requires explicit origins, FastAPI security docs recommend `pwdlib` password hashing, SQLAlchemy PostgreSQL docs support psycopg 3, Alembic is used for SQLAlchemy migrations, and redis-py is the official Redis Python client.

