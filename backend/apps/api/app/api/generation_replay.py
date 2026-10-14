"""Replay canonical rows using producer-defined reference paths only.

GenerationService emits assets[].id in snapshot, generation_intent and
director_execution. Lineage is snapshot.lineage.parent_generation_id and
prompt_provenance.inherited_from in those three containers. Workers emit
output_metadata.artifacts[].asset_id; report artifacts may omit the Asset.
Freshness inputs, Product/Brand context, workflow/profile JSON, generation
configuration and asset media_metadata are opaque business data, not FK schemas.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import AuthPrincipal
from apps.api.app.api.ownership import owner_scope
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
    Scene,
    SceneGeneration,
    Video,
)
from apps.api.app.providers.minimax_h3_director.contracts import (
    DirectorExecutionSpec,
    artifact_member_range,
)
from apps.api.app.schemas.api import BatchGenerationDTO, ExecutionGroupDTO, GenerationDTO
from apps.api.app.services.generation_intent import stable_hash

_SINGLE_ASSET_FIELDS = (
    "first_frame_asset_id",
    "last_frame_asset_id",
    "source_video_asset_id",
)
_ASSET_LIST_FIELDS = (
    "reference_image_asset_ids",
    "reference_video_asset_ids",
    "reference_audio_asset_ids",
)


def _cached_resource_missing() -> AppError:
    return AppError(
        "IDEMPOTENCY_RESOURCE_MISSING",
        "The original generation result is unavailable",
        409,
    )


def _cached_resource_forbidden() -> AppError:
    return AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)


def _reference_containers(snapshot: dict) -> list[dict]:
    containers = [snapshot]
    for name in ("generation_intent", "director_execution"):
        if name in snapshot:
            value = snapshot[name]
            if not isinstance(value, dict):
                raise _cached_resource_missing()
            containers.append(value)
    return containers


def _cached_snapshot_asset_ids(snapshot: dict) -> set[str]:
    asset_ids: set[str] = set()
    for container in _reference_containers(snapshot):
        if "assets" not in container:
            continue
        assets = container["assets"]
        if not isinstance(assets, list):
            raise _cached_resource_missing()
        for asset in assets:
            if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
                raise _cached_resource_missing()
            asset_ids.add(asset["id"])
    return asset_ids


def _cached_parent_generation_ids(snapshot: dict) -> set[str]:
    parent_ids: set[str] = set()
    locations = [(snapshot, "lineage", "parent_generation_id")]
    locations.extend(
        (container, "prompt_provenance", "inherited_from")
        for container in _reference_containers(snapshot)
    )
    for container, name, field in locations:
        if name not in container:
            continue
        value = container[name]
        if not isinstance(value, dict):
            raise _cached_resource_missing()
        parent_id = value.get(field)
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise _cached_resource_missing()
            parent_ids.add(parent_id)
    return parent_ids


def _cached_output_asset_ids(metadata: dict) -> set[str]:
    asset_ids: set[str] = set()
    if "artifacts" not in metadata:
        return asset_ids
    artifacts = metadata["artifacts"]
    if not isinstance(artifacts, list):
        raise _cached_resource_missing()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise _cached_resource_missing()
        asset_id = artifact.get("asset_id")
        if asset_id is not None:
            if not isinstance(asset_id, str):
                raise _cached_resource_missing()
            asset_ids.add(asset_id)
    return asset_ids


def _generation_config_asset_ids(config: dict) -> set[str]:
    """Read the typed SceneGenerationConfig fields, never arbitrary nested JSON."""
    if not isinstance(config, dict):
        raise _cached_resource_missing()
    asset_ids: set[str] = set()
    for field in _SINGLE_ASSET_FIELDS:
        asset_id = config.get(field)
        if asset_id is not None:
            if not isinstance(asset_id, str):
                raise _cached_resource_missing()
            asset_ids.add(asset_id)
    for field in _ASSET_LIST_FIELDS:
        values = config.get(field)
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise _cached_resource_missing()
        asset_ids.update(values)
    return asset_ids


def _cached_generation_config_asset_ids(snapshot: dict) -> set[str]:
    """Collect only the persisted schema's SceneGenerationConfig locations."""
    configs: list[dict] = []
    if "generation_config" in snapshot:
        configs.append(snapshot["generation_config"])

    freshness = snapshot.get("generation_freshness")
    if freshness is not None:
        if not isinstance(freshness, dict):
            raise _cached_resource_missing()
        inputs = freshness.get("inputs")
        if inputs is not None:
            if not isinstance(inputs, dict):
                raise _cached_resource_missing()
            scene = inputs.get("scene")
            if scene is not None:
                if not isinstance(scene, dict):
                    raise _cached_resource_missing()
                if "generation_config" in scene:
                    configs.append(scene["generation_config"])
            source_group = inputs.get("source_group")
            if source_group is not None:
                if not isinstance(source_group, list):
                    raise _cached_resource_missing()
                for member in source_group:
                    if not isinstance(member, dict):
                        raise _cached_resource_missing()
                    if "generation_config" in member:
                        configs.append(member["generation_config"])
    return set().union(*(_generation_config_asset_ids(config) for config in configs))


