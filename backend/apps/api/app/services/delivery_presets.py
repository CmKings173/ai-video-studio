"""Bounded delivery canvases and ratio-preserving FFmpeg geometry.

This describes deterministic export, independently of model generation or AI
enhancement. Callers pass only explicitly supplied raw dimensions with a preset.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

DELIVERY_PRESETS = MappingProxyType(
    {
        "SOCIAL_VERTICAL_1080": (1080, 1920),
        "LANDSCAPE_FHD": (1920, 1080),
        "SQUARE_1080": (1080, 1080),
        "PORTRAIT_4_5": (1080, 1350),
        "PORTRAIT_3_4": (1080, 1440),
        "LANDSCAPE_4_3": (1440, 1080),
        "ULTRAWIDE_2560_1080": (2560, 1080),
    }
)
MAX_DELIVERY_PIXELS = 2560 * 1440


@dataclass(frozen=True)
class DeliveryCanvas:
    preset: str | None
    width: int
    height: int
    fps: int
    fit_mode: str

    def video_filter(self) -> str:
        direction = "decrease" if self.fit_mode == "FIT_PAD" else "increase"
        geometry = (
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2"
            if self.fit_mode == "FIT_PAD"
            else f"crop={self.width}:{self.height}:(iw-ow)/2:(ih-oh)/2"
        )
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio={direction}:"
            f"force_divisible_by=2,{geometry},setsar=1,fps={self.fps},format=yuv420p"
        )


def _dimension(value: int) -> int:
    if type(value) is not int or not 256 <= value <= 4096 or value % 2:
        raise ValueError("Delivery dimensions must be even integers between 256 and 4096")
    return value


def resolve_delivery(
    *,
    preset: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int = 24,
    fit_mode: str = "FIT_PAD",
) -> DeliveryCanvas:
    """Resolve declared export settings, rejecting overrides that would be ignored."""
    if type(fps) is not int or fps not in (24, 25, 30):
        raise ValueError("Delivery FPS must be 24, 25 or 30")
    if fit_mode not in ("FIT_PAD", "CENTER_CROP"):
        raise ValueError("Delivery fit mode must be FIT_PAD or CENTER_CROP")
    if width is not None:
        _dimension(width)
    if height is not None:
        _dimension(height)
    if preset is not None:
        if not isinstance(preset, str) or preset not in DELIVERY_PRESETS:
            raise ValueError("Unknown delivery preset")
        resolved_width, resolved_height = DELIVERY_PRESETS[preset]
        if (width is not None and width != resolved_width) or (
            height is not None and height != resolved_height
        ):
            raise ValueError("Raw dimensions conflict with the delivery preset")
    else:
        resolved_width = 1080 if width is None else width
        resolved_height = 1920 if height is None else height
    if resolved_width * resolved_height > MAX_DELIVERY_PIXELS:
        raise ValueError("Delivery canvas exceeds the supported pixel area")
    return DeliveryCanvas(preset, resolved_width, resolved_height, fps, fit_mode)
