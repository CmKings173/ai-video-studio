from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from apps.api.app.services.retention import RetentionPolicy


def test_retention_policy_uses_runtime_windows_and_status_timestamps():
    now = datetime.now(UTC)
    policy = RetentionPolicy(pending_hours=2, failed_hours=4, deleted_hours=8)

    assert policy.eligible(
        SimpleNamespace(status="PENDING_UPLOAD", created_at=now - timedelta(hours=3)), now
    )
    assert not policy.eligible(
        SimpleNamespace(
            status="FAILED",
            created_at=now - timedelta(days=30),
            failed_at=now - timedelta(hours=3),
        ),
        now,
    )
    assert policy.eligible(
        SimpleNamespace(
            status="FAILED",
            created_at=now - timedelta(days=30),
            failed_at=now - timedelta(hours=5),
        ),
        now,
    )
    assert policy.eligible(
        SimpleNamespace(status="DELETED", deleted_at=now - timedelta(hours=9)), now
    )
    assert not policy.eligible(
        SimpleNamespace(status="READY", created_at=now - timedelta(days=365)), now
    )