def _output_asset_id(owner_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{owner_id}"))


def _director_bindings(
    snapshot: dict,
    *,
    workflow_id: str,
    member_count: int | None,
) -> tuple[DirectorExecutionSpec, list[dict]]:
    """Read producer identity only from validated, immutable Director execution input."""
    try:
        if not isinstance(snapshot, dict) or snapshot.get("provider") != "minimax_h3_director":
            raise _cached_resource_forbidden()
        spec = DirectorExecutionSpec.model_validate(snapshot.get("director_execution"))
        if spec.provider != "minimax_h3_director" or spec.workflow_id != workflow_id:
            raise _cached_resource_forbidden()
        profile = spec.provenance.get("workflow_profile")
        if not isinstance(profile, dict):
            raise _cached_resource_missing()
        bindings = profile.get("output_artifacts")
        if bindings is None:
            output_node = profile.get("output_node")
            if not isinstance(output_node, str) or not output_node:
                raise _cached_resource_missing()
            bindings = [{"role": "final", "node_id": output_node, "required": True}]
        if not isinstance(bindings, list) or not bindings or len(bindings) > 128:
            raise _cached_resource_missing()
        if "output_artifacts" in snapshot and snapshot["output_artifacts"] != bindings:
            raise _cached_resource_forbidden()
        return spec, bindings
    except AppError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise _cached_resource_missing() from exc


def _director_output_candidates(
    *,
    run_id: str | None,
    generation_ids: list[str],
    bindings: list[dict],
    member_count: int | None,
    aggregate: bool,
) -> tuple[
    dict[tuple[str, str, str], dict],
    dict[tuple[str, int | None, int], dict],
    set[tuple[str, int | None, int]],
    dict[str, bool],
]:
    """Derive worker Asset IDs and GenerationAsset roles from frozen exporters."""
    if not generation_ids:
        raise _cached_resource_missing()
    candidates: dict[tuple[str, str, str], dict] = {}
    by_identity: dict[tuple[str, int | None, int], dict] = {}
    required_identities: set[tuple[str, int | None, int]] = set()
    report_nodes: dict[str, bool] = {}
    seen: set[tuple[str, int | None, int]] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise _cached_resource_missing()
        role = binding.get("role")
        if role == "report":
            node_id = binding.get("node_id")
            required = binding.get("required", False)
            if (
                not isinstance(node_id, str)
                or not node_id
                or type(required) is not bool
                or node_id in report_nodes
            ):
                raise _cached_resource_missing()
            report_nodes[node_id] = required
            continue
        if role not in {"final", "segment", "pre-refine", "pre-face"}:
            raise _cached_resource_missing()
        try:
            coverage = artifact_member_range(binding, member_count)
        except (TypeError, ValueError) as exc:
            raise _cached_resource_missing() from exc
        member_start = coverage.start if "coverage" in binding else binding.get("member_index")
        for ordinal in range(len(coverage)):
            member_index = (
                member_start + ordinal if member_start is not None else None
            )
            if role == "segment" and member_index is None:
                raise _cached_resource_missing()
            if member_index is not None and (
                type(member_index) is not int
                or member_index < 0
                or member_index >= len(generation_ids)
            ):
                raise _cached_resource_missing()
            identity = (role, member_index, ordinal)
            if identity in seen:
                raise _cached_resource_missing()
            seen.add(identity)
            required = binding.get("required", True)
            if type(required) is not bool:
                raise _cached_resource_missing()
            if required:
                required_identities.add(identity)
            target_generation_id = (
                generation_ids[member_index] if member_index is not None else generation_ids[0]
            )
            if aggregate and role == "segment":
                owner_id = target_generation_id
            elif not aggregate and role == "final":
                owner_id = generation_ids[0]
            else:
                namespace_id = run_id if aggregate else generation_ids[0]
                owner_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"director:{namespace_id}:{role}:{member_index}:{ordinal}",
                    )
                )
            asset_id = _output_asset_id(owner_id)
            generation_asset_role = f"DIRECTOR_{role.upper().replace('-', '_')}"
            candidate = {
                "generation_id": target_generation_id,
                "asset_id": asset_id,
                "role": role,
                "generation_asset_role": generation_asset_role,
                "member_index": member_index,
                "ordinal": ordinal,
            }
            key = (target_generation_id, generation_asset_role, asset_id)
            if key in candidates:
                raise _cached_resource_missing()
            candidates[key] = candidate
            by_identity[identity] = candidate
    return candidates, by_identity, required_identities, report_nodes


