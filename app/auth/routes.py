from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_failures,
    create_device,
    is_safe_next,
    is_throttled,
    record_failure,
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
from app.identity.models import Invitation, SignupRequest, TrustedDevice, User
from app.web import context, templates

router = APIRouter()


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
    keys = throttle_keys(email, request.client.host if request.client else "unknown")
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
    raw_session, raw_csrf, device = create_device(
        db, user, request.headers.get("user-agent", "Browser")[:120]
    )
    response = RedirectResponse(next if is_safe_next(next) else "/month", status_code=303)
    max_days = (
        get_settings().trusted_device_days_elevated
        if device.elevated
        else get_settings().trusted_device_days_standard
    )
    response.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=max_days * 86400,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        raw_csrf,
        max_age=max_days * 86400,
        httponly=False,
        secure=get_settings().cookie_secure,
        samesite="strict",
        path="/",
    )
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


@router.get("/invite/{token}", response_class=HTMLResponse)
def invitation_page(token: str, request: Request, db: Session = Depends(get_db)):
    from app.auth.security import token_hash

    invite = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    available = bool(invite and not invite.consumed_at and not invite.revoked_at)
    return templates.TemplateResponse(
        "invite.html", context(request, token=token, invite=invite, available=available, error="")
    )


@router.post("/invite/{token}", response_class=HTMLResponse)
def invitation_activate(
    token: str,
    request: Request,
    display_name: str = Form(...),
    credential: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        activate_invitation(db, token, display_name, credential)
    except ValueError as exc:
        return templates.TemplateResponse(
            "invite.html",
            context(request, token=token, invite=None, available=True, error=str(exc)),
            status_code=400,
        )
    return RedirectResponse("/login?activated=1", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: Session = Depends(get_db)):
    regions = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return templates.TemplateResponse(
        "signup.html",
        context(request, regions=regions, enabled=get_settings().public_signup_enabled, sent=False),
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
    if not get_settings().public_signup_enabled:
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
    signup_keys = throttle_keys(
        "signup:" + signup_email, request.client.host if request.client else "unknown"
    )
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
