import pytest

from apps.api.app.core.config import Settings
from apps.api.app.core.metrics import MetricsRegistry


def test_prometheus_metrics_have_measurable_queue_and_backup_series():
    registry = MetricsRegistry()
    registry.set("studio_generation_queue_oldest_age_seconds", 12)
    registry.set("studio_backup_age_seconds", -1)
    output = registry.render()
    assert "studio_generation_queue_oldest_age_seconds 12" in output
    assert "studio_backup_age_seconds -1" in output


def test_production_rejects_placeholder_metrics_token():
    with pytest.raises(ValueError, match="metrics token"):
        Settings(
            app_env="production",
            cookie_secure=True,
            minio_access_key="access",
            minio_secret_key="secret",
            bootstrap_admin_password="a" * 16,
            metrics_token="replace-with-a-random-32-character-token",
        )