def _validate_director_manifest(
    run: DirectorRun,
    *,
    candidates_by_identity: dict[tuple[str, int | None, int], dict],
    required_identities: set[tuple[str, int | None, int]],
    report_nodes: dict[str, bool],
    links_by_generation: dict[str, set[tuple[str, str, str]]],
    run_generation_ids: set[str],
) -> None:
    manifest = run.output_manifest
    if not isinstance(manifest, dict):
        raise _cached_resource_missing()
    artifacts = manifest.get("artifacts")
    if artifacts is None and run.status != "COMPLETED" and not manifest:
        artifacts = []
    if not isinstance(artifacts, list):
        raise _cached_resource_missing()
    manifest_keys: set[tuple[str, str, str]] = set()
    manifest_identities: set[tuple[str, int | None, int]] = set()
    manifest_report_nodes: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise _cached_resource_missing()
        role = artifact.get("role")
        asset_id = artifact.get("asset_id")
        if role == "report":
            locator = artifact.get("provider_locator")
            node_id = locator.get("node_id") if isinstance(locator, dict) else None
            if (
                node_id not in report_nodes
                or asset_id is not None
                or node_id in manifest_report_nodes
            ):
                raise _cached_resource_forbidden()
            manifest_report_nodes.add(node_id)
            continue
        member_index = artifact.get("member_index")
        ordinal = artifact.get("ordinal")
        if (member_index is not None and type(member_index) is not int) or type(ordinal) is not int:
            raise _cached_resource_missing()
        candidate = candidates_by_identity.get((role, member_index, ordinal))
        if candidate is None or asset_id != candidate["asset_id"]:
            raise _cached_resource_forbidden()
        key = (
            candidate["generation_id"],
            candidate["generation_asset_role"],
            candidate["asset_id"],
        )
        if key in manifest_keys:
            raise _cached_resource_forbidden()
        manifest_keys.add(key)
        manifest_identities.add((role, member_index, ordinal))
    if run.status == "COMPLETED":
        required_reports = {
            node_id for node_id, required in report_nodes.items() if required
        }
        if not required_identities.issubset(manifest_identities) or not required_reports.issubset(
            manifest_report_nodes
        ):
            raise _cached_resource_forbidden()
        for identity in required_identities:
            candidate = candidates_by_identity.get(identity)
            if candidate is None:
                raise _cached_resource_missing()
            key = (
                candidate["generation_id"],
                candidate["generation_asset_role"],
                candidate["asset_id"],
            )
            if key not in manifest_keys or key not in links_by_generation.get(
                candidate["generation_id"], set()
            ):
                raise _cached_resource_forbidden()
        for generation_id in run_generation_ids:
            expected = {key for key in manifest_keys if key[0] == generation_id}
            if expected != links_by_generation.get(generation_id, set()):
                raise _cached_resource_forbidden()


def _validate_standalone_director_completion(
    generation: SceneGeneration,
    *,
    candidates_by_identity: dict[tuple[str, int | None, int], dict],
    required_identities: set[tuple[str, int | None, int]],
    report_nodes: dict[str, bool],
    links_by_generation: dict[str, set[tuple[str, str, str]]],
) -> None:
    if generation.status != "COMPLETED":
        return
    metadata = generation.output_metadata
    artifacts = metadata.get("artifacts") if isinstance(metadata, dict) else None
    if not isinstance(artifacts, list):
        raise _cached_resource_missing()

    identities: set[tuple[str, int | None, int]] = set()
    manifest_keys: set[tuple[str, str, str]] = set()
    manifest_report_nodes: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise _cached_resource_missing()
        role = artifact.get("role")
        asset_id = artifact.get("asset_id")
        if role == "report":
            locator = artifact.get("provider_locator")
            node_id = locator.get("node_id") if isinstance(locator, dict) else None
            if (
                node_id not in report_nodes
                or asset_id is not None
                or node_id in manifest_report_nodes
            ):
                raise _cached_resource_forbidden()
            manifest_report_nodes.add(node_id)
            continue

        member_index = artifact.get("member_index")
        ordinal = artifact.get("ordinal")
        if (member_index is not None and type(member_index) is not int) or type(ordinal) is not int:
            raise _cached_resource_missing()
        identity = (role, member_index, ordinal)
        candidate = candidates_by_identity.get(identity)
        if candidate is None or asset_id != candidate["asset_id"] or identity in identities:
            raise _cached_resource_forbidden()
        identities.add(identity)
        manifest_keys.add(
            (
                generation.id,
                candidate["generation_asset_role"],
                candidate["asset_id"],
            )
        )

    required_reports = {node_id for node_id, required in report_nodes.items() if required}
    if not required_identities.issubset(identities) or not required_reports.issubset(
        manifest_report_nodes
    ):
        raise _cached_resource_forbidden()
    if manifest_keys != links_by_generation.get(generation.id, set()):
        raise _cached_resource_forbidden()


