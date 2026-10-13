"""Pure display adapters for server-resolved and historical generation canvases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from apps.api.app.schemas.api import ResolvedGenerationConfigurationDTO

_MARKETING_LABELS: dict[tuple[int, int], str] = {
    (854, 480): "480p",
    (480, 854): "480p",
    (1280, 720): "720p HD",
    (720, 1280): "720p HD",
    (1920, 1080): "1080p Full HD",
    (1080, 1920): "1080p Full HD",
    (2560, 1440): "1440p QHD",
    (1440, 2560): "1440p QHD",
    (3840, 2160): "2160p 4K UHD",
    (2160, 3840): "2160p 4K UHD",
}
_JS_SAFE_INTEGER_MAX = (1 << 53) - 1


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    if isinstance(value, BaseModel) and name in type(value).model_fields:
        return getattr(value, name)
    return None


def _valid_dimensions(width: object, height: object) -> tuple[int, int] | None:
    if (
        type(width) is not int
        or type(height) is not int
        or width <= 0
        or height <= 0
        or width > _JS_SAFE_INTEGER_MAX
        or height > _JS_SAFE_INTEGER_MAX
    ):
        return None
    return width, height


def format_dimensions(width: object, height: object) -> str | None:
    """Return exact positive integer dimensions; partial or malformed values are unknown."""
    dimensions = _valid_dimensions(width, height)
    if dimensions is None:
        return None
    return f"{dimensions[0]} × {dimensions[1]} px"


def _format_resolution(width: object, height: object) -> str | None:
    exact = format_dimensions(width, height)
    dimensions = _valid_dimensions(width, height)
    if exact is None or dimensions is None:
        return None
    marketing_label = _MARKETING_LABELS.get(dimensions)
    return f"{marketing_label} - {exact}" if marketing_label else exact


def format_resolved_resolution(
    configuration: ResolvedGenerationConfigurationDTO | Mapping[str, Any] | None,
    *,
    kind: Literal["native", "output"] = "output",
) -> str | None:
    """Format a canvas from the server-resolved DTO, never from request intent."""
    if configuration is None:
        return None
    if kind == "output":
        width = _field(configuration, "expected_output_width")
        height = _field(configuration, "expected_output_height")
    else:
        width = _field(configuration, "native_width")
        height = _field(configuration, "native_height")
    return _format_resolution(width, height)


def _format_snapshot_fields(snapshot: object) -> str | None:
    resolved = _field(snapshot, "resolved_configuration")
    if resolved is not None:
        output = format_resolved_resolution(resolved, kind="output")
        if output is not None:
            return output

    for width_key, height_key in (
        ("expected_output_width", "expected_output_height"),
        ("resolved_width", "resolved_height"),
        ("width", "height"),
    ):
        formatted = _format_resolution(
            _field(snapshot, width_key), _field(snapshot, height_key)
        )
        if formatted is not None:
            return formatted

    # Frozen pre-DTO execution formats kept their actual canvas in these explicit paths.
    for parent_key, child_key in (
        ("director_execution", "canvas"),
        ("director_execution_spec", "generation"),
    ):
        parent = _field(snapshot, parent_key)
        child = _field(parent, child_key) if parent is not None else None
        formatted = _format_resolution(
            _field(child, "width"), _field(child, "height")
        )
        if formatted is not None:
            return formatted

    if resolved is not None:
        native = format_resolved_resolution(resolved, kind="native")
        if native is not None:
            return native
    return None


def format_historical_resolution(snapshot: object) -> str | None:
    """Safely read recorded resolved dimensions from current or legacy snapshots.

    Request-only fields are deliberately ignored. Legacy ``width``/``height`` are
    accepted because generation snapshots recorded the executed native canvas there.
    """
    if snapshot is None:
        return None
    formatted = _format_snapshot_fields(snapshot)
    if formatted is not None:
        return formatted

    # API history objects wrap the frozen snapshot under this field.
    nested_snapshot = _field(snapshot, "input_snapshot")
    if nested_snapshot is not None and nested_snapshot is not snapshot:
        return _format_snapshot_fields(nested_snapshot)
    return None
