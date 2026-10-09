from types import SimpleNamespace

from apps.api.app.core.metrics import MetricsRegistry, http_request_labels


def test_metrics_registry_renders_prometheus_counters_gauges_and_histograms():
    registry = MetricsRegistry()
    registry.inc("studio_requests_total", labels={"method": "GET", "path": "/api"})
    registry.set("studio_queue_count", 3)
    registry.observe("studio_request_duration_seconds", 0.02, labels={"method": "GET"})

    rendered = registry.render()

    assert 'studio_requests_total{method="GET",path="/api"} 1' in rendered
    assert "studio_queue_count 3" in rendered
    assert (
        'studio_request_duration_seconds_bucket{le="0.025",method="GET"} 1' in rendered
        or 'studio_request_duration_seconds_bucket{method="GET",le="0.025"} 1' in rendered
    )
    assert 'studio_request_duration_seconds_count{method="GET"} 1' in rendered


def test_http_metric_labels_bound_unmatched_paths_and_methods():
    registry = MetricsRegistry()

    for index in range(500):
        unmatched_request = SimpleNamespace(
            method=f"CUSTOM-{index}",
            scope={"route": None},
            url=SimpleNamespace(path=f"/api/v1/missing/{index}"),
        )
        registry.inc("studio_http_requests_total", labels=http_request_labels(unmatched_request))

        matched_request = SimpleNamespace(
            method="GET",
            scope={"route": SimpleNamespace(path="/api/v1/assets/{asset_id}")},
            url=SimpleNamespace(path=f"/api/v1/assets/{index}"),
        )
        registry.inc("studio_http_requests_total", labels=http_request_labels(matched_request))

    samples = [
        line for line in registry.render().splitlines()
        if line.startswith("studio_http_requests_total{")
    ]

    assert len(samples) == 2
    assert any('method="OTHER",path="__unmatched__"' in line for line in samples)
    assert any('method="GET",path="/api/v1/assets/{asset_id}"' in line for line in samples)