async def _validate_generation_replay_references(
    session: AsyncSession,
    generations: dict[str, SceneGeneration],
    input_references: dict[str, set[str]],
    config_references: dict[str, set[str]],
    output_references: dict[str, set[str]],
    parent_references: dict[str, set[str]],
    video: Video,
) -> None:
    generation_ids = set(generations)
    generation_asset_links = list(
        (
            await session.execute(
                select(
                    GenerationAsset.generation_id,
                    GenerationAsset.asset_id,
                    GenerationAsset.role,
                ).where(GenerationAsset.generation_id.in_(generation_ids))
            )
        ).all()
    ) if generation_ids else []

    # A DirectorRunMember row, not output_metadata, decides whether a generation
    # was produced by an aggregate run. Load the entire frozen membership so a
    # single-member replay can still verify its run-wide producer contract.
    target_members = list(
        (
            await session.scalars(
                select(DirectorRunMember).where(
                    DirectorRunMember.scene_generation_id.in_(generation_ids)
                )
            )
        ).all()
    ) if generation_ids else []
    member_run_by_generation: dict[str, str] = {}
    for member in target_members:
        if member.scene_generation_id in member_run_by_generation:
            raise _cached_resource_forbidden()
        member_run_by_generation[member.scene_generation_id] = member.director_run_id
    run_ids = set(member_run_by_generation.values())
    runs = {
        run.id: run
        for run in (
            await session.scalars(select(DirectorRun).where(DirectorRun.id.in_(run_ids)))
        ).all()
    } if run_ids else {}
    if set(runs) != run_ids:
        raise _cached_resource_forbidden()

    run_members = list(
        (
            await session.scalars(
                select(DirectorRunMember)
                .where(DirectorRunMember.director_run_id.in_(run_ids))
                .order_by(DirectorRunMember.director_run_id, DirectorRunMember.member_index)
            )
        ).all()
    ) if run_ids else []
    run_member_generation_ids = {member.scene_generation_id for member in run_members}
    if run_member_generation_ids:
        aggregate_director_links = list(
            (
                await session.execute(
                    select(
                        GenerationAsset.generation_id,
                        GenerationAsset.asset_id,
                        GenerationAsset.role,
                    ).where(
                        GenerationAsset.generation_id.in_(run_member_generation_ids),
                        GenerationAsset.role.like("DIRECTOR_%"),
                    )
                )
            ).all()
        )
        generation_asset_links = list(set(generation_asset_links) | set(aggregate_director_links))
    run_member_generations = {
        generation.id: generation
        for generation in (
            await session.scalars(
                select(SceneGeneration).where(
                    SceneGeneration.id.in_(run_member_generation_ids)
                )
            )
        ).all()
    } if run_member_generation_ids else {}

    director_candidate_keys: set[tuple[str, str, str]] = set()
    director_candidate_ids: dict[str, set[str]] = {}
    director_generation_ids: set[str] = set()
    director_run_data: list[dict] = []
    referenced_output_ids: set[str] = set()
    for run_id, run in runs.items():
        snapshot = run.input_snapshot
        if (
            run.video_id != video.id
            or run.provider != "minimax_h3_director"
            or not isinstance(snapshot, dict)
            or snapshot.get("kind") != "director_aggregate"
            or snapshot.get("provider") != run.provider
            or snapshot.get("video_id") != run.video_id
            or snapshot.get("workflow_id") != run.workflow_id
        ):
            raise _cached_resource_forbidden()
        try:
            unsigned = {key: value for key, value in snapshot.items() if key != "semantic_hash"}
            if snapshot.get("semantic_hash") != stable_hash(unsigned):
                raise _cached_resource_forbidden()
            member_count = snapshot.get("member_count")
            frozen_members = snapshot.get("members")
            if (
                type(member_count) is not int
                or member_count < 1
                or not isinstance(frozen_members, list)
                or len(frozen_members) != member_count
            ):
                raise _cached_resource_missing()
            spec, bindings = _director_bindings(
                snapshot,
                workflow_id=run.workflow_id,
                member_count=member_count,
            )
        except AppError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise _cached_resource_missing() from exc
        if (
            spec.task != run.task
            or (spec.member_count is not None and spec.member_count != member_count)
        ):
            raise _cached_resource_forbidden()

        actual_members = [member for member in run_members if member.director_run_id == run_id]
        if len(actual_members) != member_count:
            raise _cached_resource_forbidden()
        member_generation_ids: list[str] = []
        member_indices: set[int] = set()
        for index, (member, frozen_member) in enumerate(
            zip(actual_members, frozen_members, strict=True)
        ):
            generation = run_member_generations.get(member.scene_generation_id)
            if (
                not isinstance(frozen_member, dict)
                or generation is None
                or member.member_index != index
                or frozen_member.get("member_index") != index
                or frozen_member.get("scene_generation_id") != member.scene_generation_id
                or frozen_member.get("scene_id") != member.scene_id
                or generation.scene_id != member.scene_id
                or generation.video_id != run.video_id
                or generation.workflow_id != run.workflow_id
                or not isinstance(frozen_member.get("input_snapshot"), dict)
                or frozen_member["input_snapshot"] != generation.input_snapshot
            ):
                raise _cached_resource_forbidden()
            member_snapshot = generation.input_snapshot
            if (
                not isinstance(member_snapshot, dict)
                or member_snapshot.get("provider") != "minimax_h3_director"
                or member_snapshot.get("workflow_id") != generation.workflow_id
            ):
                raise _cached_resource_forbidden()
            if member_snapshot.get("schema_version", 1) >= 2:
                try:
                    member_unsigned = {
                        key: value
                        for key, value in member_snapshot.items()
                        if key != "semantic_hash"
                    }
                    if member_snapshot.get("semantic_hash") != stable_hash(member_unsigned):
                        raise _cached_resource_forbidden()
                    DirectorExecutionSpec.model_validate(
                        member_snapshot.get("director_execution")
                    )
                except AppError:
                    raise
                except (TypeError, ValueError, KeyError) as exc:
                    raise _cached_resource_missing() from exc
            member_generation_ids.append(generation.id)
            member_indices.add(index)

        (
            candidates,
            candidates_by_identity,
            required_identities,
            report_nodes,
        ) = _director_output_candidates(
            run_id=run.id,
            generation_ids=member_generation_ids,
            bindings=bindings,
            member_count=member_count,
            aggregate=True,
        )
        segment_indices = {
            candidate["member_index"]
            for candidate in candidates_by_identity.values()
            if candidate["role"] == "segment"
        }
        if segment_indices != member_indices:
            raise _cached_resource_forbidden()
        for key, candidate in candidates.items():
            director_candidate_keys.add(key)
            director_candidate_ids.setdefault(candidate["generation_id"], set()).add(
                candidate["asset_id"]
            )
        director_generation_ids.update(member_generation_ids)
        ordered_member_generations = [
            run_member_generations[generation_id] for generation_id in member_generation_ids
        ]
        for member, generation in zip(
            actual_members, ordered_member_generations, strict=True
        ):
            selected = [
                candidate
                for candidate in candidates_by_identity.values()
                if candidate["role"] == "segment"
                and candidate["member_index"] == member.member_index
            ]
            if len(selected) != 1 or member.output_asset_id != generation.output_asset_id:
                raise _cached_resource_forbidden()
            expected_output_id = selected[0]["asset_id"]
            if member.output_asset_id is not None and member.output_asset_id != expected_output_id:
                raise _cached_resource_forbidden()
            if run.status == "COMPLETED" and member.output_asset_id != expected_output_id:
                raise _cached_resource_forbidden()
            if member.output_asset_id is not None:
                referenced_output_ids.add(member.output_asset_id)
        director_run_data.append(
            {
                "run": run,
                "candidates": candidates,
                "candidates_by_identity": candidates_by_identity,
                "required_identities": required_identities,
                "report_nodes": report_nodes,
                "run_generation_ids": set(member_generation_ids),
            }
        )

    # Standalone Director generations use their immutable execution snapshot.
    # Aggregate members above deliberately use the run snapshot and run identity.
    standalone_run_data: list[dict] = []
    for generation_id, generation in generations.items():
        if generation_id in member_run_by_generation:
            continue
        snapshot = generation.input_snapshot
        is_director = isinstance(snapshot, dict) and (
            snapshot.get("provider") == "minimax_h3_director"
            or "director_execution" in snapshot
        )
        if not is_director:
            continue
        if snapshot.get("schema_version", 1) >= 2:
            try:
                unsigned = {key: value for key, value in snapshot.items() if key != "semantic_hash"}
                if snapshot.get("semantic_hash") != stable_hash(unsigned):
                    raise _cached_resource_forbidden()
            except AppError:
                raise
            except (TypeError, ValueError, KeyError) as exc:
                raise _cached_resource_missing() from exc
        spec, bindings = _director_bindings(
            snapshot,
            workflow_id=generation.workflow_id,
            member_count=None,
        )
        if spec.task not in {"t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"}:
            raise _cached_resource_forbidden()
        (
            candidates,
            candidates_by_identity,
            required_identities,
            report_nodes,
        ) = _director_output_candidates(
            run_id=None,
            generation_ids=[generation_id],
            bindings=bindings,
            member_count=spec.member_count,
            aggregate=False,
        )
        final_candidates = [
            candidate
            for candidate in candidates_by_identity.values()
            if candidate["role"] == "final"
        ]
        if len(final_candidates) != 1:
            raise _cached_resource_forbidden()
        final_asset_id = final_candidates[0]["asset_id"]
        if (
            generation.output_asset_id is not None
            and generation.output_asset_id != final_asset_id
        ):
            raise _cached_resource_forbidden()
        if generation.status == "COMPLETED" and generation.output_asset_id != final_asset_id:
            raise _cached_resource_forbidden()
        for key, candidate in candidates.items():
            director_candidate_keys.add(key)
            director_candidate_ids.setdefault(generation_id, set()).add(candidate["asset_id"])
        director_generation_ids.add(generation_id)
        standalone_run_data.append(
            {
                "generation_id": generation_id,
                "candidates": candidates,
                "candidates_by_identity": candidates_by_identity,
                "required_identities": required_identities,
                "report_nodes": report_nodes,
            }
        )

    linked_asset_ids = {asset_id for _, asset_id, _ in generation_asset_links}
    director_links_by_generation: dict[str, set[tuple[str, str, str]]] = {}
    input_asset_links: set[tuple[str, str]] = set()
    for generation_id, asset_id, role in generation_asset_links:
        if role in {
            "FIRST_FRAME", "LAST_FRAME", "SOURCE_VIDEO", "REFERENCE_IMAGE",
            "REFERENCE_AUDIO", "REFERENCE_VIDEO",
        }:
            if asset_id not in input_references.get(generation_id, set()):
                raise _cached_resource_forbidden()
            input_asset_links.add((generation_id, asset_id))
        elif role.startswith("DIRECTOR_"):
            if (generation_id, role, asset_id) not in director_candidate_keys:
                raise _cached_resource_forbidden()
            director_links_by_generation.setdefault(generation_id, set()).add(
                (generation_id, role, asset_id)
            )
        else:
            raise _cached_resource_forbidden()

    for data in director_run_data:
        _validate_director_manifest(
            data["run"],
            candidates_by_identity=data["candidates_by_identity"],
            required_identities=data["required_identities"],
            report_nodes=data["report_nodes"],
            links_by_generation=director_links_by_generation,
            run_generation_ids=data["run_generation_ids"],
        )
    for data in standalone_run_data:
        _validate_standalone_director_completion(
            generations[data["generation_id"]],
            candidates_by_identity=data["candidates_by_identity"],
            required_identities=data["required_identities"],
            report_nodes=data["report_nodes"],
            links_by_generation=director_links_by_generation,
        )

    output_ids = set().union(*output_references.values()) if output_references else set()
    for generation_id, generation in generations.items():
        asset_ids = output_references.get(generation_id, set())
        if generation_id in director_generation_ids:
            if not asset_ids.issubset(director_candidate_ids.get(generation_id, set())):
                raise _cached_resource_forbidden()
            if not asset_ids.issubset(
                {
                    asset_id
                    for _, _, asset_id in director_links_by_generation.get(
                        generation_id, set()
                    )
                }
            ):
                raise _cached_resource_forbidden()
        else:
            canonical_id = _output_asset_id(generation_id)
            if (
                generation.output_asset_id is not None
                and generation.output_asset_id != canonical_id
            ):
                raise _cached_resource_forbidden()
            if any(asset_id != canonical_id for asset_id in asset_ids):
                raise _cached_resource_forbidden()
            if asset_ids and generation.output_asset_id != canonical_id:
                raise _cached_resource_forbidden()
            if generation.status == "COMPLETED" and generation.output_asset_id != canonical_id:
                raise _cached_resource_forbidden()

    director_link_asset_ids = {
        asset_id
        for links in director_links_by_generation.values()
        for _generation_id, _role, asset_id in links
    }
    output_asset_ids = output_ids | director_link_asset_ids | referenced_output_ids
    all_asset_ids = (
        set().union(*input_references.values(), *config_references.values())
        if input_references or config_references
        else set()
    ) | linked_asset_ids | output_asset_ids
    if all_asset_ids:
        visible_assets = {
            row.id: row
            for row in (
                await session.execute(
                    select(
                        Asset.id,
                        Asset.project_id,
                        Asset.product_id,
                        Asset.role,
                        Asset.kind,
                        Asset.status,
                        Asset.resource_owner_id,
                        Asset.ownership_provenance,
                        Asset.created_by,
                    ).where(
                        Asset.id.in_(all_asset_ids),
                        owner_scope(Asset, video.created_by),
                    )
                )
            ).all()
        }
        visible_ids = set(visible_assets)
        if visible_ids != all_asset_ids:
            existing_ids = set(
                (
                    await session.scalars(
                        select(Asset.id).where(Asset.id.in_(all_asset_ids))
                    )
                ).all()
            )
            if existing_ids - visible_ids:
                raise _cached_resource_forbidden()
            raise _cached_resource_missing()

        for asset_id in output_asset_ids:
            asset = visible_assets[asset_id]
            current_owner = (
                asset.resource_owner_id == video.created_by
                and asset.ownership_provenance == "VIDEO_PARENT"
            )
            canonical_legacy = (
                asset.resource_owner_id is None
                and asset.ownership_provenance is None
                and asset.created_by == video.created_by
            )
            if (
                asset.role != "GENERATED_VIDEO"
                or asset.kind != "VIDEO"
                or asset.status != "READY"
                or asset.project_id != video.project_id
                or asset.product_id is not None
                or not (current_owner or canonical_legacy)
            ):
                raise _cached_resource_forbidden()

        # Same scope contract as get_video_reference_asset, checked in one query.
        input_and_config_ids = set().union(
            *input_references.values(), *config_references.values()
        ) if input_references or config_references else set()
        for asset_id in input_and_config_ids:
            if asset_id in output_asset_ids:
                raise _cached_resource_forbidden()
            asset = visible_assets[asset_id]
            if (
                (asset.project_id is not None and asset.project_id != video.project_id)
                or (asset.product_id is not None and asset.product_id != video.product_id)
                or (asset.project_id is None and asset.product_id is None)
            ):
                raise _cached_resource_forbidden()

        if any(
            (generation_id, asset_id) not in input_asset_links
            for generation_id, asset_ids in input_references.items()
            for asset_id in asset_ids
        ):
            raise _cached_resource_forbidden()

    all_parent_ids = set().union(*parent_references.values()) if parent_references else set()
    if all_parent_ids:
        parents = {
            row.id: row
            for row in (
                await session.scalars(
                    select(SceneGeneration).where(
                        SceneGeneration.id.in_(all_parent_ids),
                        owner_scope(SceneGeneration, video.created_by),
                    )
                )
            ).all()
        }
        if set(parents) != all_parent_ids:
            existing_ids = set(
                (
                    await session.scalars(
                        select(SceneGeneration.id).where(
                            SceneGeneration.id.in_(all_parent_ids)
                        )
                    )
                ).all()
            )
            if existing_ids - set(parents):
                raise _cached_resource_forbidden()
            raise _cached_resource_missing()
        for generation_id, parent_ids in parent_references.items():
            generation = generations[generation_id]
            if any(
                parents[parent_id].scene_id != generation.scene_id
                or parents[parent_id].video_id != generation.video_id
                for parent_id in parent_ids
            ):
                raise _cached_resource_forbidden()


