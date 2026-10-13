"""Password verification and signed access-token primitives."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from apps.api.app.core.config import Settings, is_valid_jwt_signing_secret

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
JWT_ALGORITHM = "HS256"
JWT_MAX_TOKEN_BYTES = 8192
JWT_MAX_LIFETIME_SECONDS = 2_592_000


class AccessTokenError(ValueError):
    """Base class for rejected access tokens or unavailable signing configuration."""


class AccessTokenExpiredError(AccessTokenError):
    """The access token has expired."""


class AccessTokenConfigurationError(AccessTokenError):
    """The application has no safe JWT signing configuration."""


@dataclass(frozen=True)
class AccessTokenPrincipal:
    id: str
    role: str
    csrf_token: str
    expires_at: int
    auth_version: int = 1


def _jwt_configuration(settings: Settings) -> tuple[str, str, str]:
    secret = settings.jwt_secret
    issuer = settings.jwt_issuer.strip()
    audience = settings.jwt_audience.strip()
    if not is_valid_jwt_signing_secret(secret):
        raise AccessTokenConfigurationError("A valid JWT signing secret is required")
    if not issuer or not audience:
        raise AccessTokenConfigurationError("JWT issuer and audience are required")
    return secret, issuer, audience


def validate_jwt_signing_configuration(settings: Settings) -> None:
    """Fail API startup when its production signing configuration is unsafe."""
    _jwt_configuration(settings)


def create_access_token(
    user_id: str, role: str, settings: Settings, *, auth_version: int = 1
) -> tuple[str, str]:
    """Create a 30-day-or-shorter signed token and its JWT-bound CSRF token."""
    secret, issuer, audience = _jwt_configuration(settings)
    lifetime = settings.jwt_expiration_seconds
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(role, str)
        or role not in {"ADMIN", "EDITOR"}
        or type(auth_version) is not int
        or auth_version < 1
    ):
        raise AccessTokenConfigurationError("A valid user id and role are required")
    if not 1 <= lifetime <= JWT_MAX_LIFETIME_SECONDS:
        raise AccessTokenConfigurationError("JWT lifetime must be between 1 second and 30 days")

    issued_at = int(datetime.now(UTC).timestamp())
    expires_at = issued_at + lifetime
    csrf_token = secrets.token_urlsafe(32)
    token = jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "iss": issuer,
            "aud": audience,
            "iat": issued_at,
            "exp": expires_at,
            "csrf": csrf_token,
            "auth_version": auth_version,
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )
    return token, csrf_token


def verify_access_token(token: str, settings: Settings) -> AccessTokenPrincipal:
    """Verify a signed token against fixed claims and the configured HS256 key."""
    secret, issuer, audience = _jwt_configuration(settings)
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or len(token) > JWT_MAX_TOKEN_BYTES
    ):
        raise AccessTokenError("Access token is malformed")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={
                "require": ["sub", "role", "iss", "aud", "iat", "exp", "csrf", "auth_version"],
            },
        )
    except ExpiredSignatureError as exc:
        raise AccessTokenExpiredError("Access token has expired") from exc
    except (InvalidTokenError, TypeError, ValueError, OverflowError) as exc:
        raise AccessTokenError("Access token is invalid") from exc

    user_id = claims.get("sub")
    role = claims.get("role")
    csrf_token = claims.get("csrf")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    auth_version = claims.get("auth_version")
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(role, str)
        or role not in {"ADMIN", "EDITOR"}
        or not isinstance(csrf_token, str)
        or not csrf_token.isascii()
        or len(csrf_token) < 32
        or type(issued_at) is not int
        or type(expires_at) is not int
        or issued_at < 0
        or expires_at <= issued_at
        or expires_at - issued_at > JWT_MAX_LIFETIME_SECONDS
        or type(auth_version) is not int
        or auth_version < 1
    ):
        raise AccessTokenError("Access token claims are invalid")
    return AccessTokenPrincipal(user_id, role, csrf_token, expires_at, auth_version)


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Passwords must contain between 12 and 1024 characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or len(password) > 1024:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
