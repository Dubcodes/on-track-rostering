from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.auth.network import resolve_request, same_origin
from app.auth.policy import actor_for
from app.auth.security import CSRF_COOKIE, SESSION_COOKIE, resolve_device
from app.branding.service import DEFAULT_BRANDING, branding_for
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.system_settings.service import DEFAULT_OPERATIONAL_SETTINGS, operational_settings_for

PUBLIC_PATHS = {
    "/login",
    "/login/totp",
    "/login/passkey/options",
    "/login/passkey/verify",
    "/signup",
    "/invite",
    "/invite/activate",
    "/health/live",
    "/health/ready",
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
        database_free = path == "/health/live" or path == "/service-worker.js" or path.startswith("/static/")
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
            return RedirectResponse(f"/login?next={path}", status_code=303)
        response = await call_next(request)
        if resolved:
            _, device = resolved
            days = (
                get_settings().trusted_device_days_elevated
                if device.elevated
                else get_settings().trusted_device_days_standard
            )
            response.set_cookie(
                SESSION_COOKIE,
                request.cookies.get(SESSION_COOKIE, ""),
                max_age=days * 86400,
                httponly=True,
                secure=get_settings().cookie_secure,
                samesite="lax",
                path="/",
            )
            if request.cookies.get(CSRF_COOKIE):
                response.set_cookie(
                    CSRF_COOKIE,
                    request.cookies[CSRF_COOKIE],
                    max_age=days * 86400,
                    httponly=False,
                    secure=get_settings().cookie_secure,
                    samesite="strict",
                    path="/",
                )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; img-src 'self' data:; style-src 'self' 'nonce-{request.state.csp_nonce}'; script-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        return response
