"""Downstream writes validate active, authoritative parents independently of routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from apps.api.app.api import assembly, generations, products
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Brand,
    DirectorRun,
    FinalVideo,
    IdempotencyKey,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
)
from apps.api.app.schemas.api import (
    AssemblyRequest,
    GenerateAll,
    GenerationRequest,
    ProductCreate,
    ProductPatch,
)
from apps.api.app.services import generation_dependency_invalidation
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.director_run_service import create_director_run
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_director_dynamic_aggregate import prepared_members, qualify_run
from tests.integration.test_generation_preparation import seed
from tests.integration.test_video_scene_ownership import seed_ownership_graph


async def _active_graph(factory):
    user_id, scene_id = await seed(factory)
    async with factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        brand = Brand(name="Product brand", created_by=user_id)
        override = Brand(name="Explicit override", created_by=user_id)
        session.add_all([brand, override])
        await session.flush()
        product = Product(name="Product", brand_id=brand.id, created_by=user_id)
        session.add(product)
        await session.flush()
        video.product_id = product.id
        return dict(user=user_id, scene=scene_id, video=video.id,
                    project=video.project_id, product=product.id,
                    brand=brand.id, override=override.id)


async def _counts(session):
    return tuple(
        [await session.scalar(select(func.count()).select_from(model))
         for model in (SceneGeneration, FinalVideo, DirectorRun, IdempotencyKey)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,model", [("project", Project), ("product", Product), ("brand", Brand)]
)
async def test_archived_dependencies_reject_preview_and_assembly_preflight(
    session_factory, tmp_path, kind, model
):
    ids = await _active_graph(session_factory)
    async with session_factory() as session, session.begin():
        (await session.get(model, ids[kind])).archived = True
    user = SimpleNamespace(id=ids["user"], role="EDITOR")
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    async with session_factory() as session:
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await generations.preview_prompt(
                ids["scene"], payload=None, user=user, session=session, settings=settings
            )
        assert (error.value.status_code, error.value.code) == (409, f"{kind.upper()}_NOT_ACTIVE")
        with pytest.raises(AppError) as error:
            await AssemblyService().authorize_inputs(
                session, video_id=ids["video"], request=AssemblyRequest(), user_id=ids["user"]
            )
        assert (error.value.status_code, error.value.code) == (409, f"{kind.upper()}_NOT_ACTIVE")
        assert await _counts(session) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generation", "assembly"])
async def test_preloaded_project_archive_is_authoritative_at_direct_write(
    session_factory, tmp_path, operation
):
    ids = await _active_graph(session_factory)
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    async with session_factory() as session, session.begin():
        project = await session.get(Project, ids["project"])
        await session.execute(
            update(Project).where(Project.id == project.id).values(archived=True)
            .execution_options(synchronize_session=False)
        )
        assert not project.archived
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            if operation == "generation":
                await GenerationService(settings).create(
                    session, scene_id=ids["scene"], request=GenerationRequest(seed=42),
                    user_id=ids["user"], request_id=None,
                )
            else:
                await AssemblyService().create(
                    session, video_id=ids["video"], request=AssemblyRequest(),
                    expected_revision=1, user_id=ids["user"], request_id=None,
                )
        assert (error.value.status_code, error.value.code) == (409, "PROJECT_NOT_ACTIVE")
        assert await _counts(session) == before
        video = await session.get(Video, ids["video"])
        assert (video.revision, video.status) == (1, "DRAFT")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch"])
async def test_admin_cannot_mix_product_and_brand_owners(session_factory, operation):
    world = await seed_ownership_graph(session_factory)
    admin = SimpleNamespace(id=world["users"]["ADMIN"], role="ADMIN")
    async with session_factory() as session, session.begin():
        before = await session.scalar(select(func.count()).select_from(Product))
        with pytest.raises(AppError) as error:
            if operation == "create":
                await products.create_product(
                    ProductCreate(name="Invalid", brand_id=world["brands"]["B"]),
                    user=admin, session=session,
                )
            else:
                await products.patch_product(
                    world["products"]["A"], ProductPatch(brand_id=world["brands"]["B"]),
                    revision=1, user=admin, session=session,
                )
        assert (error.value.status_code, error.value.code) == (404, "BRAND_NOT_FOUND")
        assert await session.scalar(select(func.count()).select_from(Product)) == before
        product = await session.get(Product, world["products"]["A"])
        assert (product.brand_id, product.revision) == (world["brands"]["A"], 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch"])
async def test_admin_can_use_brand_matching_product_owner(session_factory, operation):
    world = await seed_ownership_graph(session_factory)
    admin = SimpleNamespace(id=world["users"]["ADMIN"], role="ADMIN")
    async with session_factory() as session, session.begin():
        if operation == "create":
            brand = Brand(name="Admin brand", created_by=admin.id)
            session.add(brand)
            await session.flush()
            dto = await products.create_product(
                ProductCreate(name="Admin product", brand_id=brand.id), user=admin, session=session
            )
            assert (await session.get(Product, dto.id)).created_by == admin.id
        else:
            dto = await products.patch_product(
                world["products"]["A"], ProductPatch(brand_id=world["brands"]["A"]),
                revision=1, user=admin, session=session,
            )
            assert dto.revision == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "patch"])
async def test_product_brand_archive_after_preflight_is_refreshed(
    session_factory, monkeypatch, operation
):
    world = await seed_ownership_graph(session_factory)
    user = SimpleNamespace(id=world["users"]["EDITOR_A"], role="EDITOR")
    async with session_factory() as session, session.begin():
        brand = await session.get(Brand, world["brands"]["A"])
        before = await session.scalar(select(func.count()).select_from(Product))

        async def archive_at_graph_boundary(locked_session):
            await locked_session.execute(
                update(Brand).where(Brand.id == brand.id).values(archived=True)
                .execution_options(synchronize_session=False)
            )
            assert not brand.archived

        monkeypatch.setattr(
            products if operation == "create" else generation_dependency_invalidation,
            "lock_dependency_graph", archive_at_graph_boundary,
        )
        with pytest.raises(AppError) as error:
            if operation == "create":
                await products.create_product(
                    ProductCreate(name="Archived dependency", brand_id=brand.id),
                    user=user, session=session,
                )
            else:
                await products.patch_product(
                    world["products"]["A"], ProductPatch(brand_id=brand.id),
                    revision=1, user=user, session=session,
                )
        assert (error.value.status_code, error.value.code) == (409, "BRAND_NOT_ACTIVE")
        assert await session.scalar(select(func.count()).select_from(Product)) == before
        product = await session.get(Product, world["products"]["A"])
        assert (product.brand_id, product.revision) == (brand.id, 1)


@pytest.mark.asyncio
async def test_existing_same_owner_brand_override_remains_generatable(session_factory, tmp_path):
    ids = await _active_graph(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        video.brand_id = ids["override"]
        await session.flush()
        generation = await GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        ).create(session, scene_id=ids["scene"], request=GenerationRequest(seed=42),
                 user_id=ids["user"], request_id=None)
        assert generation.status == "CREATED"


@pytest.mark.asyncio
async def test_pending_product_brand_cannot_hide_foreign_owner_behind_override(
    session_factory, tmp_path
):
    ids = await _active_graph(session_factory)
    async with session_factory() as session, session.begin():
        foreign = User(email=f"brand-{uuid4()}@example.test", password_hash="unused")
        session.add(foreign)
        await session.flush()
        brand = Brand(name="Foreign Product brand", created_by=foreign.id)
        session.add(brand)
        video = await session.get(Video, ids["video"])
        video.brand_id = ids["override"]
        await session.flush()
        foreign_brand_id = brand.id
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        product.brand_id = foreign_brand_id
        # Persisted SQL ownership still accepts this graph. The direct guard
        # must validate pending Product intent and both Brands before any DML.
        with pytest.raises(AppError) as error:
            await GenerationService(
                Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
            ).create(session, scene_id=ids["scene"], request=GenerationRequest(seed=42),
                     user_id=ids["user"], request_id=None)
        assert (error.value.status_code, error.value.code) == (404, "BRAND_NOT_FOUND")
        assert product.brand_id == foreign_brand_id
        assert await session.scalar(
            select(Product.brand_id).where(Product.id == product.id)
        ) == ids["brand"]
        assert await _counts(session) == (0, 0, 0, 0)
        await session.rollback()


@pytest.mark.asyncio
async def test_direct_director_entry_rejects_inconsistent_video_before_persistence(
    session_factory, tmp_path, monkeypatch
):
    user_id, video_id, scene_ids, generations = await prepared_members(
        session_factory, tmp_path, 1, persist=False
    )
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_ids[0])
        generation = generations[0]
        await qualify_run(session, [generation], [scene], video, user_id, monkeypatch)
        foreign = User(email=f"director-{uuid4()}@example.test", password_hash="unused")
        session.add(foreign)
        await session.flush()
        project = Project(name="Foreign", created_by=foreign.id)
        session.add(project)
        await session.flush()
        video.project_id = project.id
        await session.flush()
        before = await _counts(session)
        with pytest.raises(AppError) as error:
            await create_director_run(
                session, [generation], [scene], video=video,
                user_id=user_id, is_admin=True,
            )
        assert error.value.status_code == 404
        assert await _counts(session) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generation", "batch", "assembly"])
async def test_archived_dependencies_reject_before_generation_idempotency(
    session_factory, monkeypatch, operation
):
    ids = await _active_graph(session_factory)
    async with session_factory() as session, session.begin():
        (await session.get(Project, ids["project"])).archived = True
    claim = AsyncMock(side_effect=AssertionError("Denied requests must not claim idempotency"))
    monkeypatch.setattr(generations, "claim", claim)
    monkeypatch.setattr(assembly, "claim", claim)
    async with session_factory() as session:
        user = SimpleNamespace(id=ids["user"], role="EDITOR")
        request = SimpleNamespace(state=SimpleNamespace(request_id=None))
        with pytest.raises(AppError) as error:
            if operation == "generation":
                await generations._create(
                    scene_id=ids["scene"], payload=GenerationRequest(),
                    request=request, key="denied",
                    user=user, session=session, settings=Settings(_env_file=None),
                    operation_name="generate",
                )
            elif operation == "batch":
                await generations.generate_all(
                    ids["video"], GenerateAll(), request, key="denied", user=user,
                    session=session, settings=Settings(_env_file=None),
                )
            else:
                await assembly.assemble_video(
                    ids["video"], AssemblyRequest(), request, revision=1, key="denied", user=user,
                    session=session, settings=Settings(_env_file=None),
                )
        assert (error.value.status_code, error.value.code) == (409, "PROJECT_NOT_ACTIVE")
        claim.assert_not_awaited()
