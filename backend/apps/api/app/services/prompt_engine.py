"""Deterministic advertising prompt composition with an optional enhancer."""

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PromptResult:
    raw_prompt: str
    execution_prompt: str
    enhanced: bool
    warnings: tuple[str, ...] = ()


class PromptEngine:
    def __init__(self, settings: Any = None, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    @staticmethod
    def _text(value: Any) -> str:
        return " ".join(str(value or "").split())

    def base_prompt(self, context: dict[str, Any]) -> str:
        brand = context.get("brand") or {}
        product = context.get("product") or {}
        scene = context.get("scene") or {}
        spec = scene.get("spec") or {}
        sections = [
            ("subject_definitions", self._text(spec.get("subject") or product.get("name"))),
            ("summary", self._text(scene.get("prompt") or context.get("brief"))),
            (
                "retention_analysis",
                self._text(
                    "Keep product identity, packaging, logo placement, proportions, "
                    "colors and materials stable. " + str(product.get("description") or "")
                ),
            ),
            (
                "detailed_description",
                self._text(
                    "; ".join(
                        str(spec.get(key) or "")
                        for key in (
                            "description",
                            "action",
                            "environment",
                            "camera",
                            "lighting",
                            "style",
                        )
                        if spec.get(key)
                    )
                ),
            ),
            ("overall_soundscape", self._text(spec.get("soundscape"))),
            ("non_diegetic_music", self._text(context.get("music"))),
        ]
        if brand:
            sections[3] = (
                sections[3][0],
                self._text(
                    f"{sections[3][1]}; brand tone: {brand.get('description', '')}; "
                    f"brand context: {brand.get('context', {})}"
                ),
            )
        dialogue = self._text(spec.get("dialogue"))
        if dialogue:
            sections[3] = (sections[3][0], self._text(f"{sections[3][1]}; dialogue: {dialogue}"))
        return "\n".join(f"{name}: {value or 'none'}" for name, value in sections)

    async def compose(
        self, context: dict[str, Any], enhancer: dict[str, Any] | None = None
    ) -> PromptResult:
        base = self.base_prompt(context)
        if not enhancer or not enhancer.get("enabled"):
            return PromptResult(base, base, False)
        url = getattr(self.settings, "llm_base_url", "")
        model = getattr(self.settings, "llm_model", "")
        if not url or not model:
            return PromptResult(base, base, False, ("enhancer_not_configured",))
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Improve this advertising video prompt without changing facts. "
                        "Return prompt text only."
                    ),
                },
                {"role": "user", "content": base},
            ],
            "temperature": float(enhancer.get("temperature", 0.2)),
        }
        headers = {}
        key = getattr(self.settings, "llm_api_key", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            if self.client is not None:
                response = await self.client.post(
                    f"{url.rstrip('/')}/chat/completions", json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                    response = await client.post(
                        f"{url.rstrip('/')}/chat/completions", json=payload, headers=headers
                    )
            response.raise_for_status()
            improved = self._text(response.json()["choices"][0]["message"]["content"])
            if not improved:
                raise ValueError("empty enhancer response")
            return PromptResult(base, improved, True)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return PromptResult(base, base, False, ("enhancer_failed",))
