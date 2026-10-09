import pytest

from apps.api.app.services.executable_weights import collect_executable_weights


def test_inventory_includes_all_audited_loaders_and_auxiliary_weights():
    graph = {
        str(index): {"class_type": cls, "inputs": {field: filename}}
        for index, (cls, field, filename) in enumerate(
            [
                ("UNETLoader", "unet_name", "h3.safetensors"),
                ("CLIPLoader", "clip_name", "clip.safetensors"),
                ("VAELoader", "vae_name", "vae.safetensors"),
                ("LoraLoaderModelOnly", "lora_name", "turbo.safetensors"),
                ("LoraLoader", "lora_name", "turbo.safetensors"),
                ("UpscaleModelLoader", "model_name", "pixel.pth"),
                ("MiniMaxH3DirectorRefine", "latent_upscale_model", "latent.safetensors"),
                ("MiniMaxH3DirectorFaceRefine", "detector", "face.pt"),
            ]
        )
    }
    graph["asset"] = {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
    assert collect_executable_weights(graph) == {
        "h3.safetensors",
        "clip.safetensors",
        "vae.safetensors",
        "turbo.safetensors",
        "pixel.pth",
        "latent.safetensors",
        "face.pt",
    }


@pytest.mark.parametrize("value", [None, "", " ", ["dynamic", 0]])
@pytest.mark.parametrize(
    "cls,field",
    [
        ("MiniMaxH3DirectorRefine", "latent_upscale_model"),
        ("MiniMaxH3DirectorFaceRefine", "detector"),
    ],
)
def test_auxiliary_weight_dependency_cannot_be_empty_or_dynamic(cls, field, value):
    with pytest.raises(ValueError):
        collect_executable_weights({"config": {"class_type": cls, "inputs": {field: value}}})
