"""Small dependency-free Prometheus registry for the on-prem deployment."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

_HTTP_METHOD_LABELS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_UNMATCHED_ROUTE_LABEL = "__unmatched__"


def http_request_labels(request: Any) -> dict[str, str]:
    """Return bounded method and route-template labels for HTTP metrics."""
    raw_method = getattr(request, "method", "")
    method = raw_method.upper() if isinstance(raw_method, str) and len(raw_method) <= 16 else ""
    if method not in _HTTP_METHOD_LABELS:
        method = "OTHER"

    scope = getattr(request, "scope", None)
    route = scope.get("route") if isinstance(scope, Mapping) else None
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str) or not route_path or len(route_path) > 200:
        path = _UNMATCHED_ROUTE_LABEL
    else:
        path = "".join(
            char if char.isascii() and (char.isalnum() or char in "_./{}:-") else "_"
            for char in route_path
        )
        path = path or _UNMATCHED_ROUTE_LABEL

    return {"method": method, "path": path}


class MetricsRegistry:
    _buckets = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: defaultdict[
            tuple[str, tuple[tuple[str, str], ...]], float
        ] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: defaultdict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, float]
        ] = defaultdict(
            lambda: {"count": 0.0, "sum": 0.0, **{str(bucket): 0.0 for bucket in self._buckets}}
        )

    @staticmethod
    def _labels(labels: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((key, str(value)) for key, value in (labels or {}).items()))

    def inc(
        self, name: str, value: float = 1, labels: Mapping[str, object] | None = None
    ) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            self._counters[key] += value

    def set(self, name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            self._gauges[key] = value

    def observe(
        self, name: str, value: float, labels: Mapping[str, object] | None = None
    ) -> None:
        key = (name, self._labels(labels))
        with self._lock:
            histogram = self._histograms[key]
            histogram["count"] += 1
            histogram["sum"] += value
            for bucket in self._buckets:
                if value <= bucket:
                    histogram[str(bucket)] += 1

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: dict(value) for key, value in self._histograms.items()}
        names = {name for name, _ in counters} | {name for name, _ in gauges} | {
            name for name, _ in histograms
        }
        for name in sorted(names):
            kind = (
                "histogram"
                if any(n == name for n, _ in histograms)
                else "gauge"
                if any(n == name for n, _ in gauges)
                else "counter"
            )
            lines.append(f"# TYPE {name} {kind}")
            for (metric, labels), value in sorted(counters.items()):
                if metric == name:
                    lines.append(f"{metric}{_format_labels(labels)} {value:g}")
            for (metric, labels), value in sorted(gauges.items()):
                if metric == name:
                    lines.append(f"{metric}{_format_labels(labels)} {value:g}")
            for (metric, labels), value in sorted(histograms.items()):
                if metric != name:
                    continue
                for bucket in self._buckets:
                    bucket_labels = labels + (("le", _format_number(bucket)),)
                    lines.append(
                        f"{metric}_bucket{_format_labels(bucket_labels)} {value[str(bucket)]:g}"
                    )
                inf_labels = labels + (("le", "+Inf"),)
                lines.append(
                    f"{metric}_bucket{_format_labels(inf_labels)} {value['count']:g}"
                )
                lines.append(f"{metric}_count{_format_labels(labels)} {value['count']:g}")
                lines.append(f"{metric}_sum{_format_labels(labels)} {value['sum']:g}")
        return "\n".join(lines) + ("\n" if lines else "")


def _format_number(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = (
        f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in labels
    )
    return "{" + ",".join(escaped) + "}"


metrics = MetricsRegistry()
