from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.security import require_fresh_auth, verify_csrf
from app.auth.service import approve_signup, grant_role, revoke_role_grant
from app.catalog.models import Region
from app.core.database import get_db
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Person, RoleGrant, SignupRequest, User, UserPersonLink
from app.web import context, templates

router = APIRouter(prefix="/manage/accounts")


def _regions(request: Request, db: Session) -> list[Region]:
    actor = request.state.actor
    query = select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)
    if not actor.is_admin:
        managed = [
            region_id
            for region_id, roles in actor.regional_roles.items()
            if Role.MANAGER.value in roles
        ]
        if not managed:
            raise HTTPException(403, "Account administration authority required.")
        query = query.where(Region.id.in_(managed))
    return list(db.scalars(query))


@router.get("", response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    regions = _regions(request, db)
    region_ids = [region.id for region in regions]
    signup_query = select(SignupRequest).where(SignupRequest.status == "PENDING")
    if not request.state.actor.is_admin:
        signup_query = signup_query.where(SignupRequest.requested_region_id.in_(region_ids))
    grants_query = select(RoleGrant).where(RoleGrant.status != "REVOKED")
    if not request.state.actor.is_admin:
        grants_query = grants_query.where(
            RoleGrant.region_id.in_(region_ids), RoleGrant.role == Role.SUB_MANAGER.value
        )
    linked_people = select(UserPersonLink.person_id)
    return templates.TemplateResponse(
        "accounts.html",
        context(
            request,
            regions=regions,
            signup_requests=list(db.scalars(signup_query.order_by(SignupRequest.created_at))),
            people=list(
                db.scalars(
                    select(Person)
                    .where(
                        Person.lifecycle == "ACTIVE",
                        Person.id.not_in(linked_people),
                        or_(Person.home_region_id.in_(region_ids), Person.home_region_id.is_(None)),
                    )
                    .order_by(Person.display_name)
                )
            ),
            users=list(db.scalars(select(User).where(User.status == "ACTIVE").order_by(User.display_name))),
            grants=list(db.scalars(grants_query.order_by(RoleGrant.granted_at.desc()))),
            grant_roles=(
                [role.value for role in Role]
                if request.state.actor.is_admin
                else [Role.SUB_MANAGER.value]
            ),
            invite_url=request.query_params.get("invite_url", ""),
        ),
    )


@router.post("/signup-requests/{signup_id}/approve")
def approve_signup_request(
    signup_id: uuid.UUID,
    request: Request,
    role: str = Form(...),
    region_id: uuid.UUID = Form(...),
    person_action: str = Form(...),
    person_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _regions(request, db)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    signup = db.get(SignupRequest, signup_id)
    if not signup:
        raise HTTPException(404)
    try:
        raw = approve_signup(
            db,
            signup=signup,
            actor=request.state.actor,
            role=role,
            region_id=region_id,
            person_id=uuid.UUID(person_id) if person_id else None,
            create_person=person_action == "create",
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/accounts?invite_url=/invite/{raw}", status_code=303)


@router.post("/signup-requests/{signup_id}/reject")
def reject_signup_request(
    signup_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    regions = _regions(request, db)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    signup = db.get(SignupRequest, signup_id)
    if not signup or signup.status != "PENDING":
        raise HTTPException(409, "Signup request is no longer pending.")
    if not request.state.actor.is_admin and signup.requested_region_id not in {
        region.id for region in regions
    }:
        raise HTTPException(403)
    signup.status = "REJECTED"
    signup.reviewed_by_user_id = request.state.user.id
    signup.reviewed_at = utcnow()
    record_audit(db, "signup.rejected", "signup_request", signup.id, request.state.user.id)
    db.commit()
    return RedirectResponse("/manage/accounts", status_code=303)


@router.post("/grants")
def create_role_grant(
    request: Request,
    user_id: uuid.UUID = Form(...),
    role: str = Form(...),
    region_id: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _regions(request, db)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404)
    scoped_region = uuid.UUID(region_id) if region_id else None
    try:
        grant_role(
            db,
            actor=request.state.actor,
            target_user=user,
            role=role,
            region_id=scoped_region,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/manage/accounts#grants", status_code=303)


@router.post("/grants/{grant_id}/revoke")
def revoke_grant(
    grant_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _regions(request, db)
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    grant = db.get(RoleGrant, grant_id)
    if not grant:
        raise HTTPException(404)
    try:
        revoke_role_grant(db, actor=request.state.actor, grant=grant)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse("/manage/accounts#grants", status_code=303)
