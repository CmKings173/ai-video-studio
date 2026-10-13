from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.db.models import Asset, Brand, Product, Project, Video
from tests.integration.test_authz import api_client, seed_user

PASSWORD = "ownership-fixture-password"


@pytest.fixture(autouse=True)
def clean_login_throttle():
    login_throttle.clear()
    yield
    login_throttle.clear()


async def seed_identities_and_resources(session_factory):
    identities = {}
    for role, label in (("EDITOR", "a"), ("EDITOR", "b"), ("ADMIN", "admin")):
        email = f"ownership-{label}@example.test"
        identities[label] = {
            "id": await seed_user(
                session_factory,
                email=email,
                password=PASSWORD,
                role=role,
            ),
            "email": email,
        }

    async with session_factory() as session, session.begin():
        rows = {}
        for label in ("a", "b", "admin"):
            rows[f"project_{label}"] = Project(
                name=f"Owned project {label}", created_by=identities[label]["id"]
            )
            rows[f"brand_{label}"] = Brand(
                name=f"Owned brand {label}", created_by=identities[label]["id"]
            )
            session.add_all((rows[f"project_{label}"], rows[f"brand_{label}"]))
        await session.flush()

        for label in ("a", "b", "admin"):
            rows[f"product_{label}"] = Product(
                name=f"Owned product {label}",
                brand_id=rows[f"brand_{label}"].id,
                created_by=identities[label]["id"],
            )
            session.add(rows[f"product_{label}"])
        await session.flush()

        rows["project_asset_a"] = Asset(
            project_id=rows["project_a"].id,
            kind="IMAGE",
            filename="project-a.png",
            content_type="image/png",
            object_key=f"ownership/{uuid4()}",
            created_by=identities["a"]["id"],
        )
        rows["project_asset_b_on_a"] = Asset(
            project_id=rows["project_a"].id,
            kind="IMAGE",
            filename="project-b.png",
            content_type="image/png",
            object_key=f"ownership/{uuid4()}",
            created_by=identities["b"]["id"],
        )
        rows["product_asset_a"] = Asset(
            product_id=rows["product_a"].id,
            kind="IMAGE",
            filename="product-a.png",
            content_type="image/png",
            object_key=f"ownership/{uuid4()}",
            created_by=identities["a"]["id"],
        )
        rows["product_asset_b_on_a"] = Asset(
            product_id=rows["product_a"].id,
            kind="IMAGE",
            filename="product-b.png",
            content_type="image/png",
            object_key=f"ownership/{uuid4()}",
            created_by=identities["b"]["id"],
        )
        rows["project_video_a"] = Video(
            project_id=rows["project_a"].id,
            title="Project video A",
            brief="Fixture video owned by A",
            created_by=identities["a"]["id"],
        )
        rows["project_video_b_on_a"] = Video(
            project_id=rows["project_a"].id,
            title="Project video B",
            brief="Fixture video owned by B",
            created_by=identities["b"]["id"],
        )
        rows["product_video_a"] = Video(
            project_id=rows["project_a"].id,
            product_id=rows["product_a"].id,
            title="Product video A",
            brief="Fixture product video owned by A",
            created_by=identities["a"]["id"],
        )
        rows["product_video_b_on_a"] = Video(
            project_id=rows["project_b"].id,
            product_id=rows["product_a"].id,
            title="Product video B",
            brief="Fixture product video owned by B",
            created_by=identities["b"]["id"],
        )
        session.add_all(
            (
                rows["project_asset_a"],
                rows["project_asset_b_on_a"],
                rows["product_asset_a"],
                rows["product_asset_b_on_a"],
                rows["project_video_a"],
                rows["project_video_b_on_a"],
                rows["product_video_a"],
                rows["product_video_b_on_a"],
            )
        )
        await session.flush()
        ids = {key: value.id for key, value in rows.items()}

    return identities, ids


async def login(client, email):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return client.cookies.get("studio_session"), response.json()["csrf_token"]


def authenticate(client, credentials):
    token, csrf = credentials
    client.cookies.clear()
    client.cookies.set("studio_session", token)
    return {"X-CSRF-Token": csrf}


@pytest.mark.asyncio
async def test_resource_lists_search_counts_and_direct_reads_are_owner_scoped(
    session_factory,
):
    identities, ids = await seed_identities_and_resources(session_factory)
    async with api_client(session_factory) as client:
        credentials = {
            label: await login(client, identities[label]["email"])
            for label in ("a", "b", "admin")
        }

        authenticate(client, credentials["a"])
        for resource in ("projects", "brands", "products"):
            response = await client.get(
                f"/api/v1/{resource}?search=Owned&page=1&page_size=1"
            )
            assert response.status_code == 200
            assert response.json()["total"] == 1
            assert len(response.json()["items"]) == 1
            assert response.json()["items"][0]["name"].endswith("a")

        for resource, own_key, foreign_key in (
            ("projects", "project_a", "project_b"),
            ("brands", "brand_a", "brand_b"),
            ("products", "product_a", "product_b"),
        ):
            assert (
                await client.get(f"/api/v1/{resource}/{ids[own_key]}")
            ).status_code == 200
            assert (
                await client.get(f"/api/v1/{resource}/{ids[foreign_key]}")
            ).status_code == 404

        authenticate(client, credentials["b"])
        for resource, a_key in (
            ("projects", "project_a"),
            ("brands", "brand_a"),
            ("products", "product_a"),
        ):
            assert (await client.get(f"/api/v1/{resource}/{ids[a_key]}")).status_code == 404

        authenticate(client, credentials["admin"])
        for resource in ("projects", "brands", "products"):
            response = await client.get(f"/api/v1/{resource}?search=Owned")
            assert response.status_code == 200
            assert response.json()["total"] == 3
        assert (await client.get(f"/api/v1/projects/{ids['project_b']}")).status_code == 200
        assert (await client.get(f"/api/v1/brands/{ids['brand_b']}")).status_code == 200
        assert (await client.get(f"/api/v1/products/{ids['product_b']}")).status_code == 200


