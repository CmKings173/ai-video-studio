"""Disjoint routing rules for the approved H3 workflow modes."""

from dataclasses import dataclass, field


class WorkflowRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class RoutingInput:
    requested_mode: str | None = None
    first_frame_asset_id: str | None = None
    last_frame_asset_id: str | None = None
    reference_image_asset_ids: list[str] = field(default_factory=list)
    reference_audio_asset_ids: list[str] = field(default_factory=list)
    reference_video_asset_ids: list[str] = field(default_factory=list)


def select_mode(value: RoutingInput) -> str:
    has_refs = any(
        (
            value.reference_image_asset_ids,
            value.reference_audio_asset_ids,
            value.reference_video_asset_ids,
        )
    )
    if value.last_frame_asset_id and not value.first_frame_asset_id:
        raise WorkflowRoutingError("last_frame requires first_frame")
    if has_refs and (value.first_frame_asset_id or value.last_frame_asset_id):
        raise WorkflowRoutingError("frame inputs and reference inputs cannot be mixed")
    if (
        value.first_frame_asset_id
        and value.last_frame_asset_id
        and value.first_frame_asset_id == value.last_frame_asset_id
    ):
        raise WorkflowRoutingError("first_frame and last_frame must be different assets")
    derived = (
        "r2v"
        if has_refs
        else "i2v_first_last"
        if value.first_frame_asset_id and value.last_frame_asset_id
        else "i2v"
        if value.first_frame_asset_id
        else "t2v"
    )
    if value.requested_mode and value.requested_mode != derived:
        raise WorkflowRoutingError(
            f"requested mode {value.requested_mode!r} conflicts with supplied inputs ({derived})"
        )
    return derived
