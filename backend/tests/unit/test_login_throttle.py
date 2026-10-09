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


def test_login_throttle_record_login_success_preserves_ip_history():
    now = [100.0]
    limiter = LoginThrottle(
        max_entries=100, max_failures=5, base_delay_seconds=60, clock=lambda: now[0]
    )
    ip_key = "ip:192.168.1.50"
    valid_email = "valid@example.test"
    valid_identity_key = f"identity:{valid_email}"
    valid_attempt_key = f"attempt:192.168.1.50:{valid_email}"

    # 4 failed attempts from same IP across different accounts
    for i in range(4):
        now[0] += 1
        limiter.record_failure_many(
            (ip_key, f"identity:wrong{i}@test", f"attempt:192.168.1.50:wrong{i}@test")
        )

    # IP is not blocked yet (threshold is 5)
    assert limiter.retry_after(ip_key) == 0

    # Successful login for valid user
    now[0] += 1
    limiter.record_login_success(
        ip_key=ip_key,
        identity_key=valid_identity_key,
        attempt_key=valid_attempt_key,
    )

    # Identity and attempt keys are cleared
    assert limiter.retry_after(valid_identity_key) == 0
    assert limiter.retry_after(valid_attempt_key) == 0

    # IP-wide failure history is NOT cleared, next failure triggers IP block
    now[0] += 1
    limiter.record_failure_many(
        (ip_key, "identity:another@test", "attempt:192.168.1.50:another@test")
    )
    assert limiter.retry_after(ip_key) > 0
