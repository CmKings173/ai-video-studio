"""Deterministic stale identity-map and no-side-effect parent guard checks."""

from copy import deepcopy

import pytest
from sqlalchemy import delete, event, func, inspect, select, update

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Brand, Product, Project, Scene, User, Video
from apps.api.app.services.video_dependency_guard import (
    lock_creation_dependencies,
    lock_video_dependencies,
    read_creation_dependencies,
    read_video_dependencies,
)


async def seed_guard(factory):
    async with factory() as session, session.begin():
        owner = User(email="guard-owner@example.test", password_hash="hash")
        other = User(email="guard-other@example.test", password_hash="hash")
        admin = User(email="guard-admin@example.test", password_hash="hash", role="ADMIN")
        session.add_all([owner, other, admin])
        await session.flush()
        project = Project(name="Project", created_by=owner.id)
        foreign_project = Project(name="Private", created_by=other.id)
        brand = Brand(name="Actual", context={"tone": "original"}, created_by=owner.id)
        override = Brand(name="Override", context={"tone": "override"}, created_by=owner.id)
        foreign_brand = Brand(name="Private", context={"secret": True}, created_by=other.id)
        session.add_all([project, foreign_project, brand, override, foreign_brand])
        await session.flush()
        product = Product(name="Product", brand_id=brand.id, context={}, created_by=owner.id)
        foreign_product = Product(name="Private", brand_id=foreign_brand.id, created_by=other.id)
        session.add_all([product, foreign_product])
        await session.flush()
        video = Video(
            project_id=project.id, product_id=product.id, title="Video", created_by=owner.id
        )
        session.add(video)
        await session.flush()
        return {
            "owner": owner.id,
            "other": other.id,
            "admin": admin.id,
            "project": project.id,
            "foreign_project": foreign_project.id,
            "product": product.id,
            "foreign_product": foreign_product.id,
            "brand": brand.id,
            "override": override.id,
            "foreign_brand": foreign_brand.id,
            "video": video.id,
        }


def creation(ids, **overrides):
    return {
        "project_id": ids["project"],
        "product_id": ids["product"],
        "user_id": ids["owner"],
        **overrides,
    }


async def assert_no_children(session, ids):
    assert await session.scalar(select(func.count()).select_from(Video)) == 1
    assert await session.scalar(select(func.count()).select_from(Scene)) == 0
    assert await session.scalar(select(Video.revision).where(Video.id == ids["video"])) == 1


@pytest.mark.parametrize(
    "model,key", [(Project, "project"), (Product, "product"), (Brand, "brand")]
)
async def test_preloaded_archive_is_refreshed_after_lock(session_factory, model, key):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        preloaded = await session.get(model, ids[key])
        preflight = await read_creation_dependencies(session, **creation(ids))
        assert not preflight.authoritative
        # Persisted-column mutation without synchronizing the loaded ORM row is
        # a deterministic equivalent of another transaction's committed edit.
        await session.execute(
            update(model)
            .where(model.id == ids[key])
            .values(archived=True, revision=2)
            .execution_options(synchronize_session=False)
        )
        ordinary = await session.scalar(select(model).where(model.id == ids[key]).with_for_update())
        assert ordinary is preloaded and ordinary.archived is False  # original faulty mechanism
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids))
        assert (raised.value.status_code, raised.value.code) == (
            409,
            f"{model.__name__.upper()}_NOT_ACTIVE",
        )
        assert preloaded.archived is True
        await assert_no_children(session, ids)


