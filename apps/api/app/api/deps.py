import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.security import hash_token
from apps.api.app.db.models import Session as AuthSession
from apps.api.app.db.models import User, utcnow
from apps.api.app.db.session import get_session


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


async def auth_context(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AppError("AUTH_REQUIRED", "Authentication required", 401)
    auth_session = await session.get(AuthSession, hash_token(token))
    if auth_session is None or auth_session.expires_at <= utcnow():
        raise AppError("SESSION_EXPIRED", "Session expired", 401)
    user = await session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        raise AppError("ACCOUNT_DISABLED", "Account is disabled", 403)
    return AuthContext(user, auth_session)


async def current_user(context: AuthContext = Depends(auth_context)) -> User:
    return context.user


async def require_editor(user: User = Depends(current_user)) -> User:
    if user.role not in {"ADMIN", "EDITOR"}:
        raise AppError("FORBIDDEN", "Editor access required", 403)
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    return user


async def require_csrf(
    request: Request,
    context: AuthContext = Depends(auth_context),
    settings: Settings = Depends(get_settings),
) -> User:
    supplied = request.headers.get(settings.csrf_header_name)
    if not supplied or not hmac.compare_digest(hash_token(supplied), context.session.csrf_hash):
        raise AppError("CSRF_INVALID", "Valid CSRF token required", 403)
    return context.user


async def require_admin_csrf(user: User = Depends(require_csrf)) -> User:
    """Require both a valid CSRF token and administrator role."""
    if user.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    return user


def parse_revision(value: str | None) -> int:
    if value is None:
        raise AppError("PRECONDITION_REQUIRED", "If-Match revision is required", 428)
    cleaned = value.strip().strip('"')
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:].strip('"')
    try:
        revision = int(cleaned)
    except ValueError as exc:
        raise AppError("INVALID_REVISION", "If-Match must be an integer revision", 400) from exc
    if revision < 1:
        raise AppError("INVALID_REVISION", "Revision must be positive", 400)
    return revision


async def expected_revision(if_match: str = Header(alias="If-Match")) -> int:
    return parse_revision(if_match)


async def idempotency_key(
    value: str = Header(alias="Idempotency-Key"),
) -> str:
    if value is None or not value.strip() or len(value) > 200:
        raise AppError(
            "IDEMPOTENCY_KEY_REQUIRED",
            "A valid Idempotency-Key header is required",
            400,
        )
    return value.strip()
