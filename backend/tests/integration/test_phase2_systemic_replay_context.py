"""Replay real producer snapshots without treating business JSON as foreign keys.

Reference schema: snapshot/assets[].id, generation_intent/assets[].id and
director_execution/assets[].id; lineage.parent_generation_id and
{snapshot,generation_intent,director_execution}.prompt_provenance.inherited_from;
output_metadata.artifacts[].asset_id. Dependency context, workflow node inputs,
generation configuration and asset media_metadata are arbitrary JSON.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from apps.api.app.db.models import Brand, Product, SceneGeneration, Video
from tests.integration.test_batch_completion_replay import batch_client
from tests.integration.test_generation_replay_authorization import (
    replay_database_state,
    row_counts,
)

CONTEXT = {
    "assets": ["logo", "hero-image"],
    "asset_id": "marketing-reference",
    "reference_asset_ids": ["creative-a", "creative-b"],
    "parent_generation_id": "editorial-guideline",
    "inherited_from": "brand-template",
    "nested": [{"assets": {"asset_id": 42}, "inherited_from": ["free-form"]}],
}


@pytest.mark.parametrize("route", ["generations", "generate-all", "variations", "regenerations"])
async def test_real_snapshot_context_survives_http_replay(session_factory, tmp_path, route):
    async with batch_client(session_factory, tmp_path) as (client, video_id, batch, _):
        scene_id = batch["scene_ids"][0]
        async with session_factory() as session, session.begin():
            video = await session.get(Video, video_id)
            brand = Brand(name="Brand", context=deepcopy(CONTEXT), created_by=video.created_by)
            session.add(brand)
            await session.flush()
            product = Product(name="Product", brand_id=brand.id, context=deepcopy(CONTEXT),
                              created_by=video.created_by)
            session.add(product)
            await session.flush()
            video.product_id = product.id
        payload = {}
        if route in {"variations", "regenerations"}:
            original = await client.post(f"/api/v1/scenes/{scene_id}/generations", json={})
            assert original.status_code == 202, original.text
            payload = {"parent_generation_id": original.json()["id"]}
            async with session_factory() as session, session.begin():
                parent = await session.get(SceneGeneration, original.json()["id"])
                parent.status = "COMPLETED"
                parent.phase = "COMPLETED"
        if route == "generate-all":
            preview = await client.post(
                f"/api/v1/videos/{video_id}/generate-all/preview", json=payload
            )
            assert preview.status_code == 200, preview.text
            payload["settings"] = {
                "preview_configuration_hash": preview.json()["preview_configuration_hash"]
            }
        key = f"context-{route}"
        client.headers["Idempotency-Key"] = key
        url = (f"/api/v1/videos/{video_id}/generate-all" if route == "generate-all"
               else f"/api/v1/scenes/{scene_id}/{route}")
        first = await client.post(url, json=payload)
        assert first.status_code == 202, first.text
        entries = first.json()["generations"] if route == "generate-all" else [first.json()]
        for entry in entries:
            dependencies = entry["input_snapshot"]["generation_freshness"]["inputs"]["dependencies"]
            assert dependencies["product"]["context"] == CONTEXT
            assert dependencies["brand"]["context"] == CONTEXT
        # Advance canonical state: replay must not return the original CREATED cache.
        async with session_factory() as session, session.begin():
            generations = list((await session.scalars(select(SceneGeneration).where(
                SceneGeneration.id.in_([entry["id"] for entry in entries])
            ))).all())
            for generation in generations:
                generation.status = "RUNNING"
                generation.phase = "RUNNING"
                generation.output_metadata = {"annotation": {"asset_id": "business-note"}}
        counts = await row_counts(session_factory)
        state = await replay_database_state(session_factory, key)
        replay = await client.post(url, json=payload)
        assert replay.status_code == 202, replay.text
        replay_entries = (
            replay.json()["generations"] if route == "generate-all" else [replay.json()]
        )
        assert [entry["id"] for entry in replay_entries] == [entry["id"] for entry in entries]
        for entry in replay_entries:
            assert entry["status"] == "RUNNING"
            assert entry["output_metadata"]["annotation"] == {"asset_id": "business-note"}
            dependencies = entry["input_snapshot"]["generation_freshness"]["inputs"]["dependencies"]
            assert dependencies["product"]["context"] == CONTEXT
            assert dependencies["brand"]["context"] == CONTEXT
        assert await row_counts(session_factory) == counts
        assert await replay_database_state(session_factory, key) == state
