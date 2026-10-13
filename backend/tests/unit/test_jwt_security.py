from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI

from apps.api.app import main
from apps.api.app.core.config import Settings
from apps.api.app.core.security import (
    AccessTokenConfigurationError,
    AccessTokenError,
    AccessTokenExpiredError,
    create_access_token,
    validate_jwt_signing_configuration,
    verify_access_token,
)

JWT_SECRET = "unit-test-only-signing-secret-with-at-least-32-bytes"


@pytest.fixture
def jwt_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        jwt_secret=JWT_SECRET,
        jwt_issuer="ai-video-studio-tests",
        jwt_audience="ai-video-studio-api-tests",
    )


def valid_claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user-123",
        "role": "EDITOR",
        "iss": "ai-video-studio-tests",
        "aud": "ai-video-studio-api-tests",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
        "csrf": "csrf-fixture-token-value-that-is-long-enough",
        "auth_version": 1,
    }
    claims.update(overrides)
    return claims


def test_created_access_token_has_required_claims_and_thirty_day_expiration(
    jwt_settings: Settings,
) -> None:
    token, csrf_token = create_access_token("user-123", "EDITOR", jwt_settings)
    claims = jwt.decode(
        token,
        JWT_SECRET,
        algorithms=["HS256"],
        issuer="ai-video-studio-tests",
        audience="ai-video-studio-api-tests",
    )

    assert claims["sub"] == "user-123"
    assert claims["role"] == "EDITOR"
    assert claims["csrf"] == csrf_token
    assert claims["exp"] - claims["iat"] == 2_592_000
    assert "password" not in claims
    assert "email" not in claims


def test_valid_token_returns_a_typed_principal(jwt_settings: Settings) -> None:
    token, csrf_token = create_access_token("user-123", "ADMIN", jwt_settings)

    principal = verify_access_token(token, jwt_settings)

    assert principal.id == "user-123"
    assert principal.role == "ADMIN"
    assert principal.csrf_token == csrf_token


def test_token_with_wrong_signature_is_rejected(jwt_settings: Settings) -> None:
    token, _ = create_access_token("user-123", "EDITOR", jwt_settings)
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"

    with pytest.raises(AccessTokenError):
        verify_access_token(f"{header}.{payload}.{replacement}{signature[1:]}", jwt_settings)


def test_expired_token_has_a_typed_expiration_error(jwt_settings: Settings) -> None:
    token = jwt.encode(valid_claims(iat=0, exp=1), JWT_SECRET, algorithm="HS256")

    with pytest.raises(AccessTokenExpiredError):
        verify_access_token(token, jwt_settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": ""},
        {"role": None},
        {"role": []},
        {"role": "OWNER"},
        {"iss": "other-issuer"},
        {"aud": "other-api"},
        {"iat": None},
        {"exp": None},
        {"csrf": ""},
        {"auth_version": None},
        {"auth_version": True},
        {"auth_version": 1.5},
        {"auth_version": 0},
        {"csrf": "é" * 40},
    ],
)
def test_missing_or_invalid_required_claims_are_rejected(
    jwt_settings: Settings,
    overrides: dict[str, object],
) -> None:
    claims = valid_claims(**overrides)
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")

    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwt_settings)


@pytest.mark.parametrize(
    "claim", ["sub", "role", "iss", "aud", "iat", "exp", "csrf", "auth_version"]
)
def test_missing_required_claim_is_rejected(jwt_settings: Settings, claim: str) -> None:
    claims = valid_claims()
    claims.pop(claim)
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")

    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwt_settings)


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_verifier_rejects_header_selected_algorithms(
    jwt_settings: Settings,
    algorithm: str,
) -> None:
    key = "" if algorithm == "none" else JWT_SECRET
    token = jwt.encode(valid_claims(), key, algorithm=algorithm)

    with pytest.raises(AccessTokenError):
        verify_access_token(token, jwt_settings)


def test_malformed_token_is_rejected(jwt_settings: Settings) -> None:
    with pytest.raises(AccessTokenError):
        verify_access_token("not.a.jwt", jwt_settings)
    with pytest.raises(AccessTokenError):
        verify_access_token("é" * 40, jwt_settings)


def test_signing_fails_closed_when_secret_is_missing() -> None:
    settings = Settings(_env_file=None, app_env="development", jwt_secret="")

    with pytest.raises(AccessTokenError):
        create_access_token("user-123", "EDITOR", settings)


def production_settings(*, jwt_secret: str = "") -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        jwt_secret=jwt_secret,
        internal_proxy_token="i" * 40,
        studio_ingress_token="s" * 40,
        cookie_secure=True,
        database_url="postgresql+asyncpg://studio:good-password@localhost/studio",
        minio_access_key="access-key",
        minio_secret_key="minio-secret",
        allowed_origins=["https://studio.example.test"],
        bootstrap_admin_password="a-strong-bootstrap-password",
        metrics_token="m" * 40,
    )


def test_non_api_production_settings_allow_missing_jwt_secret() -> None:
    # Workers and migrations share production settings but do not sign tokens.
    assert production_settings().jwt_secret == ""


def test_api_signing_configuration_rejects_missing_jwt_secret() -> None:
    with pytest.raises(AccessTokenConfigurationError, match="JWT signing secret"):
        validate_jwt_signing_configuration(production_settings())


@pytest.mark.asyncio
async def test_production_api_lifespan_fails_closed_without_jwt_secret(monkeypatch) -> None:
    monkeypatch.setattr(main, "settings", production_settings())

    with pytest.raises(AccessTokenConfigurationError, match="JWT signing secret"):
        async with main.lifespan(FastAPI()):
            pytest.fail("production API lifespan must reject missing JWT_SECRET")


def test_cryptographically_generated_jwt_secret_is_accepted() -> None:
    settings = production_settings(jwt_secret=secrets.token_urlsafe(48))

    validate_jwt_signing_configuration(settings)


@pytest.mark.parametrize(
    "secret",
    [
        "short",
        "replace-with-a-real-secret-value-123456",
        "a" * 40,
        "Abcd1234Abcd1234Abcd1234Abcd1234",
        "0123456789abcdefg0123456789abcdefg",
        "abcdefghijklmnopqrstuvwxyz0123456abcdefghijklmnopqrstuvwxyz0123456",
    ],
)
def test_production_settings_reject_placeholder_or_weak_jwt_secret(secret: str) -> None:
    with pytest.raises(ValueError, match="JWT"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret=secret,
            internal_proxy_token="a" * 40,
            studio_ingress_token="b" * 40,
            cookie_secure=True,
            database_url="postgresql+asyncpg://studio:good-password@localhost/studio",
            minio_access_key="access-key",
            minio_secret_key="minio-secret",
            allowed_origins=["https://studio.example.test"],
            bootstrap_admin_password="a-strong-bootstrap-password",
            metrics_token="c" * 40,
        )