@pytest.mark.asyncio
async def test_cross_owner_mutations_and_brand_foreign_keys_fail_before_changes(session_factory):
    identities, ids = await seed_identities_and_resources(session_factory)
    async with api_client(session_factory) as client:
        credentials = {
            label: await login(client, identities[label]["email"])
            for label in ("a", "b", "admin")
        }
        headers = authenticate(client, credentials["a"])

        assert (
            await client.patch(
                f"/api/v1/projects/{ids['project_b']}",
                json={"name": "taken over"},
                headers={**headers, "If-Match": "1"},
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/projects/{ids['project_b']}/archive",
                headers={**headers, "If-Match": "1"},
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"/api/v1/brands/{ids['brand_b']}",
                json={"name": "taken over"},
                headers={**headers, "If-Match": "1"},
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"/api/v1/products/{ids['product_b']}",
                json={"name": "taken over"},
                headers={**headers, "If-Match": "1"},
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/products/{ids['product_b']}/archive",
                headers={**headers, "If-Match": "1"},
            )
        ).status_code == 404

        rejected_create = await client.post(
            "/api/v1/products",
            json={"name": "must not be created", "brand_id": ids["brand_b"]},
            headers=headers,
        )
        assert rejected_create.status_code == 404
        assert rejected_create.json()["error"]["code"] == "BRAND_NOT_FOUND"

        rejected_rebind = await client.patch(
            f"/api/v1/products/{ids['product_a']}",
            json={"brand_id": ids["brand_b"]},
            headers={**headers, "If-Match": "1"},
        )
        assert rejected_rebind.status_code == 404
        assert rejected_rebind.json()["error"]["code"] == "BRAND_NOT_FOUND"

        same_owner_create = await client.post(
            "/api/v1/products",
            json={"name": "same-owner product", "brand_id": ids["brand_a"]},
            headers=headers,
        )
        assert same_owner_create.status_code == 201

        admin_headers = authenticate(client, credentials["admin"])
        admin_create = await client.post(
            "/api/v1/products",
            json={"name": "admin product", "brand_id": ids["brand_b"]},
            headers=admin_headers,
        )
        # Admin access does not permit internally cross-owner Product/Brand graphs.
        assert admin_create.status_code == 404
        assert admin_create.json()["error"]["code"] == "BRAND_NOT_FOUND"
        admin_valid = await client.post(
            "/api/v1/products",
            json={"name": "admin valid product", "brand_id": ids["brand_admin"]},
            headers=admin_headers,
        )
        assert admin_valid.status_code == 201

    async with session_factory() as session:
        project_b = await session.get(Project, ids["project_b"])
        brand_b = await session.get(Brand, ids["brand_b"])
        product_a = await session.get(Product, ids["product_a"])
        product_b = await session.get(Product, ids["product_b"])
        products = list((await session.scalars(select(Product))).all())
        assert project_b.name == "Owned project b"
        assert project_b.archived is False
        assert brand_b.name == "Owned brand b"
        assert product_a.brand_id == ids["brand_a"]
        assert product_a.revision == 1
        assert product_b.name == "Owned product b"
        assert product_b.archived is False
        assert len(products) == 5
        assert not any(product.name == "admin product" for product in products)


@pytest.mark.asyncio
async def test_project_and_product_child_lists_hide_foreign_rows_and_ids(session_factory):
    identities, ids = await seed_identities_and_resources(session_factory)
    async with api_client(session_factory) as client:
        credentials = {
            label: await login(client, identities[label]["email"])
            for label in ("a", "b", "admin")
        }

        authenticate(client, credentials["a"])
        project_videos = await client.get(f"/api/v1/projects/{ids['project_a']}/videos")
        project_assets = await client.get(f"/api/v1/projects/{ids['project_a']}/assets")
        product_videos = await client.get(f"/api/v1/products/{ids['product_a']}/videos")
        product_assets = await client.get(f"/api/v1/products/{ids['product_a']}/assets")
        for response, own_ids, foreign_id in (
            (
                project_videos,
                {ids["project_video_a"], ids["product_video_a"]},
                ids["project_video_b_on_a"],
            ),
            (project_assets, {ids["project_asset_a"]}, ids["project_asset_b_on_a"]),
            (product_videos, {ids["product_video_a"]}, ids["product_video_b_on_a"]),
            (product_assets, {ids["product_asset_a"]}, ids["product_asset_b_on_a"]),
        ):
            assert response.status_code == 200
            assert response.json()["total"] == len(own_ids)
            child_ids = {item["id"] for item in response.json()["items"]}
            assert child_ids == own_ids
            assert foreign_id not in child_ids

        assert (
            await client.get(f"/api/v1/projects/{ids['project_b']}/videos")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/products/{ids['product_b']}/assets")
        ).status_code == 404

        authenticate(client, credentials["admin"])
        admin_project_videos = await client.get(
            f"/api/v1/projects/{ids['project_a']}/videos"
        )
        admin_product_assets = await client.get(
            f"/api/v1/products/{ids['product_a']}/assets"
        )
        assert admin_project_videos.status_code == 200
        assert admin_project_videos.json()["total"] == 2
        assert {item["id"] for item in admin_project_videos.json()["items"]} == {
            ids["project_video_a"], ids["product_video_a"]
        }
        assert (
            await client.get(f"/api/v1/videos/{ids['project_video_b_on_a']}")
        ).status_code == 404
        assert admin_product_assets.status_code == 200
        assert admin_product_assets.json()["total"] == 2
