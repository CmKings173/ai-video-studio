"""Bounded in-process login abuse protection."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _FailureState:
    failures: int = 0
    blocked_until: float = 0.0
    last_seen: float = 0.0


class LoginThrottle:
    """Progressive per-identity/IP throttling with bounded memory."""

    def __init__(
        self,
        *,
        max_entries: int = 10_000,
        max_failures: int = 5,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 300.0,
        failure_window_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(max_entries, max_failures) < 1:
            raise ValueError("throttle bounds must be positive")
        if min(base_delay_seconds, max_delay_seconds) <= 0:
            raise ValueError("throttle delays must be positive")
        self.max_entries = max_entries
        self.max_failures = max_failures
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.failure_window_seconds = failure_window_seconds
        self._clock = clock
        self._entries: dict[str, _FailureState] = {}
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._entries)

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            self._purge(now)
            state = self._entries.get(key)
            if state is None or state.blocked_until <= now:
                return 0
            return max(1, int(state.blocked_until - now + 0.999))

    def record_failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._purge(now)
            state = self._entries.setdefault(key, _FailureState(last_seen=now))
            state.failures += 1
            state.last_seen = now
            if state.failures >= self.max_failures:
                exponent = state.failures - self.max_failures
                delay = min(self.max_delay_seconds, self.base_delay_seconds * (2**exponent))
                state.blocked_until = now + delay
            self._trim()

    def record_success(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _purge(self, now: float) -> None:
        expired = [
            key
            for key, state in self._entries.items()
            if now - state.last_seen >= self.failure_window_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _trim(self) -> None:
        overflow = len(self._entries) - self.max_entries
        if overflow <= 0:
            return
        oldest = sorted(self._entries, key=lambda key: self._entries[key].last_seen)[:overflow]
        for key in oldest:
            self._entries.pop(key, None)


login_throttle = LoginThrottle()