@pytest.mark.parametrize(
    "foreign,expected",
    [
        ("project", "PROJECT_NOT_FOUND"),
        ("product", "PRODUCT_NOT_FOUND"),
        ("brand", "BRAND_NOT_FOUND"),
        ("actual_brand", "BRAND_NOT_FOUND"),
    ],
)
@pytest.mark.parametrize("admin", [False, True])
async def test_both_brands_and_parents_reject_foreign_owners(
    session_factory, foreign, expected, admin
):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        changes = {
            "project": (Video, {"project_id": ids["foreign_project"]}),
            "product": (Video, {"product_id": ids["foreign_product"]}),
            "brand": (Video, {"brand_id": ids["foreign_brand"]}),
            "actual_brand": (Product, {"brand_id": ids["foreign_brand"]}),
        }
        model, values = changes[foreign]
        identity = ids["product"] if model is Product else ids["video"]
        await session.execute(
            update(model)
            .where(model.id == identity)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if foreign == "actual_brand":
            # A valid override must not hide the Product's corrupt actual Brand.
            await session.execute(
                update(Video)
                .where(Video.id == video.id)
                .values(brand_id=ids["override"])
                .execution_options(synchronize_session=False)
            )
        for helper in (read_video_dependencies, lock_video_dependencies):
            with pytest.raises(AppError) as raised:
                await helper(
                    session, video, user_id=ids["admin"] if admin else ids["owner"], is_admin=admin
                )
            assert (raised.value.status_code, raised.value.code) == (404, expected)
            assert "Private" not in raised.value.message and not raised.value.details
        await assert_no_children(session, ids)


async def test_reassignment_uses_authoritative_effective_brand(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        preflight = await read_creation_dependencies(session, **creation(ids))
        assert preflight.effective_brand.id == ids["brand"]
        await session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(brand_id=ids["override"], revision=2)
            .execution_options(synchronize_session=False)
        )
        resolved = await lock_creation_dependencies(session, **creation(ids))
        assert resolved.authoritative and resolved.effective_brand.id == ids["override"]
        assert resolved.product.brand_id == ids["override"]
        assert product.brand_id == ids["override"]


@pytest.mark.parametrize(
    "model,key", [(Project, "project"), (Product, "product"), (Brand, "brand")]
)
async def test_missing_dependency_fails_closed(session_factory, model, key):
    ids = await seed_guard(session_factory)
    values = creation(ids)
    values[f"{key}_id"] = "missing"
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **values)
        assert (raised.value.status_code, raised.value.code) == (
            404,
            f"{model.__name__.upper()}_NOT_FOUND",
        )
        await assert_no_children(session, ids)


async def test_admin_resolves_parent_business_owner_and_creation_pairing(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        resolved = await lock_creation_dependencies(
            session, **creation(ids, user_id=ids["admin"], is_admin=True)
        )
        assert resolved.business_owner_id == ids["owner"]
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids, brand_id=ids["override"]))
        assert (raised.value.status_code, raised.value.code) == (422, "PRODUCT_BRAND_CONFLICT")
        with pytest.raises(AppError) as raised:
            await read_creation_dependencies(session, **creation(ids, user_id=ids["other"]))
        assert raised.value.status_code == 404
        await assert_no_children(session, ids)


