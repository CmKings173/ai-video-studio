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
        SimpleNamespace(
            status="DELETED", deleted_at=now - timedelta(hours=9), purged_at=None
        ),
        now,
    )
    assert not policy.eligible(
        SimpleNamespace(
            status="DELETED",
            deleted_at=now - timedelta(hours=9),
            purged_at=now - timedelta(hours=1),
        ),
        now,
    )
    assert not policy.eligible(
        SimpleNamespace(status="READY", created_at=now - timedelta(days=365)), now
    )


def test_retention_policy_deleting_retry_clock_uses_claim_timestamp():
    now = datetime.now(UTC)
    policy = RetentionPolicy(
        pending_hours=2, failed_hours=4, deleted_hours=8, deleting_retry_hours=1
    )
    # Stale claim is eligible for retry
    assert policy.eligible(
        SimpleNamespace(
            status="DELETING",
            delete_claimed_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=5),
        ),
        now,
    )
    # Fresh claim is NOT eligible for retry
    assert not policy.eligible(
        SimpleNamespace(
            status="DELETING",
            delete_claimed_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(hours=5),
        ),
        now,
    )
    # Fallback to updated_at if delete_claimed_at is None
    assert policy.eligible(
        SimpleNamespace(
            status="DELETING",
            delete_claimed_at=None,
            updated_at=now - timedelta(hours=2),
        ),
        now,
    )
