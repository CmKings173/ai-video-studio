"""Group 3 HTTP contracts: real routers/renderers, isolated SQLite, injected auth.

The application lifespan is never started; no storage or production DB is used.
"""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from apps.api.app.api import admin, assets, brands, generations, products, projects, videos
from apps.api.app.api.deps import require_admin, require_csrf, require_editor
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    Brand,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.db.session import get_session
from apps.api.app.main import app_error_handler, validation_error_handler
from apps.api.app.schemas.api import ErrorEnvelope

PREFIX = "/api/v1"
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def identity(label):
    return str(uuid5(NAMESPACE_URL, f"group3-contract/{label}"))


LISTS = (
    ("/projects", "projects"),
    ("/products", "products"),
    ("/videos", "videos"),
    ("/assets", "assets"),
    ("/brands", "brands"),
    (f"/projects/{identity('project-0')}/videos", "target_videos"),
    (f"/projects/{identity('project-0')}/assets", "project_assets"),
    (f"/products/{identity('product-0')}/assets", "product_assets"),
    (f"/scenes/{identity('scene')}/generations", "generations"),
    ("/admin/users", "users"),
    ("/admin/workflows", "workflows"),
)


@pytest_asyncio.fixture
async def wire(session_factory):
    """Stable creation-time ties exercise ID ordering; decoys expose scope leaks."""
    expected = {}
    async with session_factory() as session, session.begin():
        users = [
            User(
                id=identity(f"user-{i}"),
                email=f"group3-{i}@example.test",
                password_hash="unused-test-hash",
                role="ADMIN" if i == 0 else "EDITOR",
                name=f"User {i}",
                created_at=STAMP,
            )
            for i in range(21)
        ]
        session.add_all(users)
        await session.flush()
        owner = users[0]
        expected["users"] = {row.id for row in users}
        for model, key, label in (
            (Project, "projects", "project"),
            (Brand, "brands", "brand"),
            (Product, "products", "product"),
        ):
            rows = [
                model(
                    id=identity(f"{label}-{i}"),
                    name=f"{'Match' if i < 21 else 'Other'} {label} {i}",
                    created_by=owner.id,
                    created_at=STAMP,
                    revision=4,
                    archived=i >= 21,
                    **(
                        {"brand_id": identity(f"brand-{0 if i < 21 else 21}")}
                        if model is Product
                        else {}
                    ),
                )
                for i in range(24)
            ]
            session.add_all(rows)
            await session.flush()
            expected[key] = {row.id for row in rows}
            expected[f"matching_{key}"] = {row.id for row in rows[:21]}
        rows = [
            Video(
                id=identity(f"video-{i}"),
                title=f"{'Match' if i < 21 else 'Other'} video {i}",
                project_id=identity(f"project-{0 if i < 21 else 21}"),
                product_id=identity(f"product-{0 if i < 21 else 21}"),
                status="DRAFT" if i < 21 else "FAILED",
                created_by=owner.id,
                created_at=STAMP,
            )
            for i in range(24)
        ]
        session.add_all(rows)
        await session.flush()
        expected["videos"] = {row.id for row in rows}
        expected["target_videos"] = {row.id for row in rows[:21]}
        for scope, parent in (("project", "project"), ("product", "product"), ("other", "project")):
            rows = [
                Asset(
                    id=identity(f"asset-{scope}-{i}"),
                    **{f"{parent}_id": identity(f"{parent}-{21 if scope == 'other' else 0}")},
                    filename=f"{scope}-{i}.png",
                    object_key=f"group3/{scope}/{i}",
                    kind="VIDEO" if scope == "other" else "IMAGE",
                    content_type="video/mp4" if scope == "other" else "image/png",
                    status="FAILED" if scope == "other" else "READY",
                    created_by=owner.id,
                    created_at=STAMP,
                    deleted_at=STAMP if i == 21 else None,
                )
                for i in range(4 if scope == "other" else 22)
            ]
            session.add_all(rows)
            expected[f"{scope}_assets"] = {row.id for row in rows if row.deleted_at is None}
        await session.flush()
        expected["assets"] = set().union(
            *(expected[f"{s}_assets"] for s in ("project", "product", "other"))
        )
        workflows = [
            WorkflowRecord(
                id=identity(f"workflow-{i}"),
                code=f"group3-{i}",
                version="1",
                mode="t2v",
                workflow={},
                slots={},
                workflow_hash="a" * 64,
                slot_map_hash="b" * 64,
                enabled=False,
                created_by=owner.id,
                created_at=STAMP,
            )
            for i in range(21)
        ]
        session.add_all(workflows)
        await session.flush()
        expected["workflows"] = {row.id for row in workflows}
        session.add_all(
            [
                Scene(id=identity("scene"), video_id=identity("video-0"), scene_order=0),
                Scene(id=identity("other-scene"), video_id=identity("video-21"), scene_order=0),
            ]
        )
        await session.flush()
        rows = [
            SceneGeneration(
                id=identity(f"generation-{i}"),
                scene_id=identity("scene" if i < 21 else "other-scene"),
                video_id=identity(f"video-{0 if i < 21 else 21}"),
                generation_no=i + 1,
                mode="t2v",
                workflow_id=workflows[0].id,
                status="COMPLETED" if i < 21 else "FAILED",
                created_by=owner.id,
                created_at=STAMP,
            )
            for i in range(24)
        ]
        session.add_all(rows)
        expected["generations"] = {row.id for row in rows[:21]}

    app = FastAPI()
    for module in (projects, products, videos, assets, brands, generations, admin):
        app.include_router(module.router, prefix=PREFIX)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    async def isolated_session():
        async with session_factory() as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = isolated_session
    for dependency in (require_editor, require_admin, require_csrf):
        app.dependency_overrides[dependency] = lambda: owner
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://group3.test"
    ) as client:
        yield client, expected


