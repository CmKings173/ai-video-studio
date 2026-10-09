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
    source_video_asset_id: str | None = None


LEGACY_FL2V_MODES = frozenset({"i2v_last", "i2v_first_last", "fl2v"})


def director_task_for_mode(mode: str) -> str:
    if mode in LEGACY_FL2V_MODES:
        return "fl2v"
    if mode in {"t2v", "i2v", "r2v", "v2v", "rv2v"}:
        return mode
    raise WorkflowRoutingError(f"unsupported generation mode {mode!r}")


def select_mode(value: RoutingInput) -> str:
    has_refs = any(
        (
            value.reference_image_asset_ids,
            value.reference_audio_asset_ids,
            value.reference_video_asset_ids,
        )
    )
    if value.source_video_asset_id and (value.first_frame_asset_id or value.last_frame_asset_id):
        raise WorkflowRoutingError("source video cannot be mixed with first/last frame inputs")
    if has_refs and (value.first_frame_asset_id or value.last_frame_asset_id):
        raise WorkflowRoutingError("frame inputs and reference inputs cannot be mixed")
    if (
        value.first_frame_asset_id
        and value.last_frame_asset_id
        and value.first_frame_asset_id == value.last_frame_asset_id
    ):
        raise WorkflowRoutingError("first_frame and last_frame must be different assets")
    derived = (
        "rv2v"
        if value.source_video_asset_id and has_refs
        else "v2v"
        if value.source_video_asset_id
        else "r2v"
        if has_refs
        else "i2v_first_last"
        if value.first_frame_asset_id and value.last_frame_asset_id
        else "i2v"
        if value.first_frame_asset_id
        else "i2v_last"
        if value.last_frame_asset_id
        else "t2v"
    )
    requested = value.requested_mode
    compatible = {derived}
    if derived in {"i2v_last", "i2v_first_last"}:
        compatible.add("fl2v")
    if requested not in (None, "AUTO", *compatible):
        raise WorkflowRoutingError(
            f"requested mode {value.requested_mode!r} conflicts with supplied inputs ({derived})"
        )
    return derived if requested in (None, "AUTO", derived) else str(requested)


def require_director_execution(record) -> None:
    """Historical registry rows remain readable, but cannot authorize new execution."""
    from apps.api.app.core.errors import AppError
    from apps.api.app.providers.minimax_h3_director.graph_identity import is_director_graph

    if not is_director_graph(record.workflow) or record.profile.get("retired"):
        raise AppError(
            "WORKFLOW_RETIRED",
            "Only MiniMax H3 Director workflows may execute; historical workflows are read-only",
            409,
        )
