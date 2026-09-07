from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.auth.policy import actor_for
from app.auth.security import CSRF_COOKIE, SESSION_COOKIE, resolve_device, same_origin
from app.core.config import get_settings
from app.core.database import SessionLocal

PUBLIC_PATHS = {
    "/login",
    "/signup",
    "/health/live",
    "/health/ready",
    "/manifest.webmanifest",
    "/service-worker.js",
}
PUBLIC_PREFIXES = ("/static/", "/invite/")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.user = None
        request.state.actor = None
        request.state.device = None
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not same_origin(request):
            return Response("Cross-site request rejected", status_code=403)
        resolved = None
        with SessionLocal() as db:
            resolved = resolve_device(db, request.cookies.get(SESSION_COOKIE, ""))
            if resolved:
                user, device = resolved
                request.state.user = user
                request.state.device = device
                request.state.actor = actor_for(db, user)
        path = request.url.path
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
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        return response
