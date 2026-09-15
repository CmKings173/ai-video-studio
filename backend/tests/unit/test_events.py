from apps.api.app.api.events import _dedupe_token, _encode


def test_video_event_token_changes_when_worker_updates_status_without_revision():
    assert _dedupe_token("video:v1", 4, {"status": "GENERATING"}) != _dedupe_token(
        "video:v1", 4, {"status": "READY"}
    )


def test_sse_payload_contains_schema_and_resource_revision():
    payload = _encode(7, "generation.progress", 3, {"generation_id": "g1"})
    assert "id: 7" in payload
    assert '"schema_version":1' in payload
    assert '"resource_revision":3' in payload
