from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import (
    AuthPrincipal,
    current_user,
    principal_from_token,
    validate_csrf_token,
)
from apps.api.app.core.client_identity import login_client_host
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.core.security import (
    AccessTokenConfigurationError,
    create_access_token,
    verify_password,
)
from apps.api.app.db.models import User
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import Login, LoginDTO, UserDTO

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginDTO)
async def login(
    request: Request,
    payload: Login,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LoginDTO:
    email = payload.email.strip().lower()
    client_host = login_client_host(request, settings)
    throttle_keys = (
        f"ip:{client_host}",
        f"identity:{email}",
        f"attempt:{client_host}:{email}",
    )
    retry_after = login_throttle.retry_after_any(throttle_keys)
    if retry_after:
        raise AppError(
            "LOGIN_RATE_LIMITED",
            "Too many login attempts; try again later",
            429,
            {"retry_after_seconds": retry_after},
        )
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.record_failure_many(throttle_keys)
        raise AppError("INVALID_CREDENTIALS", "Invalid email or password", 401)
    if not user.is_active:
        login_throttle.record_failure_many(throttle_keys)
        raise AppError("ACCOUNT_DISABLED", "Account is disabled", 403)
    login_throttle.record_login_success(
        ip_key=f"ip:{client_host}",
        identity_key=f"identity:{email}",
        attempt_key=f"attempt:{client_host}:{email}",
    )
    try:
        token, csrf = create_access_token(
            user.id, user.role, settings, auth_version=user.auth_version
        )
    except AccessTokenConfigurationError as exc:
        raise AppError("AUTH_UNAVAILABLE", "Authentication is not configured", 503) from exc
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.jwt_expiration_seconds,
        path="/",
    )
    return LoginDTO(user=UserDTO.model_validate(user), csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> None:
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        try:
            principal = principal_from_token(token, settings)
        except AppError as exc:
            if exc.status_code not in {401, 503}:
                raise
        else:
            validate_csrf_token(request, principal, settings)

    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.get("/me", response_model=UserDTO)
async def me(
    principal: AuthPrincipal = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> UserDTO:
    user = await session.get(User, principal.id)
    if user is None:
        raise AppError("AUTH_REQUIRED", "Authenticated account no longer exists", 401)
    return UserDTO.model_validate(user)
