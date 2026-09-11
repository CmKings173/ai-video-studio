from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import AuthContext, auth_context, current_user, require_csrf
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.core.security import hash_token, new_token, verify_password
from apps.api.app.db.models import Session as AuthSession
from apps.api.app.db.models import User, utcnow
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
    await session.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    email = payload.email.strip().lower()
    client_host = request.client.host if request.client else "unknown"
    throttle_key = f"{client_host}:{email}"
    retry_after = login_throttle.retry_after(throttle_key)
    if retry_after:
        raise AppError(
            "LOGIN_RATE_LIMITED",
            "Too many login attempts; try again later",
            429,
            {"retry_after_seconds": retry_after},
        )
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        login_throttle.record_failure(throttle_key)
        raise AppError("INVALID_CREDENTIALS", "Invalid email or password", 401)
    if not user.is_active:
        login_throttle.record_failure(throttle_key)
        raise AppError("ACCOUNT_DISABLED", "Account is disabled", 403)
    login_throttle.record_success(throttle_key)
    token, csrf = new_token(), new_token()
    session.add(
        AuthSession(
            id=hash_token(token),
            user_id=user.id,
            csrf_hash=hash_token(csrf),
            expires_at=utcnow() + timedelta(hours=settings.session_hours),
        )
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )
    return LoginDTO(user=UserDTO.model_validate(user), csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    context: AuthContext = Depends(auth_context),
    csrf_user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    await session.delete(context.session)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserDTO)
async def me(user: User = Depends(current_user)) -> UserDTO:
    return UserDTO.model_validate(user)
