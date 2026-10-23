from __future__ import annotations

import binascii
import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.access import user_has_active_access
from app.auth.factors import (
    active_totp,
    consume_challenge,
    consume_recovery_code,
    create_challenge,
    mfa_required,
    verify_totp_factor,
)
from app.auth.network import resolve_request
from app.auth.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    challenge_throttle_key,
    clear_failures,
    create_device,
    is_throttled,
    mfa_throttle_keys,
    record_failure,
    safe_return_path,
    set_auth_cookies,
    throttle_keys,
    verify_credential,
    verify_csrf,
)
from app.auth.service import (
    activate_invitation,
    activate_pending_grants,
    consume_credential_reset,
    invitation_details,
    normalise_email,
    validated_email,
)
from app.catalog.models import Region
from app.core.config import get_settings
from app.core.database import get_db
from app.core.forms import optional_uuid
from app.core.time import utcnow
from app.identity.models import SignupRequest, TrustedDevice, User, WebAuthnChallenge
from app.web import context, templates

router = APIRouter()
TOTP_LOGIN_COOKIE = "ontrack_totp_login"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/month"):
    return templates.TemplateResponse(
        "login.html", context(request, next=safe_return_path(next), error=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    credential: str = Form(...),
    next: str = Form("/month"),
    db: Session = Depends(get_db),
):
    next = safe_return_path(next)
    email = normalise_email(email)
    keys = throttle_keys(email, resolve_request(request).client_address)
    user = db.scalar(select(User).where(User.email == email, User.status == "ACTIVE"))
    contractor_expiry = user.contractor_access_expires_at if user else None
    if contractor_expiry and contractor_expiry.tzinfo is None:
        contractor_expiry = contractor_expiry.replace(tzinfo=UTC)
    if user and contractor_expiry and contractor_expiry <= utcnow():
        user = None
    if (
        any(is_throttled(db, key) for key in keys)
        or not user
        or not verify_credential(credential, user.credential_hash)
    ):
        for key in keys:
            record_failure(db, key)
        return templates.TemplateResponse(
            "login.html",
            context(request, next=next, error="Unable to sign in with those details."),
            status_code=400,
        )
    for key in keys:
        clear_failures(db, key)
    activate_pending_grants(db, user, credential)
    if not user_has_active_access(db, user.id):
        return templates.TemplateResponse(
            "login.html",
            context(request, next=next, error="This account has no active access grant."),
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if mfa_required(db, user):
        mfa_keys = mfa_throttle_keys(user.id, resolve_request(request).client_address)
        if any(is_throttled(db, key) for key in mfa_keys):
            return templates.TemplateResponse(
                "login.html",
                context(request, next=next, error="Too many authentication attempts. Try again later."),
                status_code=429,
                headers={"Cache-Control": "no-store"},
            )
        challenge_keys = (
            challenge_throttle_key("TOTP_LOGIN_USER", user.id),
            challenge_throttle_key(
                "TOTP_LOGIN_ADDRESS", resolve_request(request).client_address
            ),
        )
        if any(is_throttled(db, key) for key in challenge_keys):
            return templates.TemplateResponse(
                "login.html",
                context(request, next=next, error="Too many authentication challenges. Try again later."),
                status_code=429,
                headers={"Cache-Control": "no-store"},
            )
        for key in challenge_keys:
            record_failure(db, key)
        if not active_totp(db, user.id):
            return templates.TemplateResponse(
                "login.html",
                context(
                    request,
                    next=next,
                    error="This account requires authenticator MFA but has no active factor.",
                ),
                status_code=403,
            )
        challenge, raw = create_challenge(db, purpose="TOTP_LOGIN", user_id=user.id)
        db.commit()
        encoded = __import__("base64").urlsafe_b64encode(raw).decode().rstrip("=")
        from urllib.parse import urlencode

        response = RedirectResponse(
            "/login/totp?" + urlencode({"challenge_id": str(challenge.id), "next": next}),
            status_code=303,
        )
        response.set_cookie(
            TOTP_LOGIN_COOKIE,
            encoded,
            max_age=get_settings().webauthn_challenge_minutes * 60,
            httponly=True,
            secure=get_settings().cookie_secure,
            samesite="strict",
            path="/login/totp",
        )
        return response
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Browser")[:120]
    )
    response = RedirectResponse(next, status_code=303)
    set_auth_cookies(response, raw_session, raw_csrf, device)
    return response


@router.get("/login/totp", response_class=HTMLResponse)
def totp_login_page(request: Request, challenge_id: str, next: str = "/month"):
    next = safe_return_path(next)
    return templates.TemplateResponse(
        "totp_login.html",
        context(request, challenge_id=challenge_id, next=next, error=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login/totp", response_class=HTMLResponse)
def totp_login(
    request: Request,
    challenge_id: uuid.UUID = Form(...),
    code: str = Form(""),
    recovery_code: str = Form(""),
    next: str = Form("/month"),
    db: Session = Depends(get_db),
):
    import base64

    next = safe_return_path(next)
    encoded = request.cookies.get(TOTP_LOGIN_COOKIE, "")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, binascii.Error):
        raw = b""
    challenge = db.get(WebAuthnChallenge, challenge_id)
    user = db.get(User, challenge.user_id) if challenge and challenge.user_id else None
    mfa_keys = mfa_throttle_keys(
        user.id if user else "unknown", resolve_request(request).client_address
    )
    if any(is_throttled(db, key) for key in mfa_keys):
        return templates.TemplateResponse(
            "totp_login.html",
            context(request, challenge_id=challenge_id, next=next, error="Too many authentication attempts. Try again later."),
            status_code=429,
            headers={"Cache-Control": "no-store"},
        )
    factor = active_totp(db, user.id, for_update=True) if user else None
    try:
        if not user or not factor:
            raise ValueError("Unavailable login challenge.")
        consume_challenge(
            db,
            challenge_id=challenge_id,
            purpose="TOTP_LOGIN",
            raw_challenge=raw,
            user_id=user.id,
        )
    except ValueError:
        return templates.TemplateResponse(
            "totp_login.html",
            context(request, challenge_id=challenge_id, next=next, error="Login challenge expired."),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    used_recovery = bool(recovery_code.strip())
    accepted = (
        consume_recovery_code(db, user.id, recovery_code)
        if used_recovery
        else verify_totp_factor(factor, code)
    )
    if not accepted:
        db.commit()
        for key in mfa_keys:
            record_failure(db, key)
        return templates.TemplateResponse(
            "totp_login.html",
            context(request, challenge_id=challenge_id, next=next, error="Authentication code was not accepted."),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    if used_recovery:
        record_audit(db, "mfa.recovery_code_used", "user", user.id, user.id)
    db.commit()
    for key in mfa_keys:
        clear_failures(db, key)
    for key in (
        challenge_throttle_key("TOTP_LOGIN_USER", user.id),
        challenge_throttle_key("TOTP_LOGIN_ADDRESS", resolve_request(request).client_address),
    ):
        clear_failures(db, key)
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Browser")[:120]
    )
    response = RedirectResponse(next, status_code=303)
    response.delete_cookie(TOTP_LOGIN_COOKIE, path="/login/totp")
    set_auth_cookies(response, raw_session, raw_csrf, device)
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...), db: Session = Depends(get_db)):
    verify_csrf(request, csrf_token)
    if request.state.device:
        device = db.get(TrustedDevice, request.state.device.id)
        if device:
            from app.core.time import utcnow

            device.revoked_at = utcnow()
            db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return response


@router.get("/invite", response_class=HTMLResponse)
def invitation_page(request: Request):
    return templates.TemplateResponse(
        "invite.html", context(request, token="", available=True, error="", invite_email="", display_name=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/invite/details")
def invitation_metadata(token: str = Form(...), db: Session = Depends(get_db)):
    try:
        invite = invitation_details(db, token)
    except ValueError:
        return JSONResponse(
            {"available": False, "detail": "This invitation is unavailable."},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {
            "available": True,
            "email": invite.email,
            "display_name": invite.display_name,
            "credential_help": (
                "Use at least eight digits or a password of at least 12 characters."
                if invite.role == "ADMIN"
                else "Use at least six digits, or a password of at least 12 characters."
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/invite/activate", response_class=HTMLResponse)
def invitation_activate(
    request: Request,
    token: str = Form(...),
    display_name: str = Form(...),
    credential: str = Form(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        invite = invitation_details(db, token)
        if credential != confirmation:
            raise ValueError("PIN/password confirmation did not match.")
        user = activate_invitation(db, token, display_name, credential)
    except ValueError as exc:
        return templates.TemplateResponse(
            "invite.html",
            context(
                request, token=token, available=True, error=str(exc),
                invite_email=(invite.email if "invite" in locals() else ""),
                display_name=display_name,
            ),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    if mfa_required(db, user):
        return RedirectResponse("/login?activated=1&mfa=required", status_code=303)
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Browser")[:120]
    )
    response = RedirectResponse("/month", status_code=303)
    set_auth_cookies(response, raw_session, raw_csrf, device)
    return response


@router.get("/credential-reset", response_class=HTMLResponse)
def credential_reset_page(request: Request):
    return templates.TemplateResponse(
        "credential_reset.html",
        context(request, token="", available=True, error=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/credential-reset/complete", response_class=HTMLResponse)
def credential_reset_complete(
    request: Request,
    token: str = Form(""),
    credential: str = Form(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    if credential != confirmation:
        return templates.TemplateResponse(
            "credential_reset.html",
            context(
                request,
                token=token,
                available=True,
                error="New credentials did not match.",
            ),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    try:
        consume_credential_reset(db, token, credential)
    except ValueError as exc:
        return templates.TemplateResponse(
            "credential_reset.html",
            context(request, token=token, available=True, error=str(exc)),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    return RedirectResponse("/login?credential=reset", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: Session = Depends(get_db)):
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return templates.TemplateResponse(
        "signup.html",
        context(
            request,
            regions=regions,
            enabled=request.state.system_settings.public_signup_enabled,
            sent=False,
        ),
    )


@router.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    requested_region_id: str = Form(""),
    db: Session = Depends(get_db),
):
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    if not request.state.system_settings.public_signup_enabled:
        return templates.TemplateResponse(
            "signup.html", context(request, regions=regions, enabled=False, sent=False), status_code=404
        )
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Enter your name")
    region_id = optional_uuid(requested_region_id, "region")
    if region_id:
        requested_region = db.get(Region, region_id)
        if not requested_region or requested_region.lifecycle != "ACTIVE":
            raise HTTPException(400, "Select an active region")
    # The public form intentionally has no role/scope inputs. This row grants no access.
    try:
        signup_email = validated_email(email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    signup_keys = throttle_keys("signup:" + signup_email, resolve_request(request).client_address)
    if any(is_throttled(db, key) for key in signup_keys):
        return templates.TemplateResponse(
            "signup.html", context(request, regions=regions, enabled=True, sent=True)
        )
    for key in signup_keys:
        record_failure(db, key)
    pending = db.scalar(
        select(SignupRequest.id).where(SignupRequest.email == signup_email, SignupRequest.status == "PENDING")
    )
    if not pending:
        db.add(
            SignupRequest(
                display_name=clean_name,
                email=signup_email,
                requested_region_id=region_id,
            )
        )
    db.commit()
    return templates.TemplateResponse(
        "signup.html", context(request, regions=regions, enabled=True, sent=True)
    )
