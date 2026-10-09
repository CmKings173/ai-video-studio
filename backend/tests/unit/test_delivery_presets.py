"""Delivery canvases are independent of model generation resolution."""

import pytest

from apps.api.app.services.delivery_presets import resolve_delivery


@pytest.mark.parametrize(
    ("preset", "width", "height"),
    [
        ("SOCIAL_VERTICAL_1080", 1080, 1920),
        ("LANDSCAPE_FHD", 1920, 1080),
        ("SQUARE_1080", 1080, 1080),
        ("PORTRAIT_4_5", 1080, 1350),
        ("PORTRAIT_3_4", 1080, 1440),
        ("LANDSCAPE_4_3", 1440, 1080),
        ("ULTRAWIDE_2560_1080", 2560, 1080),
    ],
)
def test_named_delivery_dimensions(preset, width, height):
    result = resolve_delivery(preset=preset)
    assert (result.width, result.height, result.preset) == (width, height, preset)
    assert result.fps == 24
    assert result.fit_mode == "FIT_PAD"


def test_legacy_dimensions_and_default_are_preserved():
    result = resolve_delivery()
    assert (result.width, result.height) == (1080, 1920)
    assert result.preset is None
    result = resolve_delivery(width=640, height=480, fps=30, fit_mode="CENTER_CROP")
    assert (result.width, result.height, result.fps) == (640, 480, 30)


def test_ultrawide_is_exactly_64_over_27():
    result = resolve_delivery(preset="ULTRAWIDE_2560_1080")
    assert result.width * 27 == result.height * 64
    assert result.width * 9 != result.height * 21


def test_matching_raw_dimensions_can_accompany_preset():
    result = resolve_delivery(preset="LANDSCAPE_FHD", width=1920, height=1080, fps=25)
    assert result.fps == 25


@pytest.mark.parametrize(
    "arguments",
    [
        {"preset": "LANDSCAPE_FHD", "width": 1080},
        {"preset": "LANDSCAPE_FHD", "height": 1920},
        {"preset": "UNKNOWN"},
        {"preset": True},
        {"width": 257},
        {"height": 128},
        {"width": 4098},
        {"width": 4096, "height": 4096},
        {"width": True},
        {"height": "1080"},
        {"fps": True},
        {"fps": 24.0},
        {"fps": 60},
        {"fit_mode": "STRETCH"},
    ],
)
def test_invalid_or_ignored_delivery_inputs_are_rejected(arguments):
    with pytest.raises(ValueError):
        resolve_delivery(**arguments)


def test_fit_pad_filter_preserves_aspect_and_pads_exact_canvas():
    result = resolve_delivery(width=640, height=480)
    assert result.video_filter() == (
        "scale=640:480:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "pad=640:480:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p"
    )


def test_crop_filter_preserves_aspect_and_crops_exact_canvas():
    result = resolve_delivery(width=640, height=480, fit_mode="CENTER_CROP", fps=25)
    assert result.video_filter() == (
        "scale=640:480:force_original_aspect_ratio=increase:force_divisible_by=2,"
        "crop=640:480:(iw-ow)/2:(ih-oh)/2,setsar=1,fps=25,format=yuv420p"
    )
