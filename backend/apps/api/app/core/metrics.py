"""Small dependency-free Prometheus registry for the on-prem deployment."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Mapping


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
