import json
from pathlib import Path

from apps.api.app.main import app

ROOT = Path(__file__).resolve().parents[2]


def _parameter(operation: dict, name: str) -> dict:
    return next(item for item in operation.get("parameters", []) if item["name"] == name)


def test_generation_and_assembly_contracts_expose_required_concurrency_headers():
    schema = app.openapi()
    generation = schema["paths"]["/api/v1/scenes/{scene_id}/generations"]["post"]
    assembly = schema["paths"]["/api/v1/videos/{video_id}/assemble"]["post"]

    assert _parameter(generation, "Idempotency-Key")["required"] is True
    assert _parameter(assembly, "Idempotency-Key")["required"] is True
    assert _parameter(assembly, "If-Match")["required"] is True
    assert _parameter(generation, "X-CSRF-Token")["required"] is True
    assert generation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GenerationDTO"
    }


def test_all_validation_and_business_errors_use_the_public_error_envelope():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/videos"]["post"]
    error_ref = {"$ref": "#/components/schemas/ErrorEnvelope"}

    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == error_ref
    assert operation["responses"]["500"]["content"]["application/json"]["schema"] == error_ref
    error = schema["components"]["schemas"]["ErrorDTO"]
    assert "trace_id" in error["properties"]
    assert "request_id" not in error["properties"]


def test_cookie_auth_is_declared_for_protected_routes_only():
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]

    assert schemes["StudioSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "studio_session",
    }
    assert "security" not in schema["paths"]["/api/v1/auth/login"]["post"]
    assert schema["paths"]["/api/v1/projects"]["get"]["security"] == [
        {"StudioSession": []}
    ]


def test_scene_spec_contract_matches_persisted_scene_shape():
    properties = app.openapi()["components"]["schemas"]["SceneSpec"]["properties"]
    assert {
        "title",
        "purpose",
        "description",
        "subject",
        "action",
        "environment",
        "camera",
        "lighting",
        "style",
        "continuity",
        "dialogue",
        "soundscape",
    } <= properties.keys()


def test_checked_in_openapi_artifact_matches_application_schema():
    exported = json.loads((ROOT / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
    assert exported == app.openapi()