async def get_page(client, path, params, *, page, size, total):
    response = await client.get(PREFIX + path, params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"items", "total", "page", "page_size"}
    assert (data["page"], data["page_size"], data["total"]) == (page, size, total)
    assert len(data["items"]) == min(size, max(0, total - (page - 1) * size))
    ids = [row["id"] for row in data["items"]]
    assert len(ids) == len(set(ids))
    return data


@pytest.mark.parametrize(("path", "key"), LISTS)
async def test_all_wrapper_endpoints_pagination_wire_contract(wire, path, key):
    client, expected = wire
    total = len(expected[key])
    default = await get_page(client, path, {}, page=1, size=20, total=total)
    legacy = await get_page(client, path, {"size": 5}, page=1, size=20, total=total)
    assert legacy == default  # Unknown size intentionally leaves the default unchanged.
    first = await get_page(client, path, {"page": 1, "page_size": 20}, page=1, size=20, total=total)
    second = await get_page(
        client, path, {"page": 2, "page_size": 20}, page=2, size=20, total=total
    )
    assert first == default
    first_ids = {row["id"] for row in first["items"]}
    second_ids = {row["id"] for row in second["items"]}
    assert first_ids.isdisjoint(second_ids)
    # The global assets list has 46 rows, so include its third default page.
    third = await get_page(client, path, {"page": 3, "page_size": 20}, page=3, size=20, total=total)
    third_ids = {row["id"] for row in third["items"]}
    assert (first_ids | second_ids).isdisjoint(third_ids)
    assert first_ids | second_ids | third_ids == expected[key]
    await get_page(client, path, {"page": 4, "page_size": 20}, page=4, size=20, total=total)
    small = await get_page(client, path, {"page": 2, "page_size": 5}, page=2, size=5, total=total)
    maximum = await get_page(client, path, {"page_size": 100}, page=1, size=100, total=total)
    assert {row["id"] for row in maximum["items"]} == expected[key]
    assert small["items"] == maximum["items"][5:10]
    assert first["items"] + second["items"] + third["items"] == maximum["items"]
    if key == "generations":
        assert [row["generation_no"] for row in maximum["items"]] == list(range(21, 0, -1))


@pytest.mark.parametrize(("path", "key"), LISTS)
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page_size": 101}, "page_size"),
        ({"page": 0}, "page"),
        ({"page_size": 0}, "page_size"),
        ({"page": "invalid"}, "page"),
    ],
)
async def test_all_wrapper_endpoints_reject_invalid_pagination(wire, path, key, params, field):
    client, _ = wire
    response = await client.get(PREFIX + path, params=params)
    assert response.status_code == 422, response.text
    error = ErrorEnvelope.model_validate(response.json()).error
    assert error.code == "REQUEST_VALIDATION_FAILED"
    assert any(item["location"] == ["query", field] for item in error.details["errors"])


