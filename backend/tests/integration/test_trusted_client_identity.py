import pytest

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.main import app
from tests.integration.test_authz import api_client

TOKEN = "internal-test-credential-32-characters"


@pytest.fixture(autouse=True)
def throttle_clock(monkeypatch):
    login_throttle.clear()
    monkeypatch.setattr(login_throttle, "_clock", lambda: 100.0)
    yield
    login_throttle.clear()


async def fail_login(session_factory, ip, token=TOKEN, *, configured=True):
    async with api_client(session_factory, client=("172.18.0.5", 12345)) as client:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None, internal_proxy_token=TOKEN if configured else ""
        )
        return await client.post(
            "/api/v1/auth/login",
            json={"email": f"user-{login_throttle.size}@example.test", "password": "wrong"},
            headers={"x-studio-client-ip": ip, "x-studio-proxy-token": token,
                     "x-forwarded-for": ip},
        )


async def test_trusted_proxy_clients_have_separate_ip_throttle_buckets(session_factory):
    for _ in range(5):
        assert (await fail_login(session_factory, "192.0.2.1")).status_code == 401
    assert (await fail_login(session_factory, "192.0.2.1")).status_code == 429
    assert (await fail_login(session_factory, "192.0.2.2")).status_code == 401


@pytest.mark.parametrize("token,configured", [("wrong", True), (TOKEN, False), ("", True)])
async def test_spoofed_or_unconfigured_proxy_identity_uses_socket_ip(
    session_factory, token, configured
):
    for n in range(5):
        assert (await fail_login(
            session_factory, f"192.0.2.{n + 1}", token, configured=configured
        )).status_code == 401
    assert (await fail_login(
        session_factory, "192.0.2.99", token, configured=configured
    )).status_code == 429


@pytest.mark.parametrize("ip", ["192.0.2.1, 192.0.2.2", "invalid", "fe80::1%eth0"])
async def test_invalid_authenticated_identity_falls_back_to_socket(session_factory, ip):
    for _ in range(5):
        assert (await fail_login(session_factory, ip)).status_code == 401
    assert (await fail_login(session_factory, "192.0.2.99", token="wrong")).status_code == 429


def test_proxy_token_rejects_short_configuration():
    with pytest.raises(ValueError, match="32"):
        Settings(_env_file=None, internal_proxy_token="short")
