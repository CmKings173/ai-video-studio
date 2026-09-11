"""HTTP-only ComfyUI bridge with explicit uncertain submission semantics."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx


class ComfyError(RuntimeError):
    def __init__(self, message: str, code: str = "COMFY_ERROR", details: Any = None):
        super().__init__(message)
        self.code, self.details = code, details or {}


class SubmissionUncertain(ComfyError):
    """The peer may have accepted the prompt. Reconcile; do not submit again."""


class ComfyAdapter:
    def __init__(self, settings: Any = None, client: httpx.AsyncClient | None = None):
        self.base_url = (
            settings
            if isinstance(settings, str)
            else getattr(settings, "comfyui_base_url", "http://127.0.0.1:8188")
        ).rstrip("/")
        self.timeout = float(getattr(settings, "comfy_timeout_seconds", 30))
        self.max_download_bytes = int(getattr(settings, "max_upload_bytes", 512 * 1024 * 1024))
        self.scoped_interrupt = bool(getattr(settings, "comfy_scoped_interrupt", False))
        self._client = client

    async def close(self) -> None:
        """Close an injected client only when its caller explicitly owns it."""
        return None

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            return await client.request(method, f"{self.base_url}{path}", **kwargs)

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = await self._request(method, path, **kwargs)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Expected JSON object")
            return value
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyError("ComfyUI request failed", details={"path": path}) from exc

    async def health(self) -> bool:
        try:
            await self._json("GET", "/system_stats")
            return True
        except ComfyError:
            return False

    async def system_stats(self) -> dict:
        return await self._json("GET", "/system_stats")

    async def queue_status(self) -> dict:
        result = await self._json("GET", "/queue")
        for field in ("queue_running", "queue_pending"):
            if not isinstance(result.get(field), list):
                raise ComfyError("Malformed queue response", "COMFY_INVALID_RESPONSE")
        return result

    get_queue = queue_status

    async def submit(self, workflow: dict, client_id: str) -> str:
        if not workflow or not client_id:
            raise ValueError("workflow and client_id are required")
        try:
            response = await self._request(
                "POST",
                "/prompt",
                json={
                    "prompt": workflow,
                    "client_id": client_id,
                    "extra_data": {"client_id": client_id, "studio_generation_id": client_id},
                },
            )
        except httpx.TransportError as exc:
            raise SubmissionUncertain(
                "Submission response unavailable; reconcile correlation before retry",
                "COMFY_SUBMISSION_UNCERTAIN",
            ) from exc
        if response.status_code >= 500:
            raise SubmissionUncertain(
                "ComfyUI returned an ambiguous server error", "COMFY_SUBMISSION_UNCERTAIN"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            if response.is_success:
                raise SubmissionUncertain(
                    "Submission returned an unreadable response", "COMFY_SUBMISSION_UNCERTAIN"
                ) from exc
            raise ComfyError("ComfyUI rejected workflow", "COMFY_REJECTED") from exc
        if not isinstance(payload, dict):
            raise SubmissionUncertain(
                "Submission response has no prompt ID", "COMFY_SUBMISSION_UNCERTAIN"
            )
        if not response.is_success or payload.get("error"):
            raise ComfyError(
                "ComfyUI rejected workflow",
                "COMFY_REJECTED",
                {"error": payload.get("error"), "node_errors": payload.get("node_errors", {})},
            )
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise SubmissionUncertain(
                "Submission response has no prompt ID", "COMFY_SUBMISSION_UNCERTAIN"
            )
        return prompt_id

    @staticmethod
    def _prompt_id(prompt_id: str) -> str:
        if not prompt_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in prompt_id
        ):
            raise ValueError("Invalid prompt identifier")
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict | None:
        value = await self._json("GET", f"/history/{self._prompt_id(prompt_id)}")
        entry = value.get(prompt_id)
        if entry is not None and not isinstance(entry, dict):
            raise ComfyError("Malformed history response", "COMFY_INVALID_RESPONSE")
        return entry

    @staticmethod
    def _correlated(entry: Any, client_id: str) -> bool:
        if not isinstance(entry, (list, tuple)) or len(entry) < 4 or not isinstance(entry[3], dict):
            return False
        extra = entry[3]
        return extra.get("studio_generation_id") == client_id or extra.get("client_id") == client_id

    async def find_prompt(self, client_id: str) -> str | None:
        queue = await self.queue_status()
        matches = {
            entry[1]
            for entry in queue["queue_running"] + queue["queue_pending"]
            if self._correlated(entry, client_id)
        }
        history = await self._json("GET", "/history")
        matches.update(
            prompt_id
            for prompt_id, entry in history.items()
            if isinstance(entry, dict) and self._correlated(entry.get("prompt"), client_id)
        )
        if len(matches) > 1:
            raise ComfyError(
                "Multiple prompts have the same correlation",
                "COMFY_DUPLICATE_CORRELATION",
                {"prompt_ids": sorted(matches)},
            )
        return next(iter(matches), None)

    find_by_client_id = find_prompt

    async def delete_pending(self, prompt_id: str) -> bool:
        queue = await self.queue_status()
        if not any(
            isinstance(entry, list) and len(entry) > 1 and entry[1] == prompt_id
            for entry in queue["queue_pending"]
        ):
            return False
        response = await self._request(
            "POST", "/queue", json={"delete": [self._prompt_id(prompt_id)]}
        )
        response.raise_for_status()
        queue = await self.queue_status()
        return not any(
            len(entry) > 1 and entry[1] == prompt_id
            for entry in queue["queue_pending"] + queue["queue_running"]
        )

    async def interrupt(self, prompt_id: str) -> bool:
        queue = await self.queue_status()
        running = queue["queue_running"]
        if len(running) != 1 or len(running[0]) < 2 or running[0][1] != prompt_id:
            return False
        # A queue check cannot make ComfyUI's global /interrupt race-free. Only
        # use a runtime explicitly configured to honor a scoped prompt_id.
        if not self.scoped_interrupt:
            return False
        response = await self._request(
            "POST", "/interrupt", json={"prompt_id": self._prompt_id(prompt_id)}
        )
        response.raise_for_status()
        return True

    async def cancel(self, prompt_id: str) -> bool:
        return await self.delete_pending(prompt_id) or await self.interrupt(prompt_id)

    async def upload(self, data: bytes, filename: str, content_type: str) -> str:
        self._media_name(filename)
        value = await self._json(
            "POST",
            "/upload/image",
            files={"image": (filename, data, content_type)},
            data={"type": "input", "overwrite": "false"},
        )
        name = self._media_name(value.get("name", ""))
        subfolder = self._subfolder(value.get("subfolder", ""))
        return f"{subfolder}/{name}" if subfolder else name

    @staticmethod
    def _media_name(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value in {".", ".."}
            or any(c in value for c in "/\\\x00:")
        ):
            raise ComfyError("Unsafe media filename", "COMFY_INVALID_RESPONSE")
        return value

    @staticmethod
    def _subfolder(value: str) -> str:
        if (
            not isinstance(value, str)
            or "\\" in value
            or ":" in value
            or "\x00" in value
            or PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
        ):
            raise ComfyError("Unsafe media subfolder", "COMFY_INVALID_RESPONSE")
        return value

    @classmethod
    def outputs(cls, history: dict, output_node: str | None = None) -> list[dict]:
        nodes = history.get("outputs", {})
        if not isinstance(nodes, dict):
            raise ComfyError("Malformed outputs", "COMFY_INVALID_RESPONSE")
        result, seen = [], set()
        for node_id, node in nodes.items():
            if output_node is not None and str(node_id) != str(output_node):
                continue
            if not isinstance(node, dict):
                continue
            for kind in ("videos", "gifs", "images", "audio"):
                for item in node.get(kind, []) if isinstance(node.get(kind, []), list) else []:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    name = cls._media_name(item["filename"])
                    folder = cls._subfolder(item.get("subfolder", ""))
                    file_type = item.get("type", "output")
                    if file_type not in {"output", "temp"}:
                        continue
                    key = (name, folder, file_type)
                    if key not in seen:
                        result.append(
                            {
                                "filename": name,
                                "subfolder": folder,
                                "type": file_type,
                                "kind": kind,
                                "node_id": str(node_id),
                            }
                        )
                        seen.add(key)
        return result

    async def download(self, output: dict) -> bytes:
        params = {
            "filename": self._media_name(output["filename"]),
            "subfolder": self._subfolder(output.get("subfolder", "")),
            "type": output.get("type", "output"),
        }
        if params["type"] not in {"output", "temp"}:
            raise ComfyError("Output type is not downloadable", "COMFY_INVALID_RESPONSE")

        async def receive(client: httpx.AsyncClient) -> bytes:
            async with client.stream("GET", f"{self.base_url}/view", params=params) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > self.max_download_bytes:
                        raise ComfyError(
                            "Generated output exceeds size limit", "COMFY_OUTPUT_TOO_LARGE"
                        )
                    data.extend(chunk)
                return bytes(data)

        if self._client is not None:
            return await receive(self._client)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            return await receive(client)

    async def stream_progress(self, client_id: str, prompt_id: str) -> AsyncIterator[dict]:
        import websockets

        url = urlsplit(self.base_url)
        endpoint = urlunsplit(
            (
                "wss" if url.scheme == "https" else "ws",
                url.netloc,
                url.path + "/ws",
                urlencode({"clientId": client_id}),
                "",
            )
        )
        async with websockets.connect(
            endpoint, open_timeout=self.timeout, max_size=1024 * 1024
        ) as connection:
            active = None
            async for raw in connection:
                if not isinstance(raw, str):
                    continue
                try:
                    event = json.loads(raw)
                    data, kind = event.get("data", {}), event.get("type")
                    if not isinstance(data, dict):
                        continue
                    if data.get("prompt_id"):
                        active = data["prompt_id"]
                    if active != prompt_id:
                        continue
                    if kind == "progress":
                        current, total = int(data.get("value", 0)), int(data.get("max", 0))
                        if 0 <= current <= total and total > 0:
                            yield {
                                "type": "progress",
                                "prompt_id": prompt_id,
                                "current": current,
                                "total": total,
                                "node_id": data.get("node"),
                            }
                    elif kind in {
                        "execution_start",
                        "executing",
                        "execution_error",
                        "execution_interrupted",
                        "execution_success",
                    }:
                        yield {"type": kind, "prompt_id": prompt_id, "node_id": data.get("node")}
                except (ValueError, TypeError, AttributeError):
                    continue
