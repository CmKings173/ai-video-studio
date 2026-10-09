import ast
import math
import subprocess
from pathlib import Path

import pytest

from apps.api.app.providers.minimax_h3_director.output_canvas import resolve_director_output_canvas


@pytest.mark.parametrize(
    "mode,enabled,mp,expected",
    [
        ("upscale", False, 2.0, (864, 480)),
        ("refine", True, 2.0, (864, 480)),
        ("upscale", True, 0.0, (1376, 768)),
        ("upscale", True, 0.05, (1376, 768)),
        ("upscale", True, 2.0, (1952, 1088)),
        ("latent_upscale", True, 2.0, (1952, 1088)),
    ],
)
def test_successful_output_matches_known_source_canvases(mode, enabled, mp, expected):
    assert (
        resolve_director_output_canvas(
            (864, 480), {"enabled": enabled, "mode": mode, "megapixels": mp}
        )
        == expected
    )


def test_fl2v_skip_and_pass_count_do_not_silently_resize():
    config = {"enabled": True, "mode": "upscale", "megapixels": 2.0, "passes": 4}
    assert resolve_director_output_canvas((864, 480), config, task="fl2v") == (864, 480)
    assert resolve_director_output_canvas(
        (864, 480), {**config, "skip_fl2v": False}, task="fl2v"
    ) == (1952, 1088)


@pytest.mark.parametrize(
    "config",
    [
        {"enabled": True, "width": 1280},
        {"enabled": True, "height": 720},
        {"mode": "unknown"},
        {"unknown": True},
    ],
)
def test_resolver_rejects_unsupported_or_ignored_settings(config):
    with pytest.raises(ValueError):
        resolve_director_output_canvas((864, 480), config)


def pinned_canvas_math():
    """Execute only pure function ASTs, avoiding any ComfyUI/Torch import or inference."""
    root = Path("D:/project/ComfyUI_MiniMaxH3_Director")
    assert root.is_dir(), "Exact pinned upstream checkout is required for source parity"
    functions = []
    commit = "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb"
    git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    head = (
        subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True)
        .stdout.decode("utf-8")
        .strip()
    )
    assert head == commit, "Upstream checkout HEAD must be the audited source commit"
    status = subprocess.run(
        [*git, "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
    ).stdout
    assert not status.strip(), "Upstream source checkout must be clean"
    for rel, names in [
        ("lib/image_prep.py", {"snap_dimension", "ensure_minimax_canvas"}),
        ("director/refine_pack.py", {"canvas_from_source_megapixels"}),
    ]:
        # Read the immutable commit object, never the mutable checkout files.
        source = subprocess.run(
            [
                *git,
                "show",
                f"{commit}:{rel}",
            ],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8")
        tree = ast.parse(source)
        selected = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        assert {node.name for node in selected} == names
        functions.extend(selected)
    namespace = {"math": math, "MINIMAX_CANVAS_STRIDE": 32, "DEFAULT_UPSCALE_MEGAPIXELS": 1.0}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "pinned_pure_canvas", "exec"),
        namespace,
    )
    return namespace["canvas_from_source_megapixels"]


@pytest.mark.parametrize("base", [(864, 480), (480, 864), (640, 640), (2048, 1152)])
@pytest.mark.parametrize("mp", [0.1, 1.0, 2.0, 16.0])
def test_resolver_matches_exact_pinned_pure_math_without_gpu(base, mp):
    expected = pinned_canvas_math()(*base, mp)
    actual = resolve_director_output_canvas(
        base, {"enabled": True, "mode": "upscale", "megapixels": mp}
    )
    assert actual == expected
    assert actual[0] >= base[0] and actual[1] >= base[1]
