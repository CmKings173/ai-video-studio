import pytest

from apps.api.app.services.workflow_router import (
    RoutingInput,
    WorkflowRoutingError,
    select_mode,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (RoutingInput(), "t2v"),
        (RoutingInput(first_frame_asset_id="first"), "i2v"),
        (
            RoutingInput(first_frame_asset_id="first", last_frame_asset_id="last"),
            "i2v_first_last",
        ),
        (RoutingInput(reference_image_asset_ids=["reference"]), "r2v"),
    ],
)
def test_routes_disjoint_input_shapes(value, expected):
    assert select_mode(value) == expected


def test_rejects_mixed_frame_and_reference_inputs():
    with pytest.raises(WorkflowRoutingError, match="cannot be mixed"):
        select_mode(
            RoutingInput(
                first_frame_asset_id="frame",
                reference_image_asset_ids=["reference"],
            )
        )


def test_rejects_requested_mode_that_disagrees_with_inputs():
    with pytest.raises(WorkflowRoutingError, match="conflicts"):
        select_mode(RoutingInput(requested_mode="i2v"))