FILTERS = (
    ("/projects", {"search": "Match", "archived": "false"}, "matching_projects"),
    (
        "/products",
        {"search": "Match", "archived": "false", "brand_id": identity("brand-0")},
        "matching_products",
    ),
    ("/brands", {"search": "Match", "archived": "false"}, "matching_brands"),
    ("/videos", {"search": "Match", "status": "DRAFT", "kind": "QUICK_CLIP"}, "target_videos"),
    ("/videos", {"project_id": identity("project-0")}, "target_videos"),
    ("/videos", {"product_id": identity("product-0")}, "target_videos"),
    (
        "/videos",
        {"project_id": identity("project-0"), "product_id": identity("product-0")},
        "target_videos",
    ),
    (
        "/assets",
        {"project_id": identity("project-0"), "kind": "IMAGE", "status": "READY"},
        "project_assets",
    ),
    (
        "/assets",
        {"product_id": identity("product-0"), "kind": "IMAGE", "status": "READY"},
        "product_assets",
    ),
    # Assets have a single business scope: both parents select a UNION.
    (
        "/assets",
        {"project_id": identity("project-0"), "product_id": identity("product-0")},
        "combined_assets",
    ),
    *((path, {}, key) for path, key in LISTS[5:9]),
)


@pytest.mark.parametrize(("path", "params", "key"), FILTERS)
async def test_filtered_and_related_pages_preserve_scope_and_totals(wire, path, params, key):
    client, expected = wire
    expected["combined_assets"] = expected["project_assets"] | expected["product_assets"]
    total = len(expected[key])
    seen = set()
    for page in range(1, (total + 4) // 5 + 2):
        data = await get_page(
            client,
            path,
            {**params, "page": page, "page_size": 5},
            page=page,
            size=5,
            total=total,
        )
        ids = {row["id"] for row in data["items"]}
        assert seen.isdisjoint(ids)
        seen.update(ids)
    assert seen == expected[key]


@pytest.mark.parametrize("resource", ["projects", "products"])
@pytest.mark.parametrize("operation", ["edit", "archive"])
async def test_revision_conflict_preserves_row_then_current_revision_retries_once(
    wire, session_factory, resource, operation
):
    client, _ = wire
    model = Project if resource == "projects" else Product
    resource_id = identity("project-0" if resource == "projects" else "product-0")
    path = f"{PREFIX}/{resource}/{resource_id}"
    loaded = await client.get(path)
    assert loaded.status_code == 200
    before = loaded.json()
    assert before["revision"] == 4
    assert before["archived"] is False
    mutation_path = path + ("/archive" if operation == "archive" else "")
    method = "POST" if operation == "archive" else "PATCH"
    payload = (
        {}
        if operation == "archive"
        else {"name": "Retried editor name", "description": "Saved once"}
    )
    rejected = await client.request(
        method, mutation_path, headers={"If-Match": '"3"'}, json=payload
    )
    assert rejected.status_code == 412, rejected.text
    error = ErrorEnvelope.model_validate(rejected.json()).error
    assert error.code == "REVISION_CONFLICT"
    assert error.message == "Resource changed since it was loaded"
    assert error.details == {"expected": 3, "actual": 4}
    assert (await client.get(path)).json() == before
    async with session_factory() as session:
        row = await session.get(model, resource_id)
        assert (row.name, row.description, row.archived, row.revision) == (
            before["name"],
            before["description"],
            False,
            4,
        )
    # Explicit reload provides the revision for exactly one HTTP retry.
    reloaded = await client.get(path)
    assert reloaded.status_code == 200
    current = reloaded.json()["revision"]
    success = await client.request(
        method, mutation_path, headers={"If-Match": str(current)}, json=payload
    )
    assert success.status_code == 200, success.text
    saved = success.json()
    assert saved["revision"] == 5
    assert saved["archived"] is (operation == "archive")
    assert saved["name"] == (payload["name"] if operation == "edit" else before["name"])
    assert saved["description"] == (
        payload["description"] if operation == "edit" else before["description"]
    )
    assert (await client.get(path)).json() == saved
    async with session_factory() as session:
        row = await session.get(model, resource_id)
        assert (row.name, row.description, row.archived, row.revision) == (
            saved["name"],
            saved["description"],
            saved["archived"],
            5,
        )


# Independent inputs prevent one effective filter from masking another broken filter.
INDEPENDENT_FILTERS = (
    *(
        (f"/{resource}", {"search": "Match"}, f"matching_{resource}")
        for resource in ("projects", "products", "brands")
    ),
    *(
        (f"/{resource}", {"archived": "false"}, f"matching_{resource}")
        for resource in ("projects", "products", "brands")
    ),
    ("/products", {"brand_id": identity("brand-0")}, "matching_products"),
    ("/videos", {"search": "Match"}, "target_videos"),
    ("/videos", {"status": "DRAFT"}, "target_videos"),
    ("/assets", {"kind": "IMAGE"}, "combined_assets"),
    ("/assets", {"status": "READY"}, "combined_assets"),
)


@pytest.mark.parametrize(("path", "params", "key"), INDEPENDENT_FILTERS)
async def test_each_filter_independently_controls_wire_results(wire, path, params, key):
    await test_filtered_and_related_pages_preserve_scope_and_totals(wire, path, params, key)


@pytest.mark.parametrize(
    ("target", "other", "key", "other_ids"),
    [
        (
            LISTS[5][0],
            f"/projects/{identity('project-21')}/videos",
            "target_videos",
            {identity(f"video-{i}") for i in range(21, 24)},
        ),
        (
            LISTS[6][0],
            f"/projects/{identity('project-21')}/assets",
            "project_assets",
            {identity(f"asset-other-{i}") for i in range(4)},
        ),
        (LISTS[7][0], f"/products/{identity('product-21')}/assets", "product_assets", set()),
        (
            LISTS[8][0],
            f"/scenes/{identity('other-scene')}/generations",
            "generations",
            {identity(f"generation-{i}") for i in range(21, 24)},
        ),
    ],
)
async def test_parent_switch_returns_only_new_scope(wire, target, other, key, other_ids):
    client, expected = wire
    old_page = await get_page(
        client, target, {"page": 2}, page=2, size=20, total=len(expected[key])
    )
    new_page = await get_page(client, other, {}, page=1, size=20, total=len(other_ids))
    assert {row["id"] for row in new_page["items"]} == other_ids
    assert {row["id"] for row in old_page["items"]}.isdisjoint(other_ids)
    await get_page(client, other, {"page": 2}, page=2, size=20, total=len(other_ids))
    back = await get_page(client, target, {"page": 2}, page=2, size=20, total=len(expected[key]))
    assert back == old_page


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (f"/projects/{identity('missing')}/videos", "PROJECT_NOT_FOUND"),
        (f"/projects/{identity('missing')}/assets", "PROJECT_NOT_FOUND"),
        (f"/products/{identity('missing')}/assets", "PRODUCT_NOT_FOUND"),
        (f"/scenes/{identity('missing')}/generations", "SCENE_NOT_FOUND"),
    ],
)
async def test_missing_parent_is_typed_error_not_successful_empty(wire, path, code):
    client, _ = wire
    response = await client.get(PREFIX + path, params={"page": 2, "page_size": 5})
    assert response.status_code == 404, response.text
    assert ErrorEnvelope.model_validate(response.json()).error.code == code
    assert "items" not in response.json()


async def test_video_scopes_intersect_while_asset_scopes_union(wire):
    client, expected = wire
    await get_page(
        client,
        "/videos",
        {"project_id": identity("project-0"), "product_id": identity("product-21")},
        page=1,
        size=20,
        total=0,
    )
    data = await get_page(
        client,
        "/assets",
        {
            "project_id": identity("project-0"),
            "product_id": identity("product-0"),
            "page_size": 100,
        },
        page=1,
        size=100,
        total=42,
    )
    assert {row["id"] for row in data["items"]} == (
        expected["project_assets"] | expected["product_assets"]
    )
