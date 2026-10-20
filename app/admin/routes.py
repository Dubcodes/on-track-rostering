from __future__ import annotations

import os
import uuid
from datetime import date
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.data_export import safe_data_export
from app.audit.service import record_audit
from app.auth.links import credential_reset_url, invitation_url
from app.auth.policy import require_admin
from app.auth.security import (
    credential_error,
    hash_credential,
    require_fresh_auth,
    verify_csrf,
)
from app.auth.service import create_credential_reset, create_invitation, validated_email
from app.branding.service import (
    MAX_FAVICON_BYTES,
    favicon_asset,
    reset_custom_favicon,
    save_custom_favicon,
    update_branding,
)
from app.catalog.models import BasePosition, CrewGroup, Region
from app.core.config import get_settings
from app.core.database import get_db
from app.core.enums import Lifecycle, Role
from app.core.forms import controlled_integrity, optional_uuid
from app.core.time import local_today, utcnow
from app.identity.email import validate_person_work_email
from app.identity.models import (
    Invitation,
    Person,
    RoleGrant,
    SignupRequest,
    TrustedDevice,
    User,
    UserPersonLink,
)
from app.operations.heartbeat import scheduler_health
from app.operations.readiness import evaluate_readiness
from app.positions.canonical import validate_position_identity
from app.system_settings.service import update_operational_settings
from app.web import context, templates

router = APIRouter(prefix="/admin")


def _admin(request: Request) -> None:
    require_admin(request.state.actor)


def _email_or_400(value: str) -> str:
    try:
        return validated_email(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _name_or_400(value: str, label: str, maximum: int) -> str:
    clean = value.strip()
    if not 2 <= len(clean) <= maximum:
        raise HTTPException(400, f"{label} must be between 2 and {maximum} characters.")
    return clean


def _active_reference(db: Session, model, raw_id: str, label: str):  # type: ignore[no-untyped-def]
    reference_id = optional_uuid(raw_id, label)
    row = db.get(model, reference_id) if reference_id else None
    if not row or row.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, f"Select an active {label}.")
    return row


