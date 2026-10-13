from datetime import timedelta

import pytest

from apps.api.app.db.models import Asset, IdempotencyKey, SceneGeneration, utcnow
from apps.api.app.schemas.api import GenerationDTO
from apps.api.app.services.idempotency import payload_hash
from tests.integration.test_generation_replay_authorization import (
    api_client,
    login,
    seed_replay_world,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_status"),
    [("COMPLETED", 404), ("RUNNING", 202), ("FAILED", 202)],
)
async def test_replay_requires_completed_normal_generation_output(
    session_factory, tmp_path, status, expected_status
):
    ids = await seed_replay_world(session_factory)
    generation_id = ids["editora_generation"]
    scene_id = ids["editora_scene"]
    key = f"completed-output-{status.lower()}"

    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        assert generation is not None
        output_asset = await session.get(Asset, generation.output_asset_id)
        assert output_asset is not None
        await session.delete(output_asset)
        generation.output_asset_id = None
        assert generation.output_asset_id is None
        generation.status = generation.phase = status
        response = GenerationDTO.model_validate(generation).model_dump(mode="json")
        session.add(
            IdempotencyKey(
                user_id=ids["editora_user"],
                operation=f"create-generation:{scene_id}",
                key=key,
                request_hash=payload_hash({}),
                response=response,
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editora@ownership.test")
        result = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers={"Idempotency-Key": key},
            json={},
        )

    assert result.status_code == expected_status, result.text
    if expected_status == 404:
        assert result.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    else:
        assert result.json()["status"] == status
        assert result.json()["output_asset_id"] is None
