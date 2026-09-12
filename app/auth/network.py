from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import get_settings


@dataclass(frozen=True)
class ResolvedRequest:
    client_address: str
    scheme: str
    host: str
    port: int
    forwarded: bool

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port


def normalize_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in parsed.netloc)
    ):
        return None
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _trusted_peer(peer: str) -> bool:
    address = _ip(peer)
    if not address:
        return False
    parsed = ipaddress.ip_address(address)
    for value in get_settings().trusted_proxy_cidrs:
        try:
            if parsed in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def _single_header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) != 1:
        return None
    value = values[0].strip()
    return value if value and "," not in value else None


def _request_origin(request: Request) -> tuple[str, str, int]:
    scheme = request.url.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    host = _single_header(request, "host")
    direct = normalize_origin(f"{scheme}://{host}") if host else None
    return direct or (scheme, "invalid", default_port)


def resolve_request(request: Request) -> ResolvedRequest:
    peer = _ip(request.client.host if request.client else "") or "unknown"
    direct = _request_origin(request)
    if not _trusted_peer(peer):
        return ResolvedRequest(peer, *direct, False)

    forwarded_for = _single_header(request, "x-forwarded-for")
    forwarded_proto = _single_header(request, "x-forwarded-proto")
    forwarded_host_values = request.headers.getlist("x-forwarded-host")
    forwarded_host = _single_header(request, "x-forwarded-host") if forwarded_host_values else None
    forwarded_port_values = request.headers.getlist("x-forwarded-port")
    if (forwarded_host_values and forwarded_host is None) or len(forwarded_port_values) > 1:
        return ResolvedRequest(peer, *direct, False)
    forwarded_port = forwarded_port_values[0].strip() if forwarded_port_values else ""
    client = _ip(forwarded_for or "")
    external_host = forwarded_host or _single_header(request, "host")
    if not client or forwarded_proto not in {"http", "https"} or not external_host:
        return ResolvedRequest(peer, *direct, False)
    if "," in forwarded_port or (forwarded_port and not forwarded_port.isdigit()):
        return ResolvedRequest(peer, *direct, False)
    host_value = external_host
    if forwarded_port:
        try:
            existing_port = urlsplit(f"{forwarded_proto}://{external_host}").port
        except ValueError:
            return ResolvedRequest(peer, *direct, False)
        if existing_port is not None and existing_port != int(forwarded_port):
            return ResolvedRequest(peer, *direct, False)
        if existing_port is None:
            host_value = f"{external_host}:{forwarded_port}"
    external = normalize_origin(f"{forwarded_proto}://{host_value}")
    if external is None:
        return ResolvedRequest(peer, *direct, False)
    return ResolvedRequest(client, *external, True)


def same_origin(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "same-origin").lower() == "cross-site":
        return False
    origin = request.headers.get("origin", "").strip()
    return not origin or normalize_origin(origin) == resolve_request(request).origin