@router.get("", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    settings = get_settings()
    scheduler = scheduler_health(db, interval_seconds=settings.scheduler_interval_seconds)
    readiness = evaluate_readiness(db, settings=settings)
    favicon = favicon_asset()
    return templates.TemplateResponse(
        "admin.html",
        context(
            request,
            retention_days=settings.retention_days,
            retention_from_environment="ONTRACK_RETENTION_DAYS" in os.environ,
            scheduler_health=scheduler,
            readiness_checks=readiness.checks,
            readiness_ready=readiness.ready,
            favicon_is_custom=favicon.is_custom,
            favicon_dimensions=(
                f"{favicon.width}×{favicon.height}" if favicon.width and favicon.height else None
            ),
        ),
    )


@router.get("/download-data")
def download_data(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    payload = safe_data_export(db)
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ontrack-data-{local_today().isoformat()}.zip"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.post("/branding")
def update_system_branding(
    request: Request,
    product_name: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    previous_name = request.state.branding.product_name
    try:
        row = update_branding(
            db, product_name=product_name, actor_user_id=request.state.user.id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "system_branding.updated",
        "system_branding",
        row.id,
        request.state.user.id,
        detail={"previous_product_name": previous_name, "product_name": row.product_name},
    )
    db.commit()
    return RedirectResponse("/admin#branding", status_code=303)


@router.post("/branding/favicon")
def upload_system_favicon(
    request: Request,
    image: UploadFile = File(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    content = image.file.read(MAX_FAVICON_BYTES + 1)
    try:
        stored = save_custom_favicon(content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "system_branding.favicon_uploaded",
        "system_branding",
        1,
        request.state.user.id,
        detail={
            "width": stored.width,
            "height": stored.height,
            "bytes": stored.byte_count,
            "sha256": stored.digest,
        },
    )
    db.commit()
    return RedirectResponse("/admin#branding", status_code=303)


@router.post("/branding/favicon/reset")
def reset_system_favicon(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    had_custom_icon = reset_custom_favicon()
    record_audit(
        db,
        "system_branding.favicon_reset",
        "system_branding",
        1,
        request.state.user.id,
        detail={"had_custom_icon": had_custom_icon},
    )
    db.commit()
    return RedirectResponse("/admin#branding", status_code=303)


@router.post("/system-settings")
def update_system_settings(
    request: Request,
    public_signup_enabled: bool = Form(False),
    contractor_inactivity_days: int = Form(30),
    fortnight_anchor: date = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    previous = request.state.system_settings
    try:
        row = update_operational_settings(
            db,
            public_signup_enabled=public_signup_enabled,
            contractor_inactivity_days=contractor_inactivity_days,
            fortnight_anchor=fortnight_anchor,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    record_audit(
        db,
        "system_settings.updated",
        "system_settings",
        row.id,
        request.state.user.id,
        detail={
            "public_signup_enabled": row.public_signup_enabled,
            "previous_public_signup_enabled": previous.public_signup_enabled,
            "contractor_inactivity_days": row.contractor_inactivity_days,
            "previous_contractor_inactivity_days": previous.contractor_inactivity_days,
            "fortnight_anchor": row.fortnight_anchor.isoformat(),
            "previous_fortnight_anchor": previous.fortnight_anchor.isoformat(),
        },
    )
    db.commit()
    return RedirectResponse("/admin#system-settings", status_code=303)


@router.post("/positions")
def create_position(
    request: Request,
    name: str = Form(...),
    crew_group_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    group = _active_reference(db, CrewGroup, crew_group_id, "crew group") if crew_group_id else None
    clean_name = _name_or_400(name, "Position name", 100)
    try:
        identity = validate_position_identity(db, clean_name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    position = BasePosition(
        name=identity.canonical_name,
        crew_group_id=group.id if group else None,
    )
    with controlled_integrity(db, "A base position in that crew group already uses that name."):
        db.add(position)
        db.flush()
        record_audit(
            db,
            "position.created",
            "base_position",
            position.id,
            request.state.user.id,
            detail={"name": position.name},
        )
        db.commit()
    return RedirectResponse("/admin#positions", status_code=303)


@router.post("/people")
def create_person(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    home_region_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    region = _active_reference(db, Region, home_region_id, "home region") if home_region_id else None
    try:
        person_email = validate_person_work_email(db, email)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    person = Person(
        display_name=_name_or_400(display_name, "Person name", 120),
        email=person_email,
        home_region_id=region.id if region else None,
    )
    db.add(person)
    db.flush()
    record_audit(
        db,
        "person.created",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"display_name": person.display_name},
    )
    db.commit()
    return RedirectResponse("/admin#people", status_code=303)


@router.post("/users")
def create_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    credential: str = Form(...),
    role: str = Form(...),
    region_id: str = Form(""),
    person_id: str = Form(""),
    return_person_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    form_state = {
        "display_name": display_name,
        "email": email,
        "role": role,
        "region_id": region_id,
        "person_id": person_id,
    }
    errors: dict[str, str] = {}
    valid_roles = {item.value for item in Role}
    if role not in valid_roles:
        errors["role"] = "Choose a valid account role."
    elif error := credential_error(credential, role):
        errors["credential"] = error
    try:
        scoped_region = optional_uuid(region_id, "region")
    except HTTPException as exc:
        scoped_region = None
        errors["region_id"] = str(exc.detail)
    if role in valid_roles and role != Role.ADMIN.value and scoped_region is None:
        errors["region_id"] = "Choose a Region for this role."
    if role == Role.ADMIN.value and scoped_region is not None:
        errors["region_id"] = "Admin is global and cannot carry a Region."
    if scoped_region:
        region = db.get(Region, scoped_region)
        if not region or region.lifecycle != Lifecycle.ACTIVE.value:
            errors["region_id"] = "Select an active Region."
    try:
        user_email = validated_email(email)
    except ValueError as exc:
        user_email = ""
        errors["email"] = str(exc)
    if user_email and db.scalar(select(User.id).where(User.email == user_email)):
        errors["email"] = "An account already uses that email."
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        errors["display_name"] = "Account name must be between 2 and 120 characters."
    person = None
    if person_id:
        try:
            person = _active_reference(db, Person, person_id, "crew identity")
        except HTTPException as exc:
            errors["person_id"] = str(exc.detail)
    if role == Role.CONTRACTOR.value and person is None:
        errors["person_id"] = "Contractor access must be linked to a crew identity."
    if person:
        try:
            person_email = validate_person_work_email(db, person.email, person_id=person.id)
        except ValueError as exc:
            person_email = ""
            errors["person_id"] = str(exc)
        if person_email and user_email and person_email != user_email:
            errors["email"] = "The account email must match the crew identity's work email."
    if person and db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)):
        errors["person_id"] = "That crew identity is already linked to an account."
    if errors:
        if return_person_id and return_person_id == person_id and person:
            from app.crew.routes import crew_person_response

            return crew_person_response(
                person.id,
                request,
                db,
                direct_account=form_state,
                direct_errors=errors,
                status_code=422,
            )
        from app.accounts.routes import accounts_response

        return accounts_response(
            request,
            db,
            direct_account=form_state,
            direct_errors=errors,
            status_code=422,
        )
    require_fresh_auth(request)
    user = User(
        email=user_email,
        display_name=clean_name,
        credential_hash=hash_credential(credential),
        credential_kind="pin" if credential.isdigit() else "password",
        credential_admin_eligible=not bool(credential_error(credential, Role.ADMIN.value)),
    )
    with controlled_integrity(db, "That email or crew identity is already assigned to an account."):
        db.add(user)
        db.flush()
        if person:
            db.add(UserPersonLink(user_id=user.id, person_id=person.id))
        db.add(
            RoleGrant(
                user_id=user.id, role=role, region_id=scoped_region, granted_by_user_id=request.state.user.id
            )
        )
        if role == Role.CONTRACTOR.value:
            from app.identity.contractor_access import refresh_contractor_access

            refresh_contractor_access(
                db,
                user,
                manual_extension_at=utcnow(),
                actor_user_id=request.state.user.id,
                reason="direct account creation",
            )
        record_audit(
            db,
            "user.created",
            "user",
            user.id,
            request.state.user.id,
            region_id=scoped_region,
            detail={"role": role},
        )
        db.commit()
    if person:
        return RedirectResponse(
            f"/manage/crew/{person.id}?account=created#account", status_code=303
        )
    return RedirectResponse(f"/manage/accounts?created_user={user.id}#accounts", status_code=303)


@router.post("/invitations")
def invite_user(
    request: Request,
    email: str = Form(...),
    display_name: str = Form(...),
    role: str = Form(...),
    region_id: str = Form(""),
    person_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    form_state = {
        "display_name": display_name,
        "email": email,
        "role": role,
        "region_id": region_id,
        "person_id": person_id,
    }
    try:
        _, raw = create_invitation(
            db,
            email=email,
            display_name=display_name,
            person_id=optional_uuid(person_id, "crew identity"),
            role=role,
            region_id=optional_uuid(region_id, "region"),
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        db.rollback()
        from app.accounts.routes import accounts_response

        return accounts_response(
            request,
            db,
            invitation_form=form_state,
            invitation_errors={"form": str(exc)},
            status_code=422,
        )
    return templates.TemplateResponse(
        "invitation_created.html",
        context(
            request,
            invitation_url=invitation_url(raw),
            destination="/manage/accounts#invitations",
            destination_label="Return to invitations",
            invitation_kind="Account setup invitation",
            recipient_name=display_name,
            recipient_email=email,
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/users/{user_id}/status")
def update_user_status(
    user_id: uuid.UUID,
    request: Request,
    account_status: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    if user_id == request.state.user.id:
        raise HTTPException(400, "You cannot disable your own active Admin session.")
    if account_status not in {"ACTIVE", "DISABLED"}:
        raise HTTPException(400, "Invalid account status.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    if account_status == "DISABLED":
        target_is_admin = db.scalar(
            select(RoleGrant.id).where(
                RoleGrant.user_id == user.id,
                RoleGrant.role == Role.ADMIN.value,
                RoleGrant.status == "ACTIVE",
            ).limit(1)
        )
        another_admin = db.scalar(
            select(RoleGrant.id)
            .join(User, User.id == RoleGrant.user_id)
            .where(
                RoleGrant.user_id != user.id,
                RoleGrant.role == Role.ADMIN.value,
                RoleGrant.status == "ACTIVE",
                User.status == "ACTIVE",
            )
            .limit(1)
        )
        if target_is_admin and not another_admin:
            raise HTTPException(409, "The final active Admin account cannot be disabled.")
    user.status = account_status
    user.auth_epoch += 1
    now = utcnow()
    if account_status == "ACTIVE":
        from app.identity.contractor_access import is_contractor_user, refresh_contractor_access

        if is_contractor_user(db, user.id):
            refresh_contractor_access(
                db,
                user,
                manual_extension_at=now,
                actor_user_id=request.state.user.id,
                reason="admin activation",
            )
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None)
        )
    ):
        device.revoked_at = now
    record_audit(
        db,
        "user.status.updated",
        "user",
        user.id,
        request.state.user.id,
        detail={"status": account_status},
    )
    db.commit()
    return RedirectResponse("/manage/accounts#accounts", status_code=303)


@router.post("/users/{user_id}/credential-reset")
def issue_credential_reset(
    user_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    if user_id == request.state.user.id:
        raise HTTPException(400, "Use Settings to change your own credential.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    try:
        _reset, raw = create_credential_reset(
            db,
            user=user,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return templates.TemplateResponse(
        "credential_reset_created.html",
        context(
            request,
            credential_reset_url=credential_reset_url(raw),
            target_name=user.display_name,
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/users/{user_id}/devices/revoke")
def revoke_user_devices(
    user_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    now = utcnow()
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None)
        )
    ):
        device.revoked_at = now
    record_audit(db, "user.devices.revoked", "user", user.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/manage/accounts#accounts", status_code=303)


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    invitation = db.get(Invitation, invitation_id)
    if not invitation or invitation.consumed_at:
        raise HTTPException(409, "Invitation is unavailable.")
    invitation.revoked_at = utcnow()
    record_audit(
        db, "invitation.revoked", "invitation", invitation.id, request.state.user.id
    )
    db.commit()
    return RedirectResponse("/manage/accounts#invitations", status_code=303)


@router.post("/signup-requests/{signup_id}/reject")
def reject_signup(
    signup_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    signup = db.get(SignupRequest, signup_id)
    if not signup or signup.status != "PENDING":
        raise HTTPException(409, "Signup request is no longer pending.")
    signup.status = "REJECTED"
    record_audit(db, "signup.rejected", "signup_request", signup.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/admin#signup-requests", status_code=303)
