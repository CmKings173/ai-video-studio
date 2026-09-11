"""Hybrid storyboard planner with deterministic validation and fallback."""

import json
from dataclasses import dataclass
from typing import Any

import httpx


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
        scenes = []
        for index in range(count):
            purpose = self.PURPOSES[min(index, len(self.PURPOSES) - 1)]
            duration = base + (1 if index < remainder else 0)
            scenes.append(
                {
                    "scene_order": index,
                    "title": purpose.replace("_", " ").title(),
                    "purpose": purpose,
                    "duration_seconds": duration,
                    "prompt": (
                        f"{brief}. {purpose.replace('_', ' ').lower()} featuring {product}."
                    ),
                    "negative_prompt": ("distorted product, incorrect label, unreadable text"),
                    "spec": {
                        "title": purpose.replace("_", " ").title(),
                        "purpose": purpose,
                        "description": brief,
                        "subject": product,
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
        if not 2 <= len(result.scenes) <= 15:
            raise StoryboardError("storyboard must contain 2-15 scenes")
        orders = [int(scene["scene_order"]) for scene in result.scenes]
        if orders != list(range(len(result.scenes))):
            raise StoryboardError("scene_order must be contiguous from zero")
        durations = [float(scene["duration_seconds"]) for scene in result.scenes]
        if any(duration < 4 or duration > 15 for duration in durations):
            raise StoryboardError("each scene must be 4-15 seconds")
        if abs(sum(durations) - result.total_seconds) > 0.001:
            raise StoryboardError("scene durations must equal total duration")
        for scene in result.scenes:
            if not str(scene.get("prompt", "")).strip() or not isinstance(scene.get("spec"), dict):
                raise StoryboardError("every scene needs prompt and spec")
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
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return self.validate(StoryboardResult(parsed["scenes"], total_seconds, "llm"))

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
            except (
                StoryboardError,
                httpx.HTTPError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
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
