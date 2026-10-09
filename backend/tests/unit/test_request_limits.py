from __future__ import annotations

from collections import deque

import pytest

from apps.api.app.core.request_limits import RequestBodyLimitMiddleware


async def echo_app(scope, receive, send):
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": bytes(body)})


async def run_request(chunks: list[bytes], headers: list[tuple[bytes, bytes]]):
    messages = deque(
        [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
    )
    sent = []

    async def receive():
        return messages.popleft()

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": headers}
    await RequestBodyLimitMiddleware(echo_app, max_bytes=4)(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_request_body_under_limit_is_forwarded():
    sent = await run_request([b"abcd"], [])
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"abcd"


@pytest.mark.asyncio
async def test_request_body_over_limit_is_rejected_by_actual_bytes():
    sent = await run_request([b"ab", b"cde"], [])
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_invalid_content_length_does_not_bypass_limit():
    sent = await run_request([b"12345"], [(b"content-length", b"not-a-number")])
    assert sent[0]["status"] == 413
