from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import router
from app.config import get_settings
from app.middleware import RequestContextMiddleware, configure_logging, error_response


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="AgentPulse Backend",
        version="0.1.0",
        description=(
            "Backend API for synthetic AI-agent platform monitoring. "
            "Timing values are milliseconds; estimated costs are synthetic USD estimates."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["content-type", "x-csrf-token", "x-request-id"],
    )
    app.include_router(router)

    @app.exception_handler(HTTPException)
    async def fastapi_http_exception(request: Request, exc: HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        code = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            422: "validation_error",
            429: "rate_limited",
            503: "service_unavailable",
        }.get(exc.status_code, "http_error")
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception(request: Request, exc: StarletteHTTPException):
        return error_response(request, exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError):
        return error_response(request, 422, "validation_error", str(exc))

    return app


app = create_app()