def _validated_generation_replay_dto(
    entry: dict,
    generation: SceneGeneration,
    *,
    video: Video,
) -> tuple[GenerationDTO, set[str], set[str], set[str], set[str]]:
    if not isinstance(entry, dict):
        raise _cached_resource_missing()
    if generation.video_id != video.id:
        raise _cached_resource_forbidden()

    # These fields identify the immutable resource/provenance represented by the cache.
    # State, progress, snapshots, and output metadata are instead returned from the row.
    for field in (
        "video_id",
        "scene_id",
        "mode",
        "workflow_id",
        "generation_no",
        "operation",
        "parent_generation_id",
    ):
        if field in entry and entry[field] != getattr(generation, field):
            raise _cached_resource_forbidden()

    asset_ids: set[str] = set()
    config_asset_ids: set[str] = set()
    output_ids: set[str] = set()
    parent_ids = (
        {generation.parent_generation_id}
        if generation.parent_generation_id is not None
        else set()
    )

    cached_snapshot = entry.get("input_snapshot")
    canonical_snapshot = generation.input_snapshot
    if cached_snapshot is not None and not isinstance(cached_snapshot, dict):
        raise _cached_resource_missing()
    if not isinstance(canonical_snapshot, dict):
        raise _cached_resource_missing()
    canonical_config_ids = _cached_generation_config_asset_ids(canonical_snapshot)
    canonical_input_ids = _cached_snapshot_asset_ids(canonical_snapshot)
    for snapshot in (cached_snapshot, canonical_snapshot):
        if snapshot is None:
            continue
        for parent_id in _cached_parent_generation_ids(snapshot):
            if parent_id != generation.parent_generation_id:
                raise _cached_resource_forbidden()
            parent_ids.add(parent_id)
        snapshot_asset_ids = _cached_snapshot_asset_ids(snapshot)
        if snapshot is cached_snapshot and not snapshot_asset_ids.issubset(canonical_input_ids):
            raise _cached_resource_forbidden()
        asset_ids.update(snapshot_asset_ids)
        snapshot_config_ids = _cached_generation_config_asset_ids(snapshot)
        if snapshot is cached_snapshot and not snapshot_config_ids.issubset(canonical_config_ids):
            raise _cached_resource_forbidden()
        config_asset_ids.update(snapshot_config_ids)

    cached_metadata = entry.get("output_metadata")
    canonical_metadata = generation.output_metadata
    if cached_metadata is not None and not isinstance(cached_metadata, dict):
        raise _cached_resource_missing()
    if not isinstance(canonical_metadata, dict):
        raise _cached_resource_missing()
    for metadata in (cached_metadata, canonical_metadata):
        if metadata is None:
            continue
        output_ids.update(_cached_output_asset_ids(metadata))

    # An initially null cache pointer may advance, but a populated pointer is immutable.
    cached_output_id = entry.get("output_asset_id")
    if cached_output_id is not None and cached_output_id != generation.output_asset_id:
        raise _cached_resource_forbidden()
    for output_asset_id in (entry.get("output_asset_id"), generation.output_asset_id):
        if output_asset_id is not None:
            if not isinstance(output_asset_id, str):
                raise _cached_resource_missing()
            output_ids.add(output_asset_id)
    return (
        GenerationDTO.model_validate(generation),
        asset_ids,
        config_asset_ids,
        output_ids,
        parent_ids,
    )


