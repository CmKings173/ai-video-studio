from types import SimpleNamespace

from apps.api.app.core.config import Settings
from apps.api.app.providers.minimax_h3_director.timeline import build_timeline
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_intent import GenerationTimelineSegment
from apps.api.app.services.generation_service import (
    GenerationService,
    _inherit_director_timeline,
)
from tests.unit.test_director_execution_contract import candidate


def test_frozen_timeline_projects_public_fields_without_losing_asset_bindings():
    frozen_timeline = [
        {
            "scene_id": "11111111-1111-4111-8111-111111111111",
            "prompt": "Use only the second reference",
            "start_frame": 0,
            "frame_count": 124,
            "continuity_from_previous": False,
            "asset_indices": {"REFERENCE_IMAGE": [1]},
        }
    ]

    request_timeline, inherited_timeline = _inherit_director_timeline(frozen_timeline)

    request = GenerationRequest(timeline=request_timeline)
    assert request.timeline[0].prompt == "Use only the second reference"
    assert isinstance(inherited_timeline[0], GenerationTimelineSegment)
    assert inherited_timeline[0].asset_indices == {"REFERENCE_IMAGE": [1]}

    assets = [
        {
            "id": f"asset-{index}",
            "role": "REFERENCE_IMAGE",
            "order_index": index,
            "checksum": "a" * 64,
            "filename": f"reference-{index}.png",
            "content_type": "image/png",
            "object_key": f"references/{index}.png",
        }
        for index in range(2)
    ]
    service = GenerationService(Settings(_env_file=None, min_free_disk_bytes=0))
    intent = service._build_director_intent(
        request=request,
        mode="t2v",
        scene=SimpleNamespace(id="11111111-1111-4111-8111-111111111111", duration_seconds=5),
        quality="BASE",
        ratio="16:9",
        width=864,
        height=480,
        frames=124,
        fps=24,
        steps=25,
        seed=42,
        prompt="Use only the second reference",
        negative_prompt="",
        assets=assets,
        accepted_prompt=False,
        inherited_timeline=inherited_timeline,
    )
    assert intent.timeline == inherited_timeline

    spec, _ = candidate(
        assets=assets,
        timeline=[segment.model_dump(mode="json") for segment in intent.timeline],
    )
    provider_timeline = build_timeline(
        spec,
        {
            ("REFERENCE_IMAGE", 0): "staged/first.png",
            ("REFERENCE_IMAGE", 1): "staged/second.png",
        },
    )
    assert [reference["imageFile"] for reference in provider_timeline["segments"][0]["refs"]] == [
        "staged/second.png"
    ]
