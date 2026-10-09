"""ASGI request body limiting based on bytes actually received."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from starlette.responses import JSONResponse


class RequestBodyTooLarge(Exception):
    """Raised when a streamed request exceeds the configured byte limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized requests even when clients omit Content-Length."""

    def __init__(self, app: Callable[..., Awaitable[Any]], max_bytes: int):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope.get("headers", []))
        if content_length is not None and content_length > self.max_bytes:
            await self._send_rejection(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._send_rejection(scope, receive, send)
            else:
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    def _content_length(headers) -> int | None:
        raw = next((value for key, value in headers if key.lower() == b"content-length"), None)
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return max(value, 0)

    @staticmethod
    async def _send_rejection(scope, receive, send) -> None:
        request_id = uuid4().hex
        response = JSONResponse(
            {
                "error": {
                    "code": "REQUEST_BODY_TOO_LARGE",
                    "message": "Request body exceeds the configured limit",
                    "trace_id": request_id,
                    "details": {},
                }
            },
            status_code=413,
            headers={
                "X-Request-ID": request_id,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            },
        )
        await response(scope, receive, send)
