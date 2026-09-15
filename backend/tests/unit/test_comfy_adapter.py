from types import SimpleNamespace

from apps.api.app.integrations.comfy_adapter import ComfyAdapter


def test_adapter_uses_the_shared_comfy_timeout_setting():
    adapter = ComfyAdapter(
        SimpleNamespace(
            comfyui_base_url="http://comfy.test",
            comfy_timeout_seconds=77,
            max_upload_bytes=1024,
            comfy_scoped_interrupt=False,
        )
    )

    assert adapter.timeout == 77


def test_output_parser_filters_unsafe_and_unrelated_nodes():
    history = {
        "outputs": {
            "9": {
                "videos": [
                    {"filename": "result.mp4", "subfolder": "studio", "type": "output"}
                ]
            },
            "10": {
                "videos": [
                    {"filename": "other.mp4", "subfolder": "studio", "type": "output"}
                ]
            },
        }
    }

    assert ComfyAdapter.outputs(history, "9") == [
        {
            "filename": "result.mp4",
            "subfolder": "studio",
            "type": "output",
            "kind": "videos",
            "node_id": "9",
        }
    ]
