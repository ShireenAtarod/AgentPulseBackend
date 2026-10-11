import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from redis import Redis
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Role, User
from app.redis_client import get_redis

password_hash = PasswordHash.recommended()
SESSION_COOKIE = "agentpulse_session"
CSRF_COOKIE = "agentpulse_csrf"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, password_hash_value: str) -> bool:
    return password_hash.verify(password, password_hash_value)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_for_session(session_token: str, secret: str) -> str:
    return hmac.new(secret.encode(), session_token.encode(), hashlib.sha256).hexdigest()


def session_key(token: str) -> str:
    return f"session:{token}"


def set_auth_cookies(response: Response, session_token: str, settings: Settings) -> str:
    csrf = csrf_for_session(session_token, settings.app_secret_key)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return csrf


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def require_trusted_origin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin or (referer.rstrip("/") if referer else None)
    if not candidate:
        return
    if origin and origin not in settings.trusted_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Untrusted origin")


def require_csrf(
    request: Request,
    csrf_token: str | None = Header(
        default=None,
        alias=CSRF_HEADER,
        description="CSRF token returned by login or GET /api/v1/auth/csrf.",
    ),
    settings: Settings = Depends(get_settings),
) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF token")
    expected = csrf_for_session(token, settings.app_secret_key)
    if not hmac.compare_digest(csrf_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        user_id = redis_client.get(session_key(token))
        if user_id:
            redis_client.expire(session_key(token), settings.session_ttl_seconds)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Session store unavailable") from exc
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    user = db.get(User, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid user")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


def create_session(redis_client: Redis, user: User, settings: Settings) -> str:
    token = new_token()
    redis_client.setex(session_key(token), settings.session_ttl_seconds, str(user.id))
    return token


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

