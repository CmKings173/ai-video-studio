"""Offline GX10 source-package safety tests."""
import copy
import hashlib
import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "comfy_nodes" / "gx10_package.py"


def load():
    spec = importlib.util.spec_from_file_location("gx10_package", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_official_pin_and_bridge_seams():
    m = load()
    manifest = m.load_manifest()
    assert manifest["commit"] == "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb"
    m.verify_tree(SCRIPT.parent / "studio_director_bridge", manifest["bridge"])


def test_tampered_source_rejected(tmp_path):
    m = load()
    (tmp_path / "node.py").write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        m.verify_tree(tmp_path, {"node.py": "0" * 64})


def test_extra_source_rejected(tmp_path):
    m = load()
    (tmp_path / "extra.py").write_text("injected")
    with pytest.raises(ValueError, match="inventory"):
        m.verify_tree(tmp_path, {})


def test_missing_nodes_rejected():
    with pytest.raises(ValueError, match="node"):
        load().validate_nodes({})


def test_wrong_node_contract_rejected():
    m = load()
    with pytest.raises(ValueError, match="contract"):
        m.validate_nodes({name: {"input": {}, "output": []} for name in m.NODES})


def test_missing_dependency_rejected():
    def missing(name):
        from importlib.metadata import PackageNotFoundError
        raise PackageNotFoundError(name)
    with pytest.raises(ValueError, match="dependency"):
        load().check_dependencies("opencv-python-headless>=4.8", missing)


def test_incompatible_dependency_rejected():
    with pytest.raises(ValueError, match="dependency"):
        load().check_dependencies("scenedetect>=0.6.4,<0.8", lambda _: "0.8.0")


def test_symlink_rejected(tmp_path, monkeypatch):
    m = load()
    target = tmp_path / "target"
    target.write_text("x")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    with pytest.raises(ValueError, match="symlink"):
        m.verify_tree(tmp_path, {})


def test_missing_source_rejected(tmp_path):
    with pytest.raises(ValueError, match="inventory"):
        load().verify_tree(tmp_path, {"missing.py": "0" * 64})


def test_archive_checksum_rejected_before_extraction(tmp_path):
    m = load()
    with pytest.raises(ValueError, match="archive integrity"):
        m.unpack(b"untrusted", tmp_path, m.load_manifest())
    assert list(tmp_path.iterdir()) == []


def test_existing_install_preserved(tmp_path):
    m = load()
    (tmp_path / "main.py").write_text("")
    (tmp_path / "comfy").mkdir()
    bridge = tmp_path / "custom_nodes" / "studio_director_bridge"
    bridge.mkdir(parents=True)
    marker = bridge / "owned.txt"
    marker.write_text("preserve")
    with pytest.raises(ValueError, match="overwrite"):
        m.install(tmp_path)
    assert marker.read_text() == "preserve"


def node_info():
    """Socket names/types inspected in the official pinned INPUT_TYPES methods."""
    director = {
        "required": {"model": ["MODEL"], "video_vae": ["VAE"],
                     "audio_vae": ["VAE"], "clip": ["CLIP"]},
        "optional": {"refine": ["MMX_DIR_REFINE"], "face_refine": ["MMX_DIR_FACE_REFINE"]},
    }
    refine = {
        "required": {"mode": [["refine", "upscale", "latent_upscale"], {}],
                     "upscale_method": [["h3_latent", "lanczos"], {}],
                     "latent_upscale_model": [[], {}], "sampler": [["euler"], {}],
                     "passes": ["INT", {"default": 1}]},
        "optional": {"refine_model": ["MODEL"], "sigmas": ["SIGMAS"]},
    }
    face = {
        "required": {
            "bd_grp_face_detect": ["BDGROUP", {}], "detector": [["face.pt"], {}],
            "confidence": ["FLOAT"], "crop_factor": ["FLOAT"],
            "canvas_width": ["INT"], "canvas_height": ["INT"],
            "canvas_mode": [["manual"]], "select": [["centre_most"]],
            "bd_grp_face_sample": ["BDGROUP"], "denoise": ["FLOAT"], "steps": ["INT"],
            "sampler": [["euler"]], "scheduler": [["normal"]],
        },
        "optional": {"sigmas": ["SIGMAS"]},
    }
    studio = copy.deepcopy(director)
    studio["optional"]["studio_output_prefix"] = ["STRING", {"default": "studio/director"}]
    inputs = dict(zip(load().NODES, [director, refine, face, studio], strict=True))
    return {name: {"output": output, "input": inputs[name]}
            for name, output in load().load_manifest()["outputs"].items()}


def test_all_four_contracts_accepted():
    m = load()
    info = node_info()
    m.validate_nodes(info)
    info["StudioMiniMaxH3Director"]["input"]["optional"]["refine"] = ["MODEL"]
    with pytest.raises(ValueError, match="contract"):
        m.validate_nodes(info)


@pytest.mark.parametrize("bad", [None, "not-a-schema", ["not-a-schema"], 1])
def test_malformed_inventory_root_rejected(bad):
    with pytest.raises(ValueError):
        load().validate_nodes(bad)


@pytest.mark.parametrize("name", load().NODES)
@pytest.mark.parametrize("bad", [None, "not-a-schema", ["not-a-schema"], {"bogus": None}])
@pytest.mark.parametrize("section", ["node", "input", "required", "optional"])
def test_malformed_schema_containers_rejected(name, bad, section):
    info = node_info()
    if section == "node":
        info[name] = bad
    elif section == "input":
        info[name]["input"] = bad
    else:
        info[name]["input"][section] = bad
    with pytest.raises(ValueError):
        load().validate_nodes(info)


@pytest.mark.parametrize("bad", [None, "MODEL", [], [None], [""], [1],
                                  ["MODEL", None], ["MODEL", []], ["MODEL", {}, {}],
                                  [[None]], [[{"bad": "choice"}]]])
@pytest.mark.parametrize("section", ["required", "optional"])
def test_malformed_socket_descriptors_rejected(bad, section):
    info = node_info()
    info["MiniMaxH3DirectorRefine"]["input"][section]["extra"] = bad
    with pytest.raises(ValueError):
        load().validate_nodes(info)


@pytest.mark.parametrize("name", load().NODES)
def test_bogus_but_well_shaped_required_sockets_rejected(name):
    info = node_info()
    info[name]["input"]["required"] = {"bogus": ["INT", {}]}
    with pytest.raises(ValueError):
        load().validate_nodes(info)


@pytest.mark.parametrize("bad", [None, "MMX_DIR_REFINE", [], [None], ["MODEL"],
                                  ["MMX_DIR_REFINE", None]])
def test_malformed_director_refine_socket_rejected(bad):
    info = node_info()
    info["MiniMaxH3Director"]["input"]["optional"]["refine"] = bad
    with pytest.raises(ValueError):
        load().validate_nodes(info)


@pytest.mark.parametrize("name,section,socket", [
    ("MiniMaxH3Director", "required", "video_vae"),
    ("MiniMaxH3DirectorRefine", "required", "passes"),
    ("MiniMaxH3DirectorRefine", "optional", "sigmas"),
    ("MiniMaxH3DirectorFaceRefine", "required", "detector"),
    ("MiniMaxH3DirectorFaceRefine", "optional", "sigmas"),
    ("StudioMiniMaxH3Director", "optional", "studio_output_prefix"),
])
def test_missing_known_sockets_rejected(name, section, socket):
    info = node_info()
    del info[name]["input"][section][socket]
    with pytest.raises(ValueError):
        load().validate_nodes(info)


def test_duplicate_socket_sections_rejected():
    info = node_info()
    info["MiniMaxH3Director"]["input"]["optional"]["model"] = ["MODEL"]
    with pytest.raises(ValueError, match="duplicate"):
        load().validate_nodes(info)


@pytest.mark.parametrize("name", load().NODES)
def test_wrong_known_socket_type_rejected(name):
    info = node_info()
    field = next(iter(info[name]["input"]["required"]))
    info[name]["input"]["required"][field] = ["BOGUS", {}]
    with pytest.raises(ValueError, match="socket contract"):
        load().validate_nodes(info)


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_bridge_accepts_only_pinned_content_across_line_endings(
    tmp_path, monkeypatch, line_ending
):
    spec = importlib.util.spec_from_file_location(
        "gx10_bridge", SCRIPT.parent / "studio_director_bridge" / "__init__.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    node_file = tmp_path / "nodes" / "director.py"
    node_file.parent.mkdir()
    node_file.write_bytes(b"pinned" + line_ending)
    monkeypatch.setattr(bridge, "SOURCE_FILES", {
        "nodes/director.py": hashlib.sha256(b"pinned\n").hexdigest()
    })
    module = types.ModuleType("gx10_fake_director")
    module.__file__ = str(node_file)
    module.finalize_director_outputs = lambda plan: plan
    source = type("Director", (), {"__module__": module.__name__})
    registry = types.ModuleType("nodes")
    registry.NODE_CLASS_MAPPINGS = {"MiniMaxH3Director": source}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, "nodes", registry)
    assert bridge.source_class() is source
    node_file.write_bytes(b"tampered\n")
    with pytest.raises(RuntimeError, match="source drift"):
        bridge.source_class()


@pytest.mark.parametrize("member", ["../escape.py", "/escape.py", "unexpected.py"])
def test_unsafe_archive_members_rejected(tmp_path, member):
    m = load()
    buffer = io.BytesIO()
    manifest = m.load_manifest()
    prefix = f"ComfyUI_MiniMaxH3_Director-{manifest['commit']}/"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(prefix + member, b"bad")
    data = buffer.getvalue()
    manifest["archive_sha256"] = m.digest(data)
    with pytest.raises(ValueError, match="unsafe archive"):
        m.unpack(data, tmp_path, manifest)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("missing_dependency", [False, True])
def test_offline_install_preflight_and_verified_copy(tmp_path, monkeypatch, missing_dependency):
    m = load()
    package = tmp_path / "package"
    bridge = package / "studio_director_bridge"
    bridge.mkdir(parents=True)
    (bridge / "__init__.py").write_bytes(b"# bridge\n")
    manifest = m.load_manifest()
    manifest["bridge"] = {"__init__.py": m.digest(b"# bridge\n")}
    source = {"__init__.py": b"# pinned source\n", "requirements.txt": b"example>=1\n"}
    manifest["source"] = {name: m.digest(data) for name, data in source.items()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in source.items():
            archive.writestr(f"ComfyUI_MiniMaxH3_Director-{manifest['commit']}/{name}", content)
    manifest["archive_sha256"] = m.digest(buffer.getvalue())
    archive_path = tmp_path / "official.zip"
    archive_path.write_bytes(buffer.getvalue())
    monkeypatch.setattr(m, "BASE", package)
    monkeypatch.setattr(m, "load_manifest", lambda: manifest)

    def dependencies(_requirements):
        if missing_dependency:
            raise ValueError("missing dependency: example")
        return {"example": "1.0"}

    monkeypatch.setattr(m, "check_dependencies", dependencies)

    def no_network(*args, **kwargs):
        raise AssertionError("offline installation attempted network access")

    monkeypatch.setattr(m.urllib.request, "urlopen", no_network)
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("")
    (root / "comfy").mkdir()
    custom = root / "custom_nodes"
    custom.mkdir()
    if missing_dependency:
        with pytest.raises(ValueError, match="dependency"):
            m.install(root, archive_path)
        assert not list(custom.iterdir())
    else:
        receipt = m.install(root, archive_path)
        assert receipt["dependencies"] == {"example": "1.0"}
        assert "UNQUALIFIED" in receipt["qualification"]
        m.verify_tree(custom / "ComfyUI_MiniMaxH3_Director", manifest["source"])
        m.verify_tree(custom / "studio_director_bridge", manifest["bridge"])
