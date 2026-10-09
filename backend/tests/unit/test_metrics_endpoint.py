from types import SimpleNamespace

import pytest

from apps.api.app.api import metrics as metrics_api


class FailingFactory:
    def __call__(self):
        raise ConnectionError("database is down")


@pytest.mark.asyncio
async def test_metrics_endpoint_survives_database_outage(monkeypatch):
    class HealthyStore:
        def __init__(self, _settings):
            pass

        async def health(self):
            return True

    class HealthyComfy:
        def __init__(self, _settings):
            pass

        async def health(self):
            return True

    monkeypatch.setattr(metrics_api, "AssetStore", HealthyStore)
    monkeypatch.setattr(metrics_api, "ComfyAdapter", HealthyComfy)
    settings = SimpleNamespace(metrics_token="t" * 32, backup_status_file=None, workspace_root=".")
    request = SimpleNamespace(headers={"Authorization": f"Bearer {settings.metrics_token}"})

    response = await metrics_api.prometheus_metrics(request, settings, FailingFactory())

    assert response.status_code == 200
    assert "studio_postgres_up 0" in response.body.decode()
    assert "studio_minio_up 1" in response.body.decode()
    assert "studio_comfyui_up 1" in response.body.decode()
