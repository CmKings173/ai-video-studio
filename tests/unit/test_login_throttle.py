from __future__ import annotations

from apps.api.app.core.login_throttle import LoginThrottle


def test_login_throttle_blocks_repeated_failures_and_resets_on_success():
    now = [100.0]
    limiter = LoginThrottle(
        max_entries=2, max_failures=2, base_delay_seconds=10, clock=lambda: now[0]
    )
    key = "127.0.0.1:user@example.test"

    assert limiter.retry_after(key) == 0
    limiter.record_failure(key)
    assert limiter.retry_after(key) == 0
    limiter.record_failure(key)
    assert limiter.retry_after(key) == 10
    limiter.record_success(key)
    assert limiter.retry_after(key) == 0


def test_login_throttle_evicts_oldest_entries_when_bounded():
    now = [100.0]
    limiter = LoginThrottle(max_entries=2, max_failures=2, clock=lambda: now[0])
    limiter.record_failure("a")
    now[0] += 1
    limiter.record_failure("b")
    now[0] += 1
    limiter.record_failure("c")

    assert limiter.size == 2
    assert limiter.retry_after("a") == 0


def test_login_throttle_multi_dimension_helpers_use_any_bucket_and_clear():
    limiter = LoginThrottle(
        max_entries=4, max_failures=2, base_delay_seconds=10, clock=lambda: 100.0
    )
    limiter.record_failure_many(("ip:1.2.3.4", "identity:user@example.test"))
    limiter.record_failure_many(("ip:1.2.3.4", "identity:user@example.test"))
    assert limiter.retry_after_any(("ip:1.2.3.4", "identity:other@example.test")) == 10
    limiter.record_success_many(("ip:1.2.3.4", "identity:user@example.test"))
    assert limiter.retry_after_any(("ip:1.2.3.3", "identity:user@example.test")) == 0
    limiter.clear()
    assert limiter.size == 0
