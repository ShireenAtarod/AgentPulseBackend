from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import parse_date_range, percentile
from app.main import app
from app.schemas import WebVitalBatch
from app.security import CSRF_HEADER


def test_date_range_is_inclusive_exclusive_and_bounded():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    parsed_start, parsed_end = parse_date_range(start, end)
    assert parsed_start == start
    assert parsed_end == end

    with pytest.raises(HTTPException):
        parse_date_range(end, start)

    with pytest.raises(HTTPException):
        parse_date_range(start, start + timedelta(days=91))


def test_percentile_empty_and_interpolated_values():
    assert percentile([], 0.75) is None
    assert percentile([100, 200, 300, 400], 0.75) == 325


def test_web_vitals_reject_query_string_routes_and_large_batches():
    observation = {
        "observation_id": "obs-1",
        "metric_id": "metric-1",
        "metric_name": "LCP",
        "value": 1234,
        "route_path": "/dashboard?secret=1",
        "browser_session_id": "browser-session-1",
        "app_release": "test",
        "timestamp": "2026-10-11T06:30:00Z",
        "source": "actual",
    }
    with pytest.raises(ValueError):
        WebVitalBatch(observations=[observation])

    valid = dict(observation, route_path="/dashboard")
    with pytest.raises(ValueError):
        WebVitalBatch(observations=[valid for _ in range(101)])


def test_openapi_exposes_csrf_header_on_protected_mutations():
    schema = app.openapi()
    protected_operations = [
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/agents/{agent_id}", "patch"),
        ("/api/v1/events", "post"),
        ("/api/v1/web-vitals", "post"),
    ]

    for path, method in protected_operations:
        parameters = schema["paths"][path][method]["parameters"]
        csrf_parameter = next(
            parameter
            for parameter in parameters
            if parameter["in"] == "header" and parameter["name"] == CSRF_HEADER
        )
        assert csrf_parameter["required"] is False

