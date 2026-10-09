"""Hybrid storyboard planner with deterministic validation and fallback."""

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from apps.api.app.schemas.api import SceneCreate, SceneSpec, StoryboardPublish


def _text_limit(schema: type[BaseModel], field: str) -> int:
    return next(
        item.max_length
        for item in schema.model_fields[field].metadata
        if getattr(item, "max_length", None) is not None
    )


class StoryboardError(ValueError):
    pass


@dataclass(frozen=True)
class StoryboardResult:
    scenes: list[dict[str, Any]]
    total_seconds: int
    source: str


class StoryboardService:
    PURPOSES = ("HOOK", "PRODUCT_DETAIL", "BENEFIT", "LIFESTYLE", "CTA")

    def __init__(self, settings: Any = None, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    def deterministic(self, context: dict[str, Any], total_seconds: int) -> StoryboardResult:
        if total_seconds not in {30, 60}:
            raise StoryboardError("long video duration must be 30 or 60 seconds")
        count = 5 if total_seconds == 30 else 8
        base, remainder = divmod(total_seconds, count)
        product = (context.get("product") or {}).get("name") or "the product"
        brief = context.get("brief") or f"Advertising video for {product}"
        prompt_limit = _text_limit(SceneCreate, "prompt")
        scenes = []
        for index in range(count):
            purpose = self.PURPOSES[min(index, len(self.PURPOSES) - 1)]
            duration = base + (1 if index < remainder else 0)
            prefix = f". {purpose.replace('_', ' ').lower()} featuring "
            suffix = f"{prefix}{product}."
            prompt = f"{brief}{suffix}"
            if len(prompt) > prompt_limit:
                # Reserve the complete scene direction before shortening source text.
                suffix = f"{prefix}{product[: prompt_limit - len(prefix) - 1]}."
                prompt = f"{brief[: prompt_limit - len(suffix)]}{suffix}"
            scenes.append(
                {
                    "scene_order": index,
                    "title": purpose.replace("_", " ").title(),
                    "purpose": purpose,
                    "duration_seconds": duration,
                    "prompt": prompt,
                    "negative_prompt": ("distorted product, incorrect label, unreadable text"),
                    "spec": {
                        "title": purpose.replace("_", " ").title(),
                        "purpose": purpose,
                        "description": brief[: _text_limit(SceneSpec, "description")],
                        "subject": product[: _text_limit(SceneSpec, "subject")],
                        "action": "clear commercial product movement",
                        "environment": "controlled advertising set",
                        "camera": "stable cinematic composition",
                        "lighting": "clean product lighting",
                        "style": "commercial, product faithful",
                        "continuity": "CUT",
                    },
                }
            )
        return self.validate(StoryboardResult(scenes, total_seconds, "deterministic"))

    def validate(self, result: StoryboardResult) -> StoryboardResult:
        if not isinstance(result.scenes, list) or any(
            not isinstance(scene, dict) or not isinstance(scene.get("spec"), dict)
            for scene in result.scenes
        ):
            raise StoryboardError("every scene needs prompt and spec")
        try:
            # title/purpose are preview metadata; all publish fields use the API contract.
            payload = StoryboardPublish(
                scenes=[
                    {key: value for key, value in scene.items() if key not in {"title", "purpose"}}
                    for scene in result.scenes
                ]
            )
        except ValidationError as exc:
            raise StoryboardError("storyboard violates publish schema") from exc
        orders = [scene.scene_order for scene in payload.scenes]
        if orders != list(range(len(result.scenes))):
            raise StoryboardError("scene_order must be contiguous from zero")
        durations = [scene.duration_seconds for scene in payload.scenes]
        if abs(sum(durations) - result.total_seconds) > 0.001:
            raise StoryboardError("scene durations must equal total duration")
        return result

    async def _llm(
        self, context: dict[str, Any], total_seconds: int, planner: dict[str, Any]
    ) -> StoryboardResult:
        url = getattr(self.settings, "llm_base_url", "")
        model = getattr(self.settings, "llm_model", "")
        if not url or not model:
            raise StoryboardError("planner_not_configured")
        prompt = {
            "brief": context.get("brief", ""),
            "product": context.get("product", {}),
            "brand": context.get("brand", {}),
            "total_seconds": total_seconds,
            "rules": {
                "scene_count": "2-15",
                "scene_duration_seconds": "4-15",
                "duration_sum": total_seconds,
                "scene_order": "contiguous from zero",
            },
        }
        headers = {}
        key = getattr(self.settings, "llm_api_key", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only a JSON object with a scenes array. Each scene needs "
                        "scene_order, prompt, negative_prompt, duration_seconds, and spec "
                        "matching the supplied advertising rules."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt)},
            ],
            "temperature": float(planner.get("temperature", 0.2)),
            "response_format": {"type": "json_object"},
        }
        if self.client:
            response = await self.client.post(
                f"{url.rstrip('/')}/chat/completions", json=body, headers=headers
            )
        else:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                response = await client.post(
                    f"{url.rstrip('/')}/chat/completions", json=body, headers=headers
                )
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            scenes = parsed["scenes"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            # Only provider decoding failures are eligible for fallback, not planner bugs.
            raise StoryboardError("planner response is invalid") from exc
        return self.validate(StoryboardResult(scenes, total_seconds, "llm"))

    async def plan(
        self,
        context: dict[str, Any],
        total_seconds: int = 30,
        *,
        video_revision: int | None = None,
    ) -> dict[str, Any]:
        planner = context.get("planner") or {}
        warning = None
        result = None
        if planner.get("enabled"):
            try:
                result = await self._llm(context, total_seconds, planner)
            except (StoryboardError, httpx.HTTPError) as exc:
                warning = str(exc) if str(exc) == "planner_not_configured" else "planner_failed"
        if result is None:
            result = self.deterministic(context, total_seconds)
        return {
            "scenes": result.scenes,
            "total_seconds": result.total_seconds,
            "source": "deterministic_fallback" if warning else result.source,
            "warnings": [warning] if warning else [],
            "video_revision": video_revision,
        }
