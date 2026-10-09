from types import SimpleNamespace

import pytest

from apps.api.app.providers.minimax_h3_director.collector import artifact_manifest
from comfy_nodes.studio_director_bridge import native_manifest


def profile(count=2):
    return {
        "output_artifacts": [
            {
                "role": "segment",
                "node_id": "director",
                "member_index": 0,
                "count": count,
                "transport": "studio_native_segments_v1",
            }
        ]
    }


def test_native_manifest_retains_exact_member_identity(tmp_path):
    directory = tmp_path / "minimax_seg_export" / "run"
    directory.mkdir(parents=True)
    for index in range(2):
        (directory / f"seg_{index:04d}.mp4").write_bytes(b"video")
    # Other runs and pass outputs cannot be mistaken for member output.
    (directory / "seg_0000_p2.mp4").write_bytes(b"pass")
    plan = SimpleNamespace(
        export_mode="segments",
        run_indices=None,
        segment_mp4_run_dir=directory,
        segments=[SimpleNamespace(index=0), SimpleNamespace(index=1)],
    )
    records = native_manifest(plan, tmp_path)
    records.reverse()
    artifacts = artifact_manifest(
        {"outputs": {"director": {"studio_director_artifacts": records}}},
        profile(),
        require_final=False,
    )
    assert [artifact.member_index for artifact in artifacts] == [0, 1]
    assert [artifact.locator["filename"] for artifact in artifacts] == [
        "seg_0000.mp4",
        "seg_0001.mp4",
    ]


def test_missing_segment_or_escaped_run_directory_fails_closed(tmp_path):
    directory = tmp_path / "minimax_seg_export" / "run"
    directory.mkdir(parents=True)
    plan = SimpleNamespace(
        export_mode="segments",
        run_indices=None,
        segment_mp4_run_dir=directory,
        segments=[SimpleNamespace(index=0)],
    )
    with pytest.raises(ValueError, match="every required"):
        native_manifest(plan, tmp_path)
    plan.segment_mp4_run_dir = tmp_path.parent
    with pytest.raises(ValueError, match="escaped"):
        native_manifest(plan, tmp_path)


def test_duplicate_member_manifest_is_rejected():
    record = {"role": "segment", "member_index": 0, "filename": "a.mp4", "type": "output"}
    with pytest.raises(ValueError, match="COVERAGE"):
        artifact_manifest(
            {"outputs": {"director": {"studio_director_artifacts": [record, record]}}},
            profile(),
            require_final=False,
        )
