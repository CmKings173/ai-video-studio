import pytest

from apps.api.app.providers.minimax_h3_director.collector import artifact_manifest
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.services.director_run_service import _segment_exporters
from apps.api.scripts import import_director_templates as importer
from tests.unit.test_director_execution_contract import candidate


def dynamic_profile():
    return {
        "output_artifacts": [
            {
                "role": "segment",
                "node_id": "director",
                "coverage": "all_members",
                "required": True,
                "transport": "studio_native_segments_v1",
            }
        ]
    }


def history(indices):
    return {
        "outputs": {
            "director": {
                "studio_director_artifacts": [
                    {
                        "role": "segment",
                        "member_index": index,
                        "filename": f"segment-{ordinal}.mp4",
                        "subfolder": "run",
                        "type": "output",
                    }
                    for ordinal, index in enumerate(indices)
                ]
            }
        }
    }


@pytest.mark.parametrize("count", [1, 2, 3, 5])
def test_dynamic_binding_covers_runtime_count(count):
    profile = dynamic_profile()
    assert _segment_exporters(profile, count) == profile["output_artifacts"]
    artifacts = artifact_manifest(
        history(reversed(range(count))), profile, require_final=False, member_count=count
    )
    assert [item.member_index for item in artifacts] == list(range(count))


@pytest.mark.parametrize(
    "indices", [[0, 1], [0, 1, 1], [0, 1, 3], [0, 1, -1], [0, 1, True], [0, 1, "2"]]
)
def test_dynamic_manifest_rejects_inexact_runtime_coverage(indices):
    with pytest.raises(ValueError, match="MEMBER"):
        artifact_manifest(history(indices), dynamic_profile(), require_final=False, member_count=3)


def test_dynamic_manifest_requires_frozen_count():
    with pytest.raises(ValueError, match="MEMBER"):
        artifact_manifest(history([0, 1]), dynamic_profile(), require_final=False)


def test_historical_execution_signature_without_count_remains_valid():
    spec, _ = candidate()
    historical = spec.model_dump(mode="json", exclude={"member_count"})
    assert DirectorExecutionSpec.model_validate(historical).execution_hash == spec.execution_hash


@pytest.mark.parametrize(
    "extra", [{"count": 2}, {"member_index": 0}, {"required": False}, {"transport": "other"}]
)
def test_dynamic_binding_cannot_mix_static_or_optional_coverage(extra):
    profile = dynamic_profile()
    profile["output_artifacts"][0].update(extra)
    with pytest.raises(ValueError):
        artifact_manifest(history([0, 1]), profile, require_final=False, member_count=2)


def test_importer_emits_count_independent_aggregate_contract(tmp_path, monkeypatch):
    from tests.unit.test_director_config_importer import source_checkout

    source = source_checkout(tmp_path, monkeypatch)
    entries = importer.import_templates(source, tmp_path / "output", aggregate=True)
    assert importer.import_templates(source, tmp_path / "output", aggregate=True) == entries
    assert len(entries) == 6
    for entry in entries:
        assert entry["code"] == f"H3_DIRECTOR_{entry['mode'].upper()}_BASE_AGGREGATE"
        assert entry["file"] == f"director_{entry['mode']}_aggregate.api.json"
        assert entry["execution_scope"] == "aggregate"
        binding = entry["profile"]["output_artifacts"][0]
        assert binding["coverage"] == "all_members"
        assert "count" not in binding and "member_index" not in binding
        assert entry["auto_approve"] is False
