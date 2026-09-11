from apps.api.app.core.metrics import MetricsRegistry


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
