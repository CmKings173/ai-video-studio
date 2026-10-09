from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import WorkflowRecord
from tests.integration.test_generation_preparation import seed
from workers.director_dispatcher import DirectorDispatcher
from workers.dispatcher import Dispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_class", [Dispatcher, DirectorDispatcher])
@pytest.mark.parametrize("persisted_prompt", [True, False])
@pytest.mark.parametrize("node_class", ["MiniMaxH3ImageToVideo", "ArbitraryCustomH3"])
async def test_retired_recovery_holds_before_external_io(
    session_factory, worker_class, persisted_prompt, node_class
):
    await seed(session_factory)
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        record.workflow = {"1": {"class_type": node_class, "inputs": {}}}
        record.enabled = True
        workflow_id = record.id

    worker = object.__new__(worker_class)
    worker.factory = session_factory
    held = []

    async def hold(*args):
        held.append(args)

    class NoExternalIO:
        def __getattr__(self, name):
            raise AssertionError(f"retired recovery contacted ComfyUI: {name}")

    worker._hold = hold
    worker.adapter = NoExternalIO()
    generation = SimpleNamespace(
        id="historical-job",
        comfy_prompt_id="original-prompt" if persisted_prompt else None,
        input_snapshot={"workflow_id": workflow_id},
    )
    attempt = SimpleNamespace(status="QUEUED", client_id="original-client")
    assert await worker._submit_or_recover(generation, attempt) is None
    assert held[0][:2] == ("historical-job", "WORKFLOW_RETIRED")
    assert generation.comfy_prompt_id == ("original-prompt" if persisted_prompt else None)
    assert attempt.client_id == "original-client"


@pytest.mark.asyncio
async def test_aggregate_collection_revalidates_before_claim_or_storage_io(session_factory):
    await seed(session_factory)
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        record.workflow = {"1": {"class_type": "ArbitraryCustomH3", "inputs": {}}}
        record.enabled = True
        workflow_id = record.id

    worker = object.__new__(DirectorDispatcher)
    worker.factory = session_factory

    async def update(*args, **kwargs):
        raise AssertionError("revoked aggregate reached collection state or storage")

    worker._update = update
    run = SimpleNamespace(id="retired-aggregate", input_snapshot={"workflow_id": workflow_id})
    with pytest.raises(AppError) as error:
        await worker._collect(run, {})
    assert error.value.code == "WORKFLOW_RETIRED"
