"""Filename inventory for executable graph weights, including optional branches."""

from collections.abc import Mapping
from typing import Any

WEIGHT_INPUTS = {
    "UNETLoader": ("unet_name",),
    "CLIPLoader": ("clip_name",),
    "VAELoader": ("vae_name",),
    "UpscaleModelLoader": ("model_name",),
    "MiniMaxH3DirectorRefine": ("latent_upscale_model",),
    "MiniMaxH3DirectorFaceRefine": ("detector",),
}


def collect_executable_weights(workflow: Mapping[str, Any]) -> set[str]:
    """Return exact filename tokens; dynamic/empty weight bindings fail closed.

    Digests are measured externally and bound by the profile/evidence contract.
    Presence of a filename never establishes installation or execution.
    """
    weights = set()
    for node in workflow.values():
        class_type = node["class_type"]
        fields = (
            ("lora_name",)
            if class_type.startswith("LoraLoader")
            else WEIGHT_INPUTS.get(class_type, ())
        )
        inputs = node["inputs"]
        # Retain coverage of legacy file sockets on source-specific loader classes.
        fields = set(fields) | (inputs.keys() & {"unet_name", "clip_name", "vae_name", "lora_name"})
        for field in fields:
            value = inputs.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Executable weight {class_type}.{field} needs an exact filename")
            weights.add(value)
    return weights
