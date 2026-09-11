from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import select

from apps.api.app.api import (
    admin,
    assembly,
    assets,
    auth,
    brands,
    dashboard,
    events,
    generations,
    health,
    products,
    projects,
    scenes,
    videos,
)
from apps.api.app.api import (
    metrics as metrics_api,
)
from apps.api.app.core.config import get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.logging import configure_logging, request_id_context
from apps.api.app.core.metrics import metrics
from apps.api.app.core.request_limits import RequestBodyLimitMiddleware
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import User
from apps.api.app.db.session import SessionFactory, engine
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import ErrorEnvelope
from apps.api.app.services.workflow_loader import seed_workflows

settings = get_settings()
configure_logging()
logger = logging.getLogger(__name__)
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _metric_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    return re.sub(r"[^A-Za-z0-9_./{}:-]", "_", path)[:200]


async def _bootstrap_admin() -> User | None:
    async with SessionFactory() as session, session.begin():
        admin = await session.scalar(
            select(User)
            .where(User.role == "ADMIN", User.is_active.is_(True))
            .order_by(User.created_at, User.id)
            .limit(1)
        )
        if admin is not None:
            return admin
        if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
            logger.warning("no_active_admin_configured")
            return None
        admin = User(
            email=settings.bootstrap_admin_email.strip().lower(),
            name="Administrator",
            password_hash=hash_password(settings.bootstrap_admin_password),
            role="ADMIN",
            is_active=True,
        )
        session.add(admin)
        await session.flush()
        return admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(Path(settings.workspace_root).mkdir, parents=True, exist_ok=True)
    try:
        await AssetStore(settings).ensure_bucket()
    except Exception:
        logger.exception("minio_bucket_initialization_failed")
    try:
        admin_user = await _bootstrap_admin()
        await seed_workflows(SessionFactory, Path(settings.workflow_dir), admin_user)
    except Exception:
        logger.exception("database_bootstrap_failed")
        raise
    yield
    await engine.dispose()


app = FastAPI(
    title="AI Advertising Video Studio API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Content-Type",
        "If-Match",
        "Idempotency-Key",
        "Last-Event-ID",
        "X-CSRF-Token",
        "X-Request-ID",
    ],
    expose_headers=["ETag", "X-Request-ID"],
)


app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.request_body_max_bytes)


def _error_payload(
    request: Request, code: str, message: str, details: dict | list | None = None
) -> dict:
    request_id = getattr(request.state, "request_id", "")
    return {
        "error": {
            "code": code,
            "message": message,
            "trace_id": request_id,
            "details": details or {},
        }
    }


@app.middleware("http")
async def request_context(request: Request, call_next):
    started_at = time.perf_counter()
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid4().hex
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
    except Exception:
        metrics.inc(
            "studio_http_requests_total",
            labels={"method": request.method, "path": _metric_path(request), "status_class": "5xx"},
        )
        metrics.observe(
            "studio_http_request_duration_seconds",
            time.perf_counter() - started_at,
            labels={"method": request.method, "path": _metric_path(request)},
        )
        raise
    finally:
        request_id_context.reset(token)
    path = _metric_path(request)
    metrics.inc(
        "studio_http_requests_total",
        labels={
            "method": request.method,
            "path": path,
            "status_class": f"{response.status_code // 100}xx",
        },
    )
    metrics.observe(
        "studio_http_request_duration_seconds",
        time.perf_counter() - started_at,
        labels={"method": request.method, "path": path},
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.app_env.lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        _error_payload(request, exc.code, exc.message, exc.details),
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    missing_headers = {
        str(item.get("loc", ("", ""))[-1]).lower()
        for item in exc.errors()
        if item.get("type") == "missing" and item.get("loc", (None,))[0] == "header"
    }
    if "if-match" in missing_headers:
        return JSONResponse(
            _error_payload(
                request,
                "PRECONDITION_REQUIRED",
                "If-Match revision is required",
            ),
            status_code=428,
        )
    if "idempotency-key" in missing_headers:
        return JSONResponse(
            _error_payload(
                request,
                "IDEMPOTENCY_KEY_REQUIRED",
                "A valid Idempotency-Key header is required",
            ),
            status_code=400,
        )
    errors = []
    for item in exc.errors():
        errors.append(
            {
                "type": item.get("type"),
                "location": list(item.get("loc", ())),
                "message": item.get("msg"),
            }
        )
    return JSONResponse(
        _error_payload(
            request,
            "REQUEST_VALIDATION_FAILED",
            "Request validation failed",
            {"errors": errors},
        ),
        status_code=422,
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        _error_payload(request, "HTTP_ERROR", str(exc.detail)),
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_request_error")
    return JSONResponse(
        _error_payload(request, "INTERNAL_ERROR", "An unexpected error occurred"),
        status_code=500,
    )


routers = (
    health.router,
    auth.router,
    projects.router,
    brands.router,
    products.router,
    assets.router,
    videos.router,
    scenes.router,
    generations.router,
    assembly.router,
    events.router,
    metrics_api.router,
    dashboard.router,
    admin.router,
)
for api_router in routers:
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(api_router, prefix="/api", include_in_schema=False)


def _error_response(description: str) -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
    }


def public_openapi() -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    model_schema = ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = model_schema.pop("$defs", {})
    components.setdefault("schemas", {}).update(definitions)
    components["schemas"]["ErrorEnvelope"] = model_schema
    components.setdefault("securitySchemes", {})["StudioSession"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.session_cookie_name,
    }
    error_responses = {
        "400": _error_response("Invalid request or business rule"),
        "401": _error_response("Authentication required"),
        "403": _error_response("Permission denied"),
        "404": _error_response("Resource not found"),
        "409": _error_response("State or idempotency conflict"),
        "412": _error_response("Revision precondition failed"),
        "422": _error_response("Request validation failed"),
        "428": _error_response("Required precondition missing"),
        "500": _error_response("Unexpected server error"),
    }
    public_paths = {"/api/v1/auth/login", "/api/v1/health/live", "/api/v1/health/ready"}
    mutation_methods = {"post", "patch", "delete", "put"}
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.setdefault("responses", {})
            for status_code, response in error_responses.items():
                if status_code == "422" or status_code not in responses:
                    responses[status_code] = copy.deepcopy(response)
            if path not in public_paths:
                operation["security"] = [{"StudioSession": []}]
            if method in mutation_methods and path != "/api/v1/auth/login":
                parameters = operation.setdefault("parameters", [])
                if not any(item.get("name") == settings.csrf_header_name for item in parameters):
                    parameters.append(
                        {
                            "name": settings.csrf_header_name,
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        }
                    )
    app.openapi_schema = schema
    return schema


app.openapi = public_openapi


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "ai-advertising-video-studio", "docs": "/api/docs"}
