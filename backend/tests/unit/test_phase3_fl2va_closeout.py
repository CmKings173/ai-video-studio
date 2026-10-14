import pytest

from apps.api.app.providers.minimax_h3_director.timeline import build_timeline
from apps.api.app.services.workflow_router import (
    RoutingInput,
    WorkflowRoutingError,
    director_task_for_mode,
    select_mode,
)
from tests.unit.test_director_execution_contract import candidate


def _image(asset_id: str, role: str, order_index: int = 0) -> dict[str, object]:
    return {
        "id": asset_id,
        "role": role,
        "order_index": order_index,
        "checksum": "a" * 64,
        "filename": f"{asset_id}.png",
        "content_type": "image/png",
    }


@pytest.mark.parametrize(
    ("routing", "expected_mode", "expected_task"),
    [
        (RoutingInput(requested_mode="AUTO"), "t2v", "t2v"),
        (
            RoutingInput(requested_mode="AUTO", first_frame_asset_id="first"),
            "i2v",
            "i2v",
        ),
        (
            RoutingInput(requested_mode="AUTO", last_frame_asset_id="last"),
            "i2v_last",
            "fl2v",
        ),
        (
            RoutingInput(
                requested_mode="AUTO",
                first_frame_asset_id="first",
                last_frame_asset_id="last",
            ),
            "i2v_first_last",
            "fl2v",
        ),
        (
            RoutingInput(requested_mode="AUTO", reference_image_asset_ids=["reference"]),
            "r2v",
            "r2v",
        ),
        (
            RoutingInput(requested_mode="AUTO", source_video_asset_id="source"),
            "v2v",
            "v2v",
        ),
        (
            RoutingInput(
                requested_mode="AUTO",
                source_video_asset_id="source",
                reference_image_asset_ids=["reference"],
            ),
            "rv2v",
            "rv2v",
        ),
    ],
)
def test_auto_routing_matrix_maps_business_mode_to_director_task(
    routing: RoutingInput, expected_mode: str, expected_task: str
):
    mode = select_mode(routing)

    assert mode == expected_mode
    assert director_task_for_mode(mode) == expected_task


@pytest.mark.parametrize(
    "routing",
    [
        RoutingInput(requested_mode="i2v", last_frame_asset_id="last"),
        RoutingInput(requested_mode="t2v", first_frame_asset_id="first"),
        RoutingInput(requested_mode="fl2v"),
        RoutingInput(
            requested_mode="i2v",
            first_frame_asset_id="same",
            last_frame_asset_id="same",
        ),
    ],
)
def test_explicit_mode_mismatch_and_duplicate_frames_fail_closed(routing: RoutingInput):
    with pytest.raises(WorkflowRoutingError):
        select_mode(routing)


@pytest.mark.parametrize(
    ("routing", "expected_mode"),
    [
        (RoutingInput(requested_mode="fl2v", last_frame_asset_id="last"), "fl2v"),
        (
            RoutingInput(
                requested_mode="fl2v",
                first_frame_asset_id="first",
                last_frame_asset_id="last",
            ),
            "fl2v",
        ),
        (
            RoutingInput(requested_mode="i2v_last", last_frame_asset_id="last"),
            "i2v_last",
        ),
        (
            RoutingInput(
                requested_mode="i2v_first_last",
                first_frame_asset_id="first",
                last_frame_asset_id="last",
            ),
            "i2v_first_last",
        ),
    ],
)
def test_explicit_fl2v_and_historical_labels_keep_their_task_mapping(
    routing: RoutingInput, expected_mode: str
):
    mode = select_mode(routing)

    assert mode == expected_mode
    assert director_task_for_mode(mode) == "fl2v"


def test_last_only_fl2v_timeline_has_only_an_ending_image():
    last = _image("last", "LAST_FRAME")
    spec, _ = candidate("fl2v", requested_mode="i2v_last", assets=[last])

    timeline = build_timeline(spec, {("LAST_FRAME", 0): "staged/last.png"})

    assert timeline["timelineMode"] == "fl2v"
    assert "segments" not in timeline
    shot = timeline["shots"][0]
    assert shot["taskType"] == "fl2v"
    assert shot["endImage"]["imageFile"] == "staged/last.png"
    assert "startImage" not in shot
    assert "genImage" not in shot
    assert shot["frameCount"] == spec.frames
    assert shot["durationSec"] == spec.frames / spec.fps


def test_first_and_last_fl2v_timeline_preserves_frozen_images_and_shot_values():
    first = _image("first", "FIRST_FRAME")
    last = _image("last", "LAST_FRAME")
    spec, _ = candidate(
        "fl2v",
        requested_mode="i2v_first_last",
        prompt="Accepted frozen prompt",
        seed=731,
        frames=124,
        assets=[first, last],
        timeline=[
            {
                "scene_id": "scene-1",
                "prompt": "Accepted scene prompt",
                "start_frame": 0,
                "frame_count": 124,
                "continuity_from_previous": False,
            }
        ],
    )

    timeline = build_timeline(
        spec,
        {
            ("FIRST_FRAME", 0): "staged/first.png",
            ("LAST_FRAME", 0): "staged/last.png",
        },
    )

    shot = timeline["shots"][0]
    assert spec.task == "fl2v"
    assert spec.requested_mode == "i2v_first_last"
    assert spec.prompt == "Accepted frozen prompt"
    assert spec.seed == 731
    assert timeline["global"]["prompt"] == "Accepted frozen prompt"
    assert shot["prompt"] == "Accepted scene prompt"
    assert shot["startImage"]["imageFile"] == "staged/first.png"
    assert shot["endImage"]["imageFile"] == "staged/last.png"
    assert shot["frameCount"] == 124
    assert shot["durationSec"] == 124 / spec.fps
    assert timeline["totalFrames"] == spec.frames
