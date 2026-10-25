from __future__ import annotations

import secrets
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.auth.network import resolve_request, same_origin
from app.auth.policy import actor_for
from app.auth.security import FRESH_ACTION_COOKIE, SESSION_COOKIE, resolve_device
from app.branding.service import DEFAULT_BRANDING, branding_for
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.system_settings.service import DEFAULT_OPERATIONAL_SETTINGS, operational_settings_for

PUBLIC_PATHS = {
    "/login",
    "/login/password",
    "/login/totp",
    "/login/passkey/options",
    "/login/passkey/verify",
    "/signup",
    "/invite",
    "/invite/details",
    "/invite/activate",
    "/credential-reset",
    "/credential-reset/complete",
    "/health/live",
    "/health/ready",
    "/branding/favicon",
    "/manifest.webmanifest",
    "/service-worker.js",
}
PUBLIC_PREFIXES = ("/static/",)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.user = None
        request.state.actor = None
        request.state.device = None
        request.state.network = resolve_request(request)
        request.state.csp_nonce = secrets.token_urlsafe(18)
        request.state.branding = DEFAULT_BRANDING
        request.state.system_settings = DEFAULT_OPERATIONAL_SETTINGS
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not same_origin(request):
            return Response("Cross-site request rejected", status_code=403)
        resolved = None
        path = request.url.path
        database_free = (
            path in {"/branding/favicon", "/health/live", "/service-worker.js"}
            or path.startswith("/static/")
        )
        if not database_free:
            with SessionLocal() as db:
                request.state.branding = branding_for(db)
                request.state.system_settings = operational_settings_for(db)
                resolved = resolve_device(db, request.cookies.get(SESSION_COOKIE, ""))
                if resolved:
                    user, device = resolved
                    request.state.user = user
                    request.state.device = device
                    request.state.actor = actor_for(db, user)
        public = path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
        if not public and request.state.user is None:
            return RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)
        response = await call_next(request)
        if getattr(request.state, "fresh_action_consumed", False):
            response.delete_cookie(FRESH_ACTION_COOKIE, path="/")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if get_settings().cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; img-src 'self' data:; style-src 'self' 'nonce-{request.state.csp_nonce}'; script-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        return response
