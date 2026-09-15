import pytest

from apps.api.app.services.h3_validator import (
    H3Profile,
    H3Request,
    H3ValidationError,
    H3Validator,
)


def validator() -> H3Validator:
    return H3Validator(
        H3Profile(
            canvas_multiple=32,
            max_pixels=1920 * 1088,
            fps=24,
            frame_base=5,
            frame_step=17,
            max_reference_images=9,
            max_reference_audio=3,
            max_reference_videos=3,
        )
    )


def test_valid_t2v_request_calculates_h3_frame_grid() -> None:
    result = validator().validate(H3Request(mode="t2v", width=864, height=480, duration_seconds=5))

    assert result.frames == 124
    assert result.width == 864
    assert result.height == 480


def test_rejects_non_multiple_canvas_dimensions() -> None:
    with pytest.raises(H3ValidationError, match="multiple of 32"):
        validator().validate(H3Request(mode="t2v", width=865, height=480, duration_seconds=5))


def test_rejects_canvas_above_profile_pixel_limit() -> None:
    with pytest.raises(H3ValidationError, match="pixel area"):
        validator().validate(H3Request(mode="t2v", width=1920, height=1152, duration_seconds=5))


def test_rejects_first_last_mode_without_both_frames() -> None:
    with pytest.raises(H3ValidationError, match="first_frame and last_frame"):
        validator().validate(
            H3Request(
                mode="i2v_first_last",
                width=864,
                height=480,
                duration_seconds=5,
                first_frame_asset_id="asset-first",
            )
        )


def test_rejects_reference_count_above_profile_limit() -> None:
    with pytest.raises(H3ValidationError, match="reference images"):
        validator().validate(
            H3Request(
                mode="r2v",
                width=864,
                height=480,
                duration_seconds=5,
                reference_image_asset_ids=[f"asset-{i}" for i in range(10)],
            )
        )
