# Frontend Contract

Base URL: `/api/v1`. Swagger is `/docs`; OpenAPI JSON is `/openapi.json`.

Swagger sends the page origin on login and state-changing requests. If Docker publishes the API on a non-default local port, add that exact origin, such as `http://127.0.0.1:8010`, to `TRUSTED_ORIGINS`.

All authenticated API responses use `Cache-Control: private, no-store` by default. Errors use:

```json
{
  "error": { "code": "validation_error", "message": "..." },
  "request_id": "..."
}
```

## Auth Flow

1. `POST /api/v1/auth/login`

```json
{ "email": "admin@example.test", "password": "your-password" }
```

Response:

```json
{
  "user": { "id": "...", "email": "admin@example.test", "role": "admin" },
  "csrf_token": "..."
}
```

The browser receives an HttpOnly `agentpulse_session` cookie and readable `agentpulse_csrf` cookie. Send `credentials: "include"` from the browser. For POST/PATCH, include `x-csrf-token`.

2. `GET /api/v1/auth/me` returns the current user.
3. `GET /api/v1/auth/csrf` refreshes the CSRF token/header name.
4. `POST /api/v1/auth/logout` requires the CSRF header and clears the session.

Viewer can read dashboard/session data and submit Web Vitals. Admin can also patch agents and ingest platform events.

## Sessions

`GET /api/v1/sessions` supports `agent_id`, `status`, `device`, `browser`, `date_from`, `date_to`, `search`, `sort`, `page`, and `page_size`.

Rules:

- Pagination shape is `{ items, total, page, page_size }`.
- Default page size is 25; maximum is 100.
- `date_from` is inclusive; `date_to` is exclusive.
- Default date range is previous 7 days; maximum is 90 days.
- Sort fields: `started_at`, `duration_ms`, `token_usage`, `estimated_cost_usd`, `status`. Prefix with `-` for descending. Ties sort by `id`.

`GET /api/v1/sessions/{id}` returns one session plus bounded event history using `events_page` and `events_page_size`.

## Metrics

`GET /api/v1/metrics/summary` returns:

- `active_sessions`: count of sessions with status `running`.
- `execution_rate_per_minute`: sum of `execution_count` divided by selected minutes.
- `success_rate`: completed sessions divided by selected sessions.
- `error_rate`: failed sessions divided by selected sessions.
- `latency_ms`: p50/p75/p95 from non-null session durations.
- `token_usage`: sum of session token usage.
- `estimated_cost_usd`: synthetic USD estimate.
- `device_distribution` and `browser_distribution`.
- `cache.status`: `BYPASS`, `MISS`, or `HIT`.

Empty data returns zero counts/rates and null percentiles.

`GET /api/v1/metrics/timeseries?granularity=hour` supports `minute`, `hour`, and `day`, with at most 500 returned buckets.

## Live Updates

`GET /api/v1/metrics/live` is an authenticated Server-Sent Events stream. Browser usage:

```ts
const source = new EventSource("/api/v1/metrics/live", { withCredentials: true });
source.addEventListener("summary", (event) => console.log(JSON.parse(event.data)));
source.addEventListener("heartbeat", () => {});
```

Reverse proxies should disable response buffering for this route and allow long read timeouts.

## Web Vitals

`POST /api/v1/web-vitals` accepts up to 100 observations:

```json
{
  "observations": [
    {
      "observation_id": "obs-1",
      "metric_id": "v3-123",
      "metric_name": "LCP",
      "value": 1840,
      "route_path": "/dashboard",
      "browser_session_id": "anon-browser-session",
      "device_category": "desktop",
      "browser": "Chrome",
      "operating_system": "Windows",
      "app_release": "local-dev",
      "timestamp": "2026-10-11T06:40:00Z",
      "source": "actual"
    }
  ]
}
```

Timing metrics are milliseconds. CLS is unitless. `route_path` must not include a query string or origin. Duplicates by `observation_id` or `(metric_id, browser_session_id)` update the existing row instead of increasing sample count. `fetch(..., { keepalive: true, credentials: "include" })` can be used, but still include `x-csrf-token`; `sendBeacon` is not assumed because custom headers are required.

`GET /api/v1/web-vitals/summary` returns p75 and sample counts. Synthetic fixtures are excluded unless `include_synthetic=true`.

## Events

`POST /api/v1/events` is admin-only and idempotent by `(session_id, client_event_id)`. It ingests platform execution events, not browser sessions.

## Next.js Notes

For browser same-origin calls, proxy `/api/v1/*` to this backend and forward credentials. For Next.js server-side requests, forward the incoming `Cookie` header to the backend and do not expose session cookies to JavaScript.

Prefer same-origin deployment so CORS is unnecessary in production. If cross-origin is used, configure explicit origins and `credentials: "include"`.

## Synthetic Data

Seeded agents, sessions, and execution events are synthetic and deterministic for a configured seed. They are meant for frontend UI realism. Web Vitals with `source: "actual"` are observed browser/lab measurements, not fake real-user field data.

