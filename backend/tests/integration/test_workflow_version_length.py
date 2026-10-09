from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.app.db.models import User, WorkflowRecord
from apps.api.app.schemas.api import WorkflowCreate
from apps.api.app.services.workflow_loader import seed_workflows

LONG_VERSION = "director-a8f57b8e23c4-config-template-v2-aggregate-dynamic-bridge-v1"
REGISTRY = Path(__file__).resolve().parents[2] / "workflows" / "h3"


def test_workflow_schema_accepts_real_aggregate_version_and_128_boundary():
    payload = dict(code="test", mode="t2v", workflow={}, slots={})
    assert WorkflowCreate(version=LONG_VERSION, **payload).version == LONG_VERSION
    assert WorkflowCreate(version="v" * 128, **payload).version == "v" * 128
    with pytest.raises(ValidationError):
        WorkflowCreate(version="v" * 129, **payload)


def test_workflow_storage_limit_matches_api():
    assert WorkflowRecord.__table__.c.version.type.length == 128


async def test_seed_real_registry_with_long_aggregate_version(session_factory):
    async with session_factory() as session, session.begin():
        admin = User(email="long-version@test.local", password_hash="test", role="ADMIN")
        session.add(admin)
        await session.flush()
    assert await seed_workflows(session_factory, REGISTRY, admin) > 0
    async with session_factory() as session:
        entry = await session.scalar(
            select(WorkflowRecord).where(
                WorkflowRecord.code == "H3_DIRECTOR_T2V_BASE_AGGREGATE",
                WorkflowRecord.version == LONG_VERSION,
            )
        )
        assert entry is not None
        assert not entry.enabled
    assert await seed_workflows(session_factory, REGISTRY, admin) == 0