async def test_existing_same_owner_override_remains_valid(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        # Preserve legitimate unflushed service input at the locked boundary.
        video.brand_id = ids["override"]
        locked = await lock_video_dependencies(session, video, user_id=ids["owner"])
        assert locked.effective_brand.id == ids["override"]
        assert locked.product.brand_id == ids["brand"]
        assert inspect(video).attrs.brand_id.history.has_changes()
    async with session_factory() as session:
        video = await session.get(Video, ids["video"])
        read = await read_video_dependencies(session, video, user_id=ids["owner"])
        assert read.effective_brand.id == ids["override"] and not read.authoritative


async def test_snapshots_outlive_session_and_do_not_discard_dirty_context(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        brand = await session.get(Brand, ids["brand"])
        brand.context = {"tone": "pending"}
        read = await read_creation_dependencies(session, **creation(ids))
        assert read.effective_brand.context == {"tone": "original"}
        assert brand.context == {"tone": "pending"}
        locked = await lock_creation_dependencies(session, **creation(ids))
        assert locked.effective_brand.context == {"tone": "pending"}
        assert inspect(brand).attrs.context.history.has_changes()
    assert read.project.name == "Project"
    assert read.product.description == ""
    assert locked.effective_brand.context == {"tone": "pending"}
    async with session_factory() as session:
        assert (await session.get(Brand, ids["brand"])).context == {"tone": "pending"}


async def test_conflicting_pending_edit_is_412_without_discard(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        product.context = {"pending": True}
        history = deepcopy(inspect(product).attrs.context.history)
        await session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(context={"concurrent": True}, revision=2)
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids))
        assert (raised.value.status_code, raised.value.code) == (412, "REVISION_CONFLICT")
        assert product.context == {"pending": True}
        assert inspect(product).attrs.context.history == history
        await assert_no_children(session, ids)
        await session.rollback()  # reject the conflicting pending mutation, too


async def test_no_autoflush_even_with_dirty_unrelated_rows(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        session.autoflush = True
        other = await session.get(User, ids["other"])
        other.name = "Unvalidated"
        flushes = []
        event.listen(session.sync_session, "before_flush", lambda *args: flushes.append(True))
        await read_creation_dependencies(session, **creation(ids))
        await lock_creation_dependencies(session, **creation(ids))
        video = await session.get(Video, ids["video"], execution_options={"autoflush": False})
        await read_video_dependencies(session, video, user_id=ids["owner"])
        await lock_video_dependencies(session, video, user_id=ids["owner"])
        assert flushes == [] and other in session.dirty
        await session.rollback()


async def test_read_archive_policy_does_not_weaken_owner_validation(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        await session.execute(
            update(Project).where(Project.id == ids["project"]).values(archived=True)
        )
        result = await read_video_dependencies(
            session, video, user_id=ids["owner"], require_active=False
        )
        assert result.project.archived
        for helper in (read_video_dependencies, lock_video_dependencies):
            with pytest.raises(AppError) as raised:
                await helper(session, video, user_id=ids["owner"])
            assert raised.value.status_code == 409


async def test_brand_lock_order_and_no_blocking_graph_for_existing_video(
    session_factory, monkeypatch
):
    import apps.api.app.services.video_dependency_guard as guard

    ids = await seed_guard(session_factory)
    calls = []
    original = guard.lock_revisioned_row

    async def observe(session, model, identity, **kwargs):
        calls.append((model, identity))
        return await original(session, model, identity, **kwargs)

    async def forbidden_graph(session):
        pytest.fail("Existing Video must not wait for graph after Video")

    monkeypatch.setattr(guard, "lock_revisioned_row", observe)
    monkeypatch.setattr(guard, "lock_dependency_graph", forbidden_graph)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, ids["video"])
        video.brand_id = ids["override"]
        await lock_video_dependencies(session, video, user_id=ids["owner"])
    assert calls == [
        (Video, ids["video"]),
        (Project, ids["project"]),
        (Product, ids["product"]),
    ] + [(Brand, identity) for identity in sorted((ids["brand"], ids["override"]))]


async def test_product_removed_after_preflight_fails_closed(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        await read_creation_dependencies(session, **creation(ids))
        await session.execute(
            update(Video)
            .where(Video.id == ids["video"])
            .values(product_id=None)
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            delete(Product)
            .where(Product.id == product.id)
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids))
        assert (raised.value.status_code, raised.value.code) == (404, "PRODUCT_NOT_FOUND")
        await assert_no_children(session, ids)


async def test_reassignment_to_archived_brand_after_preflight_rejects(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        product = await session.get(Product, ids["product"])
        await read_creation_dependencies(session, **creation(ids))
        await session.execute(
            update(Brand).where(Brand.id == ids["override"]).values(archived=True)
        )
        await session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(brand_id=ids["override"], revision=2)
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(AppError) as raised:
            await lock_creation_dependencies(session, **creation(ids))
        assert (raised.value.status_code, raised.value.code) == (409, "BRAND_NOT_ACTIVE")
        assert product.brand_id == ids["override"]
        await assert_no_children(session, ids)


async def test_read_uses_fresh_projection_for_preloaded_context(session_factory):
    ids = await seed_guard(session_factory)
    async with session_factory() as session, session.begin():
        brand = await session.get(Brand, ids["brand"])
        await session.execute(
            update(Brand)
            .where(Brand.id == brand.id)
            .values(context={"tone": "changed"}, revision=2)
            .execution_options(synchronize_session=False)
        )
        read = await read_creation_dependencies(session, **creation(ids))
        assert read.effective_brand.context == {"tone": "changed"}
        assert brand.context == {"tone": "original"}
        # No ORM state in returned dependencies; a consumer can mutate its
        # private context copy without altering any loaded or persisted row.
        read.effective_brand.context["tone"] = "consumer copy"
        assert brand.context == {"tone": "original"}
