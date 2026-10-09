"""Pure port of pinned a8f57b8e23c4 Refine canvas math; no runtime/GPU imports."""

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ...schemas.api import DirectorRefine


def resolve_director_output_canvas(
    base_canvas: Sequence[int], refine_settings: Mapping[str, Any], *, task: str | None = None
) -> tuple[int, int]:
    """Resolve successful final output; Face Refine paste-back retains this canvas.

    Source: nodes/director_refine.py (MP fallback), refine_pack.py
    canvas_from_source_megapixels/refine_needs_canvas, image_prep.py stride math,
    and refine_sampling.py skip_fl2v / resize-once before sampling passes.
    """
    width, height = base_canvas
    if any(type(value) is not int or value < 32 or value % 32 for value in (width, height)):
        raise ValueError("Director base canvas requires positive x32 dimensions")
    refine = DirectorRefine.model_validate(dict(refine_settings))
    if not refine.enabled or refine.mode == "refine" or task == "fl2v" and refine.skip_fl2v:
        return width, height
    # Refine.pack forces follow aspect and turns MP < 0.1 (including zero) into 1.0.
    mp = 1.0 if refine.megapixels < 0.1 else refine.megapixels
    mp = min(16.0, max(0.1, mp))
    aspect = width / float(height)
    target_h = math.sqrt(mp * 1024 * 1024 / aspect)
    target_w = aspect * target_h
    tw, th = (max(32, int(round(value / 32) * 32)) for value in (target_w, target_h))
    if tw < width or th < height:
        scale = max(width / float(tw), height / float(th), 1.0)
        tw = max(width, 32, int(round(tw * scale / 32) * 32))
        th = max(height, 32, int(round(th * scale / 32) * 32))
    return tw, th
