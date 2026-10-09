import pytest
from pydantic import ValidationError

from apps.api.app.schemas.api import (
    DirectorFaceRefine,
    GenerationRequest,
    SceneGenerationConfig,
)


@pytest.mark.parametrize("model", [DirectorFaceRefine, SceneGenerationConfig, GenerationRequest])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "detector",
    [
        "",
        "face_yolov8m",
        "face_yolov8m.onnx",
        "face_yolov8n.pt",
        "custom-face_v2.1.pt",
        "unsupported.pt",
        "../face_yolov8m.pt",
        "bbox/face_yolov8m.pt",
        r"bbox\face_yolov8m.pt",
        "/models/face_yolov8m.pt",
        r"C:\models\face_yolov8m.pt",
        "https://example.com/face_yolov8m.pt",
        "face detector.pt",
        "face_yolov8m.pt:stream",
        "face_yolov8m\x00.pt",
        "a" * 253 + ".pt",
    ],
)
def test_face_detector_rejects_invalid_or_unqualified_filenames(model, enabled, detector):
    face = {"enabled": enabled, "detector": detector}
    payload = face if model is DirectorFaceRefine else {"face_refine": face}
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    expected_location = (
        ("detector",) if model is DirectorFaceRefine else ("face_refine", "detector")
    )
    assert expected_location in [error["loc"] for error in exc.value.errors()]


@pytest.mark.parametrize("enabled", [False, True])
def test_face_detector_default_and_explicit_default_roundtrip(enabled):
    default = DirectorFaceRefine(enabled=enabled)
    explicit = DirectorFaceRefine(enabled=enabled, detector="face_yolov8m.pt")
    assert default.detector == "face_yolov8m.pt"
    assert default.model_dump() == explicit.model_dump()
    assert DirectorFaceRefine.model_validate_json(default.model_dump_json()) == default


def test_face_detector_schema_advertises_only_studio_product_option():
    detector = DirectorFaceRefine.model_json_schema()["properties"]["detector"]
    assert detector["default"] == "face_yolov8m.pt"
    assert detector["const"] == "face_yolov8m.pt"
    assert detector["type"] == "string"
