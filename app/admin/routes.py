from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.policy import require_admin
from app.auth.security import (
    active_device_count,
    credential_error,
    hash_credential,
    require_fresh_auth,
    verify_csrf,
)
from app.auth.service import create_invitation, validated_email
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.catalog.service import close_colour_warnings
from app.core.database import get_db
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import (
    Invitation,
    Person,
    RoleGrant,
    SignupRequest,
    TrustedDevice,
    User,
    UserPersonLink,
)
from app.web import context, templates

router = APIRouter(prefix="/admin")


def _admin(request: Request) -> None:
    require_admin(request.state.actor)


def _email_or_400(value: str) -> str:
    try:
        return validated_email(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    _admin(request)
    track_rows = list(
        db.execute(select(Track, Region.name).join(Region).order_by(Region.name, Track.name)).all()
    )
    users = list(db.scalars(select(User).order_by(User.display_name)))
    return templates.TemplateResponse(
        "admin.html",
        context(
            request,
            regions=list(db.scalars(select(Region).order_by(Region.name))),
            tracks=track_rows,
            colour_warnings=close_colour_warnings([row[0] for row in track_rows]),
            groups=list(db.scalars(select(CrewGroup).order_by(CrewGroup.name))),
            positions=list(
                db.execute(
                    select(BasePosition, CrewGroup.name)
                    .join(CrewGroup, isouter=True)
                    .order_by(BasePosition.name)
                ).all()
            ),
            people=list(db.scalars(select(Person).order_by(Person.display_name))),
            users=users,
            device_counts={user.id: active_device_count(db, user.id) for user in users},
            invitations=list(
                db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(50))
            ),
            signup_requests=list(
                db.scalars(
                    select(SignupRequest)
                    .where(SignupRequest.status == "PENDING")
                    .order_by(SignupRequest.created_at)
                )
            ),
            roles=[role.value for role in Role],
            invite_url=request.query_params.get("invite_url", ""),
        ),
    )


@router.post("/regions")
def create_region(
    request: Request, name: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)
):
    _admin(request)
    verify_csrf(request, csrf_token)
    region = Region(name=name.strip()[:100])
    db.add(region)
    db.flush()
    record_audit(db, "region.created", "region", region.id, request.state.user.id, region_id=region.id)
    db.commit()
    return RedirectResponse("/admin#regions", status_code=303)


@router.post("/tracks")
def create_track(
    request: Request,
    name: str = Form(...),
    region_id: uuid.UUID = Form(...),
    display_colour: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", display_colour):
        raise HTTPException(400, "Track colour must be a six-digit hex colour")
    track = Track(name=name.strip()[:120], region_id=region_id, display_colour=display_colour.upper())
    db.add(track)
    db.flush()
    record_audit(
        db,
        "track.created",
        "track",
        track.id,
        request.state.user.id,
        region_id=region_id,
        detail={"name": track.name, "colour": track.display_colour},
    )
    db.commit()
    return RedirectResponse("/admin#tracks", status_code=303)


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
    position = BasePosition(
        name=name.strip()[:100], crew_group_id=uuid.UUID(crew_group_id) if crew_group_id else None
    )
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
    person = Person(
        display_name=display_name.strip()[:120],
        email=_email_or_400(email) if email else None,
        home_region_id=uuid.UUID(home_region_id) if home_region_id else None,
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
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _admin(request)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    if role not in {item.value for item in Role}:
        raise HTTPException(400, "Invalid role")
    if error := credential_error(credential, role):
        raise HTTPException(400, error)
    scoped_region = uuid.UUID(region_id) if region_id else None
    if role != Role.ADMIN.value and scoped_region is None:
        raise HTTPException(400, "A region is required for this role")
    if role == Role.ADMIN.value and scoped_region is not None:
        raise HTTPException(400, "Admin is global and cannot carry a region scope")
    user = User(
        email=_email_or_400(email),
        display_name=display_name.strip()[:120],
        credential_hash=hash_credential(credential),
        credential_kind="pin" if credential.isdigit() else "password",
        credential_admin_eligible=not bool(credential_error(credential, Role.ADMIN.value)),
    )
    db.add(user)
    db.flush()
    if person_id:
        db.add(UserPersonLink(user_id=user.id, person_id=uuid.UUID(person_id)))
    db.add(
        RoleGrant(
            user_id=user.id, role=role, region_id=scoped_region, granted_by_user_id=request.state.user.id
        )
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
    return RedirectResponse("/admin#users", status_code=303)


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
    try:
        _, raw = create_invitation(
            db,
            email=email,
            display_name=display_name,
            person_id=uuid.UUID(person_id) if person_id else None,
            role=role,
            region_id=uuid.UUID(region_id) if region_id else None,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/admin?invite_url=/invite/{raw}#users", status_code=303)


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
    return RedirectResponse("/admin#users", status_code=303)


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
    return RedirectResponse("/admin#users", status_code=303)


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
    return RedirectResponse("/admin#invitations", status_code=303)


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
