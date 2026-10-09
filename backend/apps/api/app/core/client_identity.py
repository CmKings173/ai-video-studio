"""Authenticate the internal frontend hop; generic forwarding headers are never trusted."""

from hmac import compare_digest
from ipaddress import ip_address

from fastapi import Request

from apps.api.app.core.config import Settings


def login_client_host(request: Request, settings: Settings) -> str:
    fallback = request.client.host if request.client else "unknown"
    token = getattr(settings, "internal_proxy_token", "")
    supplied = request.headers.get("x-studio-proxy-token", "")
    if not token or not compare_digest(token.encode(), supplied.encode()):
        return fallback
    address = request.headers.get("x-studio-client-ip", "")
    if "%" in address:
        return fallback
    try:
        parsed = ip_address(address)
    except ValueError:
        return fallback
    # Use one bucket for IPv4 and its IPv6-mapped representation.
    return str(getattr(parsed, "ipv4_mapped", None) or parsed)