async def _reauthorize_generation_replay(
    session: AsyncSession,
    replay: dict,
    *,
    scene: Scene,
    video: Video,
    user: AuthPrincipal,
) -> GenerationDTO:
    """Reauthorize the cached identity and return the current authoritative row."""
    generation_id = replay.get("id") if isinstance(replay, dict) else None
    if not isinstance(generation_id, str):
        raise _cached_resource_missing()
    generation = await session.scalar(
        select(SceneGeneration).where(
            SceneGeneration.id == generation_id,
            SceneGeneration.scene_id == scene.id,
            SceneGeneration.video_id == video.id,
            owner_scope(SceneGeneration, user),
        )
    )
    if generation is None:
        exists = await session.scalar(
            select(SceneGeneration.id).where(SceneGeneration.id == generation_id)
        )
        if exists is None:
            raise _cached_resource_missing()
        raise _cached_resource_forbidden()
    if generation.scene_id != scene.id:
        raise _cached_resource_forbidden()
    dto, asset_ids, config_asset_ids, output_ids, parent_ids = _validated_generation_replay_dto(
        replay, generation, video=video
    )
    await _validate_generation_replay_references(
        session,
        {generation.id: generation},
        {generation.id: asset_ids},
        {generation.id: config_asset_ids},
        {generation.id: output_ids},
        {generation.id: parent_ids},
        video,
    )
    return dto


