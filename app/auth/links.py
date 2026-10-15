from __future__ import annotations

from urllib.parse import quote

from app.core.config import get_settings


def _trusted_fragment_url(path: str, raw_token: str) -> str:
    """Build a copyable one-time URL without consulting the request Host header."""
    origin = get_settings().webauthn_origin.rstrip("/")
    return f"{origin}{path}#token={quote(raw_token, safe='')}"


def invitation_url(raw_token: str) -> str:
    return _trusted_fragment_url("/invite", raw_token)


def credential_reset_url(raw_token: str) -> str:
    return _trusted_fragment_url("/credential-reset", raw_token)
