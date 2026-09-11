from __future__ import annotations

import binascii
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.factors import (
    active_totp,
    consume_challenge,
    create_challenge,
    mfa_required,
    verify_totp_factor,
)
from app.auth.network import resolve_request
from app.auth.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_failures,
    create_device,
    is_safe_next,
    is_throttled,
    record_failure,
    set_auth_cookies,
    throttle_keys,
    verify_credential,
    verify_csrf,
)
from app.auth.service import (
    activate_invitation,
    activate_pending_grants,
    normalise_email,
    validated_email,
)
from app.catalog.models import Region
from app.core.config import get_settings
from app.core.database import get_db
from app.identity.models import SignupRequest, TrustedDevice, User, WebAuthnChallenge
from app.web import context, templates

router = APIRouter()
TOTP_LOGIN_COOKIE = "ontrack_totp_login"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/month"):
    return templates.TemplateResponse("login.html", context(request, next=next, error=""))


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    credential: str = Form(...),
    next: str = Form("/month"),
    db: Session = Depends(get_db),
):
    email = normalise_email(email)
    keys = throttle_keys(email, resolve_request(request).client_address)
    user = db.scalar(select(User).where(User.email == email, User.status == "ACTIVE"))
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
    if mfa_required(db, user):
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
        encoded = __import__("base64").urlsafe_b64encode(raw).decode().rstrip("=")
        response = RedirectResponse(f"/login/totp?challenge_id={challenge.id}&next={next}", status_code=303)
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
    response = RedirectResponse(next if is_safe_next(next) else "/month", status_code=303)
    set_auth_cookies(response, raw_session, raw_csrf, device)
    return response


@router.get("/login/totp", response_class=HTMLResponse)
def totp_login_page(request: Request, challenge_id: str, next: str = "/month"):
    return templates.TemplateResponse(
        "totp_login.html",
        context(request, challenge_id=challenge_id, next=next, error=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login/totp", response_class=HTMLResponse)
def totp_login(
    request: Request,
    challenge_id: uuid.UUID = Form(...),
    code: str = Form(...),
    next: str = Form("/month"),
    db: Session = Depends(get_db),
):
    import base64

    encoded = request.cookies.get(TOTP_LOGIN_COOKIE, "")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, binascii.Error):
        raw = b""
    challenge = db.get(WebAuthnChallenge, challenge_id)
    user = db.get(User, challenge.user_id) if challenge and challenge.user_id else None
    factor = active_totp(db, user.id) if user else None
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
    if not verify_totp_factor(factor, code):
        db.commit()
        return templates.TemplateResponse(
            "totp_login.html",
            context(request, challenge_id=challenge_id, next=next, error="Authenticator code was not accepted."),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    db.commit()
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Browser")[:120]
    )
    response = RedirectResponse(next if is_safe_next(next) else "/month", status_code=303)
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
        "invite.html", context(request, token="", available=True, error=""),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/invite/activate", response_class=HTMLResponse)
def invitation_activate(
    request: Request,
    token: str = Form(...),
    display_name: str = Form(...),
    credential: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        activate_invitation(db, token, display_name, credential)
    except ValueError as exc:
        return templates.TemplateResponse(
            "invite.html",
            context(request, token=token, available=True, error=str(exc)),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    return RedirectResponse("/login?activated=1", status_code=303)


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
    import uuid

    region_id = uuid.UUID(requested_region_id) if requested_region_id else None
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