async def _reauthorize_batch_replay(
    session: AsyncSession,
    replay: dict,
    *,
    video: Video,
    user: AuthPrincipal,
) -> BatchGenerationDTO:
    """Reauthorize cached members and rebuild the response from current database rows."""
    entries = replay.get("generations") if isinstance(replay, dict) else None
    if not isinstance(entries, list) or not entries:
        raise _cached_resource_missing()
    cached_video_id = replay.get("video_id")
    if not isinstance(cached_video_id, str):
        raise _cached_resource_missing()
    if cached_video_id != video.id:
        raise _cached_resource_forbidden()
    if any(
        not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
        for entry in entries
    ):
        raise _cached_resource_missing()
    generation_ids = [entry["id"] for entry in entries]
    if len(generation_ids) != len(set(generation_ids)):
        raise _cached_resource_missing()

    rows = {
        row.id: row
        for row in (
            await session.scalars(
                select(SceneGeneration).where(
                    SceneGeneration.id.in_(generation_ids),
                    SceneGeneration.video_id == video.id,
                    owner_scope(SceneGeneration, user),
                )
            )
        ).all()
    }
    if set(rows) != set(generation_ids):
        known_ids = set(
            (
                await session.scalars(
                    select(SceneGeneration.id).where(SceneGeneration.id.in_(generation_ids))
                )
            ).all()
        )
        if known_ids - set(rows):
            raise _cached_resource_forbidden()
        raise _cached_resource_missing()

    response_generations: dict[str, GenerationDTO] = {}
    asset_references: dict[str, set[str]] = {}
    config_references: dict[str, set[str]] = {}
    output_references: dict[str, set[str]] = {}
    parent_references: dict[str, set[str]] = {}
    for entry in entries:
        generation = rows[entry["id"]]
        (
            response_generations[generation.id],
            asset_references[generation.id],
            config_references[generation.id],
            output_references[generation.id],
            parent_references[generation.id],
        ) = _validated_generation_replay_dto(entry, generation, video=video)
    await _validate_generation_replay_references(
        session,
        rows,
        asset_references,
        config_references,
        output_references,
        parent_references,
        video,
    )

    groups = replay.get("execution_groups", [])
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise _cached_resource_missing()
    referenced_runs: dict[str, set[str]] = {}
    response_groups: list[ExecutionGroupDTO] = []
    grouped_generation_ids: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise _cached_resource_missing()
        execution_scope = group.get("execution_scope")
        if execution_scope not in {"single_scene", "aggregate"}:
            raise _cached_resource_missing()
        group_generation_ids = group.get("generation_ids", [])
        scene_ids = group.get("scene_ids", [])
        if (
            not isinstance(group_generation_ids, list)
            or not isinstance(scene_ids, list)
            or any(not isinstance(value, str) for value in group_generation_ids + scene_ids)
            or len(group_generation_ids) != len(set(group_generation_ids))
            or any(value not in rows for value in group_generation_ids)
        ):
            raise _cached_resource_missing()
        group_scenes = [rows[value].scene_id for value in group_generation_ids]
        if scene_ids != group_scenes:
            raise _cached_resource_forbidden()
        grouped_generation_ids.extend(group_generation_ids)
        run_id = group.get("director_run_id")
        if (execution_scope == "aggregate") != (run_id is not None):
            raise _cached_resource_forbidden()
        if run_id is not None:
            if not isinstance(run_id, str) or not group_generation_ids:
                raise _cached_resource_missing()
            if run_id in referenced_runs:
                raise _cached_resource_forbidden()
            referenced_runs[run_id] = set(group_generation_ids)
        response_groups.append(
            ExecutionGroupDTO(
                execution_scope=execution_scope,
                scene_ids=scene_ids,
                generation_ids=group_generation_ids,
                director_run_id=run_id,
            )
        )

    if groups and (
        len(grouped_generation_ids) != len(set(grouped_generation_ids))
        or set(grouped_generation_ids) != set(generation_ids)
    ):
        raise _cached_resource_forbidden()

    compatibility_run_id = replay.get("director_run_id")
    if compatibility_run_id is not None:
        if not isinstance(compatibility_run_id, str):
            raise _cached_resource_missing()
        referenced_runs.setdefault(compatibility_run_id, set())

    for run_id, group_generation_ids in referenced_runs.items():
        run = await session.scalar(
            select(DirectorRun).where(
                DirectorRun.id == run_id,
                DirectorRun.video_id == video.id,
                owner_scope(DirectorRun, user),
            )
        )
        if run is None:
            exists = await session.scalar(select(DirectorRun.id).where(DirectorRun.id == run_id))
            if exists is None:
                raise _cached_resource_missing()
            raise _cached_resource_forbidden()
        member_ids = set(
            (
                await session.scalars(
                    select(DirectorRunMember.scene_generation_id).where(
                        DirectorRunMember.director_run_id == run_id
                    )
                )
            ).all()
        )
        if not member_ids or not member_ids.issubset(set(generation_ids)):
            raise _cached_resource_forbidden()
        if group_generation_ids and member_ids != group_generation_ids:
            raise _cached_resource_forbidden()
        if group_generation_ids and any(
            rows[generation_id].workflow_id != run.workflow_id
            for generation_id in group_generation_ids
        ):
            raise _cached_resource_forbidden()
    if compatibility_run_id is not None and groups:
        group_run_ids = {
            group.get("director_run_id")
            for group in groups
            if isinstance(group, dict) and group.get("director_run_id") is not None
        }
        if compatibility_run_id not in group_run_ids:
            raise _cached_resource_forbidden()

    return BatchGenerationDTO(
        video_id=video.id,
        generations=[response_generations[generation_id] for generation_id in generation_ids],
        director_run_id=compatibility_run_id,
        execution_groups=response_groups,
    )
