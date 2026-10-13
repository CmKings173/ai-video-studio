import hmac

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.security import (
    AccessTokenConfigurationError,
    AccessTokenError,
    AccessTokenExpiredError,
    AccessTokenPrincipal,
    verify_access_token,
)
from apps.api.app.db.models import User
from apps.api.app.db.session import get_session

AuthPrincipal = AccessTokenPrincipal


async def validate_current_principal(
    session: AsyncSession, principal: AuthPrincipal
) -> AuthPrincipal:
    """Read authoritative account state, bypassing an ORM identity-map snapshot."""
    account = (
        await session.execute(
            select(User.role, User.is_active, User.auth_version).where(User.id == principal.id)
        )
    ).one_or_none()
    if (
        account is None
        or not account.is_active
        or account.role != principal.role
        or account.auth_version != principal.auth_version
    ):
        raise AppError("AUTH_REQUIRED", "Authentication has been revoked; sign in again", 401)
    return principal


def principal_from_token(token: str, settings: Settings) -> AuthPrincipal:
    try:
        return verify_access_token(token, settings)
    except AccessTokenConfigurationError as exc:
        raise AppError("AUTH_UNAVAILABLE", "Authentication is not configured", 503) from exc
    except AccessTokenExpiredError as exc:
        raise AppError("SESSION_EXPIRED", "Access token expired", 401) from exc
    except AccessTokenError as exc:
        raise AppError("AUTH_REQUIRED", "Authentication required", 401) from exc


async def auth_context(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> AuthPrincipal:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise AppError("AUTH_REQUIRED", "Authentication required", 401)
    principal = principal_from_token(token, settings)
    try:
        return await validate_current_principal(session, principal)
    finally:
        # Authorization runs before endpoint writes. Release its read transaction
        # so streaming responses never retain a connection solely for authentication.
        await session.rollback()


async def current_user(
    context: AuthPrincipal = Depends(auth_context),
) -> AccessTokenPrincipal:
    return context


async def require_editor(
    user: AccessTokenPrincipal = Depends(current_user),
) -> AccessTokenPrincipal:
    if user.role not in {"ADMIN", "EDITOR"}:
        raise AppError("FORBIDDEN", "Editor access required", 403)
    return user


async def require_admin(
    user: AccessTokenPrincipal = Depends(current_user),
) -> AccessTokenPrincipal:
    if user.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access required", 403)
    return user


async def require_csrf(
    request: Request,
    context: AccessTokenPrincipal = Depends(auth_context),
    settings: Settings = Depends(get_settings),
) -> AccessTokenPrincipal:
    return validate_csrf_token(request, context, settings)


def validate_csrf_token(
    request: Request,
    context: AccessTokenPrincipal,
    settings: Settings,
) -> AccessTokenPrincipal:
    supplied = request.headers.get(settings.csrf_header_name)
    if (
        not supplied
        or not supplied.isascii()
        or not hmac.compare_digest(supplied, context.csrf_token)
    ):
        raise AppError("CSRF_INVALID", "Valid CSRF token required", 403)
    return context


async def require_admin_csrf(
    user: AuthPrincipal = Depends(require_csrf),
) -> AuthPrincipal:
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
