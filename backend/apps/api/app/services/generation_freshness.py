"""Semantic editor identity shared by generation, selection and assembly.

This identity describes the source inputs, independently of random seeds, output
selection and editor revision counters. Historical snapshots without it fail closed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    Product,
    Scene,
    SceneGeneration,
    Video,
)
from apps.api.app.services.continuity_groups import continuity_chains


def _fingerprint(inputs: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            inputs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def dependency_semantics(row: Product | Brand | None, identity: str | None) -> dict | None:
    if identity is None:
        return None
    if row is None:
        return {"id": identity, "missing": True}
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "context": deepcopy(row.context),
        "archived": row.archived,
    }


async def generation_dependencies(session: AsyncSession, video: Video) -> dict[str, Any]:
    """Resolve the same effective product/brand used by prompt composition."""
    product = await session.get(Product, video.product_id) if video.product_id else None
    brand_id = video.brand_id or (product.brand_id if product else None)
    brand = await session.get(Brand, brand_id) if brand_id else None
    return {
        "product": dependency_semantics(product, video.product_id),
        "brand": dependency_semantics(brand, brand_id),
    }


async def lock_generation_dependencies(session: AsyncSession, video: Video) -> tuple:
    """Caller holds Video; acquire Product then effective Brand before child rows."""
    product = (
        await lock_revisioned_row(session, Product, video.product_id)
        if video.product_id
        else None
    )
    brand_id = video.brand_id or (product.brand_id if product else None)
    brand = (
        await lock_revisioned_row(session, Brand, brand_id)
        if brand_id
        else None
    )
    return product, brand


def _scene_inputs(scene: Scene) -> dict[str, Any]:
    return {
        "id": scene.id,
        "prompt": scene.prompt,
        "negative_prompt": scene.negative_prompt,
        "duration_seconds": float(scene.duration_seconds),
        "spec": deepcopy(scene.spec or {}),
        "generation_config": deepcopy(scene.generation_config or {}),
    }


async def _source_group(session: AsyncSession, scene: Scene, video: Video) -> list[Scene]:
    scenes = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video.id)
                .order_by(Scene.scene_order, Scene.id)
            )
        ).all()
    )
    # The identity map reflects edits not yet flushed in the editor transaction.
    for chain in continuity_chains(scenes):
        if any(member.id == scene.id for member in chain):
            return chain
    return [scene]


async def capture_generation_freshness(
    session: AsyncSession,
    scene: Scene,
    video: Video,
) -> dict[str, Any]:
    """Capture only for ORIGINAL inputs; derivatives inherit their parent identity."""
    inputs = {
        "scene": _scene_inputs(scene),
        "source_group": [
            _scene_inputs(member) for member in await _source_group(session, scene, video)
        ],
        "video": {
            "id": video.id,
            "project_id": video.project_id,
            "brief": video.brief,
            "aspect_ratio": video.aspect_ratio,
            "music": (video.config or {}).get("music", ""),
            "prompt_enhancer": deepcopy((video.config or {}).get("prompt_enhancer")),
        },
        "dependencies": await generation_dependencies(session, video),
    }
    return {"schema_version": 2, "inputs": inputs, "fingerprint": _fingerprint(inputs)}


def bind_execution_group_freshness(
    generations: Sequence[SceneGeneration],
    run: DirectorRun,
) -> None:
    """Bind preflight members and frozen clones before persisting any jobs.

    Source freshness remains independent of selections. Execution identity prevents
    mixing outputs from different native runs, including identical RANDOM inputs.
    """
    members = (run.input_snapshot or {}).get("members")
    if (
        not run.id
        or not generations
        or not isinstance(members, list)
        or len(members) != len(generations)
        or any(generation.video_id != run.video_id for generation in generations)
        or [member.get("scene_generation_id") for member in members]
        != [generation.id for generation in generations]
        or [member.get("scene_id") for member in members]
        != [generation.scene_id for generation in generations]
        or len({generation.scene_id for generation in generations}) != len(generations)
    ):
        raise AppError("DIRECTOR_GROUP_INVALID", "Prepared run membership changed", 422)
    binding = {
        "run_id": run.id,
        "scene_ids": [g.scene_id for g in generations],
        "generation_ids": [g.id for g in generations],
    }
    snapshots = []
    for generation in generations:
        snapshot = deepcopy(generation.input_snapshot)
        saved = snapshot.get("generation_freshness")
        if (
            not isinstance(saved, dict)
            or saved.get("schema_version") != 2
            or not isinstance(saved.get("inputs"), dict)
            or saved.get("fingerprint") != _fingerprint(saved["inputs"])
        ):
            raise AppError("DIRECTOR_GROUP_INVALID", "Member has no current source identity", 422)
        saved["execution_group"] = deepcopy(binding)
        snapshot.pop("semantic_hash", None)
        snapshot["semantic_hash"] = _fingerprint(snapshot)
        snapshots.append(snapshot)
    frozen_run = deepcopy(run.input_snapshot)
    frozen_run["execution_group"] = deepcopy(binding)
    for member, snapshot in zip(frozen_run["members"], snapshots, strict=True):
        member["input_snapshot"] = deepcopy(snapshot)
    frozen_run.pop("semantic_hash", None)
    frozen_run["semantic_hash"] = _fingerprint(frozen_run)
    for generation, snapshot in zip(generations, snapshots, strict=True):
        generation.input_snapshot = snapshot
    run.input_snapshot = frozen_run


async def is_generation_fresh(
    session: AsyncSession,
    scene: Scene,
    video: Video,
    generation: SceneGeneration | None,
) -> bool:
    """Assess current source semantics without modifying historical selection."""
    if (
        generation is None
        or generation.scene_id != scene.id
        or generation.video_id != video.id
        or scene.video_id != video.id
    ):
        return False
    saved = (generation.input_snapshot or {}).get("generation_freshness")
    if not isinstance(saved, dict) or saved.get("schema_version") != 2:
        return False
    inputs = saved.get("inputs")
    if not isinstance(inputs, dict) or saved.get("fingerprint") != _fingerprint(inputs):
        return False
    current = await capture_generation_freshness(session, scene, video)
    return saved["fingerprint"] == current["fingerprint"]


async def is_selected_execution_group_fresh(
    session: AsyncSession,
    scene: Scene,
    video: Video,
    generation: SceneGeneration,
) -> bool:
    """Association check only, so source-valid members can be selected sequentially."""
    saved = (generation.input_snapshot or {}).get("generation_freshness") or {}
    binding = saved.get("execution_group")
    chain = await _source_group(session, scene, video)
    scene_ids = [member.id for member in chain]
    if binding is None:
        assigned = await session.scalar(
            select(DirectorRunMember.id).where(
                DirectorRunMember.scene_generation_id == generation.id
            )
        )
        return len(chain) == 1 and assigned is None
    if (
        not isinstance(binding, dict)
        or binding.get("scene_ids") != scene_ids
        or not isinstance(binding.get("generation_ids"), list)
        or len(binding["generation_ids"]) != len(scene_ids)
        or len(set(binding["generation_ids"])) != len(scene_ids)
        or not binding.get("run_id")
    ):
        return False
    selected_ids = [member.selected_generation_id for member in chain]
    if selected_ids != binding["generation_ids"] or scene.selected_generation_id != generation.id:
        return False
    run = await session.get(DirectorRun, binding["run_id"])
    if (
        run is None
        or run.video_id != video.id
        or run.status != "COMPLETED"
        or (run.input_snapshot or {}).get("execution_group") != binding
    ):
        return False
    members = list(
        (
            await session.scalars(
                select(DirectorRunMember)
                .where(DirectorRunMember.director_run_id == run.id)
                .order_by(DirectorRunMember.member_index)
            )
        ).all()
    )
    if [member.scene_id for member in members] != scene_ids or [
        member.scene_generation_id for member in members
    ] != selected_ids:
        return False
    for member in members:
        selected = await session.get(SceneGeneration, member.scene_generation_id)
        if (
            selected is None
            or selected.status != "COMPLETED"
            or ((selected.input_snapshot or {}).get("generation_freshness") or {}).get(
                "execution_group"
            )
            != binding
        ):
            return False
    return True


async def restore_video_after_assembly(session, video, attempted_manifest, fallback):
    """A retained historical pointer alone cannot certify a failed/cancelled retry."""
    if video.status == "DIRTY" or video.revision != attempted_manifest.get("video_revision"):
        video.status = "DIRTY"
    elif video.current_final_video_id:
        previous = await session.get(FinalVideo, video.current_final_video_id)
        video.status = (
            "READY"
            if previous
            and previous.status == "READY"
            and await is_assembly_source_current(session, video, previous.manifest)
            else "DIRTY"
        )
    else:
        video.status = fallback


async def is_selected_generation_fresh(
    session: AsyncSession,
    scene: Scene,
    video: Video,
) -> bool:
    """Selected output is current only when fresh, completed and still available."""
    if not scene.selected_generation_id:
        return False
    generation = await session.get(SceneGeneration, scene.selected_generation_id)
    if (
        generation is None
        or generation.status != "COMPLETED"
        or not generation.output_asset_id
        or not await is_generation_fresh(session, scene, video, generation)
        or not await is_selected_execution_group_fresh(session, scene, video, generation)
    ):
        return False
    asset = await session.get(Asset, generation.output_asset_id)
    return bool(asset and asset.status == "READY" and asset.checksum and asset.deleted_at is None)


async def is_assembly_source_current(
    session: AsyncSession,
    video: Video,
    manifest: dict[str, Any],
) -> bool:
    """Gate promotion only; rendering always consumes the immutable manifest.

    Caller holds the video lock. Lock effective dependencies until promotion commits
    in the same Video -> Product -> Brand order as dependency mutation.
    """
    if manifest.get("video_id") != video.id or manifest.get("video_revision") != video.revision:
        return False
    entries = manifest.get("scenes")
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        return False
    product, brand = await lock_generation_dependencies(session, video)
    brand_id = video.brand_id or (product.brand_id if product else None)
    if (video.product_id and (product is None or product.archived)) or (
        brand_id and (brand is None or brand.archived)
    ):
        return False
    scenes = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video.id, Scene.enabled.is_(True))
                .order_by(Scene.scene_order, Scene.id)
                .with_for_update()
            )
        ).all()
    )
    if [scene.id for scene in scenes] != [entry.get("scene_id") for entry in entries]:
        return False
    for scene, entry in zip(scenes, entries, strict=True):
        if scene.selected_generation_id != entry.get(
            "generation_id"
        ) or scene.scene_order != entry.get("scene_order"):
            return False
        generation = (
            await session.get(
                SceneGeneration,
                scene.selected_generation_id,
                with_for_update=True,
            )
            if scene.selected_generation_id
            else None
        )
        if (
            generation is None
            or generation.status != "COMPLETED"
            or generation.output_asset_id != entry.get("asset_id")
            or not await is_generation_fresh(session, scene, video, generation)
            or not await is_selected_execution_group_fresh(session, scene, video, generation)
        ):
            return False
        asset = await session.get(Asset, generation.output_asset_id, with_for_update=True)
        if (
            asset is None
            or asset.status != "READY"
            or asset.deleted_at is not None
            or not asset.checksum
            or asset.checksum != entry.get("asset_checksum")
        ):
            return False
    return True
