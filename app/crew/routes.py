from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.accounts.onboarding import onboarding_state
from app.audit.models import AuditEvent
from app.audit.service import record_audit
from app.auth.links import credential_reset_url, invitation_url
from app.auth.policy import (
    can_administer_region,
    can_administer_user,
    can_assist_user,
    can_grant_role,
    can_manage_region,
)
from app.auth.security import require_fresh_auth, verify_csrf
from app.auth.service import (
    create_credential_reset,
    create_invitation,
    grant_role,
    revoke_role_grant,
    validated_email,
)
from app.catalog.models import BasePosition, CrewGroup, PersonCrewGroup, Region
from app.core.database import get_db
from app.core.enums import CapabilitySignal, Lifecycle, Role
from app.core.time import utcnow
from app.identity.models import Invitation, Person, RoleGrant, TrustedDevice, User, UserPersonLink
from app.notices.models import NoticePublishingScope, NoticePublishingScopeRegion
from app.notices.service import set_notice_publishing_scope
from app.positions.canonical import canonical_position_identity
from app.positions.service import set_preference_signal
from app.rostering.models import PositionCapability
from app.web import context, templates

router = APIRouter(prefix="/manage/crew")


def _regions(db: Session, request: Request) -> list[Region]:
    rows = list(db.scalars(select(Region).where(Region.lifecycle == "ACTIVE").order_by(Region.name)))
    return [row for row in rows if can_manage_region(request.state.actor, row.id)]


def _person_in_operational_scope(db: Session, request: Request, person_id: uuid.UUID) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    if person.home_region_id is None:
        if request.state.actor.is_admin:
            return person
        raise HTTPException(404)
    if not can_manage_region(request.state.actor, person.home_region_id):
        raise HTTPException(403, "Regional roster authority required.")
    return person


def _person_in_scope(
    db: Session,
    request: Request,
    person_id: uuid.UUID,
    *,
    allow_unassigned_admin: bool = False,
) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404)
    if person.home_region_id is None:
        if allow_unassigned_admin and request.state.actor.is_admin:
            return person
        raise HTTPException(404)
    if not can_administer_region(request.state.actor, person.home_region_id):
        raise HTTPException(403, "Regional administration authority required.")
    return person


@router.get("", response_class=HTMLResponse)
def crew_management(
    request: Request,
    region_id: uuid.UUID | None = None,
    q: str = "",
    account_type: str = "",
    crew_group_id: uuid.UUID | None = None,
    position_id: uuid.UUID | None = None,
    show_archived: bool = False,
    db: Session = Depends(get_db),
):
    regions = _regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    selected = next((region for region in regions if region.id == region_id), regions[0])
    statement = select(Person).where(Person.home_region_id == selected.id)
    if not show_archived:
        statement = statement.where(Person.lifecycle == Lifecycle.ACTIVE.value)
    if q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(or_(Person.display_name.ilike(term), Person.email.ilike(term)))
    if account_type:
        if account_type == "NONE":
            statement = statement.where(
                Person.id.not_in(select(UserPersonLink.person_id))
            )
        elif account_type in {role.value for role in Role}:
            statement = statement.where(
                Person.id.in_(
                    select(UserPersonLink.person_id)
                    .join(RoleGrant, RoleGrant.user_id == UserPersonLink.user_id)
                    .where(RoleGrant.role == account_type, RoleGrant.status == "ACTIVE")
                )
            )
    if crew_group_id:
        statement = statement.where(
            Person.id.in_(
                select(PersonCrewGroup.person_id).where(
                    PersonCrewGroup.crew_group_id == crew_group_id
                )
            )
        )
    if position_id:
        statement = statement.where(
            Person.id.in_(
                select(PositionCapability.person_id).where(
                    PositionCapability.base_position_id == position_id,
                    PositionCapability.signal.in_([
                        CapabilitySignal.WORKED.value,
                        CapabilitySignal.EMPLOYEE_ALLOW.value,
                        CapabilitySignal.MANAGER_ALLOW.value,
                    ]),
                )
            )
        )
    people = list(db.scalars(statement.order_by(Person.display_name)))
    needs_setup_statement = (
        select(Person)
        .outerjoin(UserPersonLink, UserPersonLink.person_id == Person.id)
        .where(
            Person.lifecycle == Lifecycle.ACTIVE.value,
            UserPersonLink.person_id.is_(None),
            or_(Person.home_region_id.is_(None), Person.email.is_(None)),
        )
    )
    if q.strip():
        needs_setup_statement = needs_setup_statement.where(
            or_(Person.display_name.ilike(term), Person.email.ilike(term))
        )
    needs_setup_people = (
        list(db.scalars(needs_setup_statement.order_by(Person.display_name)))
        if request.state.actor.is_admin
        else []
    )
    visible_people = [*people, *needs_setup_people]
    person_ids = [person.id for person in visible_people]
    memberships: dict[uuid.UUID, set[uuid.UUID]] = {person.id: set() for person in visible_people}
    directory_summaries: dict[uuid.UUID, dict[str, object]] = {}
    if person_ids:
        for person_id, group_id in db.execute(
            select(PersonCrewGroup.person_id, PersonCrewGroup.crew_group_id).where(
                PersonCrewGroup.person_id.in_(person_ids)
            )
        ):
            memberships[person_id].add(group_id)
        accounts = {
            person_id: account
            for person_id, account in db.execute(
                select(UserPersonLink.person_id, User)
                .join(User, User.id == UserPersonLink.user_id)
                .where(UserPersonLink.person_id.in_(person_ids))
            )
        }
        account_ids = [account.id for account in accounts.values()]
        roles_by_user: dict[uuid.UUID, set[str]] = {account_id: set() for account_id in account_ids}
        last_seen_by_user: dict[uuid.UUID, object] = {}
        if account_ids:
            for user_id, role in db.execute(
                select(RoleGrant.user_id, RoleGrant.role).where(
                    RoleGrant.user_id.in_(account_ids), RoleGrant.status == "ACTIVE"
                )
            ):
                roles_by_user[user_id].add(role)
            last_seen_by_user = dict(
                db.execute(
                    select(TrustedDevice.user_id, func.max(TrustedDevice.last_seen_at))
                    .where(
                        TrustedDevice.user_id.in_(account_ids),
                        TrustedDevice.revoked_at.is_(None),
                    )
                    .group_by(TrustedDevice.user_id)
                ).all()
            )
        latest_invitation: dict[uuid.UUID, Invitation] = {}
        for invitation in db.scalars(
            select(Invitation)
            .where(
                Invitation.person_id.in_(person_ids),
                Invitation.consumed_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .order_by(Invitation.created_at)
        ):
            if invitation.person_id:
                latest_invitation[invitation.person_id] = invitation
        now = utcnow()
        for person in visible_people:
            account = accounts.get(person.id)
            roles = roles_by_user.get(account.id, set()) if account else set()
            if account:
                setup = f"{account.status.title()} account"
            elif person.home_region_id is None:
                setup = "Needs Primary Region"
            elif not person.email:
                setup = "Needs work email"
            elif invitation := latest_invitation.get(person.id):
                expires_at = invitation.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                setup = "Invitation pending" if expires_at > now else "Invitation expired"
            else:
                setup = "Ready for setup"
            directory_summaries[person.id] = {
                "setup": setup,
                "roles": ", ".join(
                    sorted(role.replace("_", " ").title() for role in roles)
                ),
                "last_seen": last_seen_by_user.get(account.id) if account else None,
                "contractor": Role.CONTRACTOR.value in roles,
                "contractor_expires_at": (
                    account.contractor_access_expires_at if account else None
                ),
                "email_mismatch": bool(
                    account and (person.email or "").casefold() != account.email.casefold()
                ),
            }
    return templates.TemplateResponse(
        "crew_management.html",
        context(
            request,
            regions=regions,
            selected_region=selected,
            people=people,
            needs_setup_people=needs_setup_people,
            memberships=memberships,
            directory_summaries=directory_summaries,
            region_names={region.id: region.name for region in regions},
            query=q,
            account_type=account_type,
            selected_crew_group_id=crew_group_id,
            selected_position_id=position_id,
            filter_account_types=[role.value for role in Role],
            filter_groups=list(db.scalars(select(CrewGroup).where(CrewGroup.lifecycle == "ACTIVE").order_by(CrewGroup.name))),
            filter_positions=list(db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name))),
            show_archived=show_archived,
        ),
    )


@router.get("/{person_id}", response_class=HTMLResponse)
def crew_person_detail(
    person_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    person = _person_in_operational_scope(db, request, person_id)
    regions = _regions(db, request)
    groups = list(
        db.scalars(select(CrewGroup).where(CrewGroup.lifecycle == "ACTIVE").order_by(CrewGroup.name))
    )
    positions = list(
        db.scalars(
            select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name)
        )
    )
    capability_rows = list(
        db.scalars(select(PositionCapability).where(PositionCapability.person_id == person.id))
    )
    identity_counts: dict[str, int] = {}
    for position in positions:
        key = canonical_position_identity(position.name).key
        identity_counts[key] = identity_counts.get(key, 0) + 1
    memberships = set(
        db.scalars(
            select(PersonCrewGroup.crew_group_id).where(PersonCrewGroup.person_id == person.id)
        )
    )
    account = db.scalar(
        select(User).join(UserPersonLink).where(UserPersonLink.person_id == person.id)
    )
    grants = list(
        db.scalars(
            select(RoleGrant)
            .where(RoleGrant.user_id == account.id, RoleGrant.status != "REVOKED")
            .order_by(RoleGrant.granted_at)
        )
    ) if account else []
    devices = list(
        db.scalars(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == account.id)
            .order_by(TrustedDevice.last_seen_at.desc())
        )
    ) if account else []
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.person_id == person.id)
        .order_by(Invitation.created_at.desc())
        .limit(1)
    )
    notice_override = db.get(NoticePublishingScope, account.id) if account else None
    notice_scope_ids = set(
        db.scalars(
            select(NoticePublishingScopeRegion.region_id).where(
                NoticePublishingScopeRegion.user_id == account.id
            )
        )
    ) if account and notice_override else set()
    history = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id.in_([str(person.id), str(account.id) if account else ""]))
            .order_by(AuditEvent.occurred_at.desc())
            .limit(30)
        )
    )
    setup_state = onboarding_state(db, person)
    setup_label = {
        "NEEDS_REGION": "Needs Primary Region",
        "NEEDS_EMAIL": "Needs work email",
        "READY_TO_INVITE": "Ready for setup",
        "INVITATION_PENDING": "Invitation pending",
        "INVITATION_EXPIRED": "Invitation expired",
        "ACCOUNT_LINKED": (
            "Active account" if account and account.status == "ACTIVE" else "Disabled account"
        ),
        "ARCHIVED": "Archived",
    }[setup_state]
    if account and (person.email or "").casefold() != account.email.casefold():
        setup_label += " · Work/login email mismatch"
    return templates.TemplateResponse(
        "crew_person.html",
        context(
            request,
            person=person,
            regions=regions,
            groups=groups,
            positions=positions,
            memberships=memberships,
            capability_rows=capability_rows,
            capability_positions={position.id: position for position in positions},
            available_positions=positions,
            ambiguous_position_keys={key for key, count in identity_counts.items() if count > 1},
            ambiguous_position_ids={
                position.id
                for position in positions
                if identity_counts[canonical_position_identity(position.name).key] > 1
            },
            account=account,
            grants=grants,
            devices=devices,
            last_seen=devices[0].last_seen_at if devices else None,
            setup_state=setup_state,
            setup_label=setup_label,
            invitation=invitation,
            history=history,
            notice_override=notice_override,
            notice_scope_ids=notice_scope_ids,
            can_control_account=bool(
                account
                and person.home_region_id
                and can_administer_user(db, request.state.actor, account, person.home_region_id)
            ),
            can_assist_account=bool(
                account
                and person.home_region_id
                and can_assist_user(db, request.state.actor, account, person.home_region_id)
            ),
        ),
    )


@router.post("/{person_id}")
def save_crew_person(
    person_id: uuid.UUID,
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    home_region_id: str = Form(""),
    truck_driver_qualified: bool = Form(False),
    group_ids: list[uuid.UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id, allow_unassigned_admin=True)
    destination = None
    if home_region_id.strip():
        try:
            destination_id = uuid.UUID(home_region_id)
        except ValueError as exc:
            raise HTTPException(400, "Choose a valid Primary Region.") from exc
        destination = db.get(Region, destination_id)
        if not destination or destination.lifecycle != "ACTIVE" or not can_administer_region(
            request.state.actor, destination.id
        ):
            raise HTTPException(403, "Primary Region is outside your authority.")
    elif not request.state.actor.is_admin:
        raise HTTPException(403, "Admin authority is required to leave Primary Region blank.")
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Enter a crew name between 2 and 120 characters.")
    try:
        clean_email = validated_email(email) if email.strip() else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    allowed_groups = set(
        db.scalars(
            select(CrewGroup.id).where(
                CrewGroup.id.in_(group_ids), CrewGroup.lifecycle == "ACTIVE"
            )
        )
    ) if group_ids else set()
    if len(allowed_groups) != len(set(group_ids)):
        raise HTTPException(400, "Select active Crew groups only.")
    person.display_name = clean_name
    person.email = clean_email
    person.home_region_id = destination.id if destination else None
    person.truck_driver_qualified = truck_driver_qualified
    db.execute(delete(PersonCrewGroup).where(PersonCrewGroup.person_id == person.id))
    db.add_all(
        PersonCrewGroup(person_id=person.id, crew_group_id=group_id)
        for group_id in allowed_groups
    )
    record_audit(
        db, "person.management.updated", "person", person.id, request.state.user.id,
        region_id=destination.id if destination else None,
        detail={
            "group_count": len(allowed_groups),
            "truck_driver_qualified": person.truck_driver_qualified,
        },
    )
    db.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "saved"})
    return RedirectResponse(f"/manage/crew/{person.id}?saved=1", status_code=303)


@router.post("/onboarding/metadata")
def update_onboarding_metadata(
    request: Request,
    person_ids: list[uuid.UUID] = Form(default=[]),
    emails: list[str] = Form(default=[]),
    region_ids: list[str] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not request.state.actor.is_admin:
        raise HTTPException(403, "Admin authority required.")
    verify_csrf(request, csrf_token)
    if not person_ids or not (len(person_ids) == len(emails) == len(region_ids)):
        raise HTTPException(400, "Submit complete edited Person rows.")
    if len(set(person_ids)) != len(person_ids):
        raise HTTPException(400, "Each Person UUID may appear only once.")
    people = {row.id: row for row in db.scalars(select(Person).where(Person.id.in_(person_ids)))}
    parsed_region_ids: list[uuid.UUID | None] = []
    for raw_region_id in region_ids:
        try:
            parsed_region_ids.append(uuid.UUID(raw_region_id) if raw_region_id.strip() else None)
        except ValueError as exc:
            raise HTTPException(400, "Use valid Primary Region identifiers.") from exc
    selected_region_ids = {region_id for region_id in parsed_region_ids if region_id}
    regions = {row.id: row for row in db.scalars(
        select(Region).where(Region.id.in_(selected_region_ids), Region.lifecycle == "ACTIVE")
    )}
    if len(people) != len(person_ids) or len(regions) != len(selected_region_ids):
        raise HTTPException(400, "Use active exact Person and Primary Region identifiers.")
    normalized: list[str | None] = []
    for person_id, email in zip(person_ids, emails, strict=True):
        person = people[person_id]
        if person.lifecycle != Lifecycle.ACTIVE.value or db.scalar(
            select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)
        ):
            raise HTTPException(409, "Batch metadata is limited to active unlinked People.")
        try:
            clean_email = validated_email(email) if email.strip() else None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if clean_email and (
            db.scalar(
                select(Person.id).where(
                    func.lower(Person.email) == clean_email,
                    Person.id != person.id,
                ).limit(1)
            )
            or db.scalar(select(User.id).where(func.lower(User.email) == clean_email).limit(1))
        ):
            raise HTTPException(409, f"Work email for {person.display_name} is already in use.")
        normalized.append(clean_email)
    supplied_emails = [email for email in normalized if email is not None]
    if len(set(supplied_emails)) != len(supplied_emails):
        raise HTTPException(409, "Work emails must be unique within the batch.")
    changed_count = 0
    for person_id, clean_email, region_id in zip(
        person_ids, normalized, parsed_region_ids, strict=True
    ):
        person = people[person_id]
        new_region_id = regions[region_id].id if region_id else None
        if person.email == clean_email and person.home_region_id == new_region_id:
            continue
        person.email = clean_email
        person.home_region_id = new_region_id
        record_audit(
            db,
            "person.onboarding_metadata.updated",
            "person",
            person.id,
            request.state.user.id,
            region_id=new_region_id,
        )
        changed_count += 1
    db.commit()
    return RedirectResponse(f"/manage/crew?metadata={changed_count}", status_code=303)


def _controlled_account(
    db: Session, request: Request, person_id: uuid.UUID, *, allow_operational_assist: bool = False
) -> tuple[Person, User]:
    person = (
        _person_in_operational_scope(db, request, person_id)
        if allow_operational_assist
        else _person_in_scope(db, request, person_id)
    )
    account = db.scalar(
        select(User).join(UserPersonLink).where(UserPersonLink.person_id == person.id)
    )
    if (
        not account
        or person.home_region_id is None
        or not (
            can_assist_user(db, request.state.actor, account, person.home_region_id)
            if allow_operational_assist
            else can_administer_user(db, request.state.actor, account, person.home_region_id)
        )
    ):
        raise HTTPException(403, "This account requires higher authority.")
    return person, account


@router.post("/{person_id}/setup-link", response_class=HTMLResponse)
def create_person_setup_link(
    person_id: uuid.UUID,
    request: Request,
    role: str = Form(Role.EMPLOYEE.value),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    person = _person_in_scope(db, request, person_id, allow_unassigned_admin=True)
    if role not in {Role.EMPLOYEE.value, Role.CASUAL.value, Role.CONTRACTOR.value, Role.SUB_MANAGER.value}:
        raise HTTPException(403, "That account role is not available here.")
    if not person.email or person.home_region_id is None:
        raise HTTPException(409, "Work email and Primary Region are required.")
    if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)):
        raise HTTPException(409, "This Person already has an account.")
    now = utcnow()
    for pending in db.scalars(
        select(Invitation).where(
            Invitation.person_id == person.id,
            Invitation.consumed_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    ):
        pending.revoked_at = now
    try:
        _invitation, raw = create_invitation(
            db,
            email=person.email,
            display_name=person.display_name,
            person_id=person.id,
            role=role,
            region_id=person.home_region_id,
            actor_user_id=request.state.user.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return templates.TemplateResponse(
        "invitation_created.html",
        context(
            request,
            invitation_url=invitation_url(raw),
            destination=f"/manage/crew/{person.id}",
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{person_id}/account-status")
def update_person_account_status(
    person_id: uuid.UUID,
    request: Request,
    account_status: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    _person, account = _controlled_account(
        db, request, person_id, allow_operational_assist=True
    )
    if account.id == request.state.user.id or account_status not in {"ACTIVE", "DISABLED"}:
        raise HTTPException(400, "Invalid account status change.")
    account.status = account_status
    account.auth_epoch += 1
    now = utcnow()
    if account_status == "ACTIVE":
        from app.identity.contractor_access import is_contractor_user, refresh_contractor_access

        if is_contractor_user(db, account.id):
            refresh_contractor_access(
                db,
                account,
                manual_extension_at=now,
                actor_user_id=request.state.user.id,
                reason="scoped management activation",
            )
    for device in db.scalars(
        select(TrustedDevice).where(
            TrustedDevice.user_id == account.id, TrustedDevice.revoked_at.is_(None)
        )
    ):
        device.revoked_at = now
    record_audit(
        db, "user.status.updated", "user", account.id, request.state.user.id,
        detail={"status": account_status},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew/{person_id}#account", status_code=303)


@router.post("/{person_id}/employment-type")
def update_person_employment_type(
    person_id: uuid.UUID,
    request: Request,
    role: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Atomically replace an ordinary Employee/Casual classification."""
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    person, account = _controlled_account(db, request, person_id)
    if role not in {Role.EMPLOYEE.value, Role.CASUAL.value}:
        raise HTTPException(400, "Choose Employee or Casual.")
    if person.home_region_id is None or not can_grant_role(
        request.state.actor, role, person.home_region_id
    ):
        raise HTTPException(403, "You cannot change that account classification.")
    grants = list(
        db.scalars(
            select(RoleGrant).where(
                RoleGrant.user_id == account.id,
                RoleGrant.status != "REVOKED",
            )
        )
    )
    if any(grant.role == Role.CONTRACTOR.value for grant in grants):
        raise HTTPException(409, "Use the Contractor lifecycle controls for this account.")
    ordinary = [
        grant
        for grant in grants
        if grant.region_id == person.home_region_id
        and grant.role in {Role.EMPLOYEE.value, Role.CASUAL.value}
    ]
    if any(grant.role == role for grant in ordinary):
        return RedirectResponse(f"/manage/crew/{person.id}#account", status_code=303)
    now = utcnow()
    previous = [grant.role for grant in ordinary]
    active_replaced = any(grant.status == "ACTIVE" for grant in ordinary)
    for grant in ordinary:
        grant.status = "REVOKED"
        grant.revoked_at = now
    replacement = RoleGrant(
        user_id=account.id,
        role=role,
        region_id=person.home_region_id,
        status="PENDING",
        granted_by_user_id=request.state.user.id,
    )
    db.add(replacement)
    if active_replaced:
        account.auth_epoch += 1
        for device in db.scalars(
            select(TrustedDevice).where(
                TrustedDevice.user_id == account.id,
                TrustedDevice.revoked_at.is_(None),
            )
        ):
            device.revoked_at = now
    record_audit(
        db,
        "user.employment_type.changed",
        "user",
        account.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"previous": previous, "new": role, "status": "PENDING"},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew/{person.id}?employment=pending#account", status_code=303)


@router.post("/{person_id}/setup-link/revoke")
def revoke_person_setup_link(
    person_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    person = _person_in_scope(db, request, person_id)
    invitation = db.scalar(
        select(Invitation)
        .where(
            Invitation.person_id == person.id,
            Invitation.consumed_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc())
        .limit(1)
    )
    if not invitation:
        raise HTTPException(409, "No pending setup invitation remains to revoke.")
    invitation.revoked_at = utcnow()
    record_audit(
        db,
        "invitation.revoked",
        "invitation",
        invitation.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"person_id": str(person.id)},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew/{person.id}#account", status_code=303)


@router.post("/{person_id}/credential-reset", response_class=HTMLResponse)
def reset_person_credential(
    person_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    _person, account = _controlled_account(
        db, request, person_id, allow_operational_assist=True
    )
    if account.id == request.state.user.id:
        raise HTTPException(400, "Use Settings to change your own credential.")
    try:
        _reset, raw = create_credential_reset(
            db, user=account, actor_user_id=request.state.user.id
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return templates.TemplateResponse(
        "credential_reset_created.html",
        context(
            request,
            credential_reset_url=credential_reset_url(raw),
            target_name=account.display_name,
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{person_id}/devices/revoke")
def revoke_person_devices(
    person_id: uuid.UUID,
    request: Request,
    device_id: uuid.UUID | None = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    _person, account = _controlled_account(db, request, person_id)
    query = select(TrustedDevice).where(
        TrustedDevice.user_id == account.id, TrustedDevice.revoked_at.is_(None)
    )
    if device_id:
        query = query.where(TrustedDevice.id == device_id)
    now = utcnow()
    for device in db.scalars(query):
        device.revoked_at = now
    record_audit(db, "user.devices.revoked", "user", account.id, request.state.user.id)
    db.commit()
    return RedirectResponse(f"/manage/crew/{person_id}#security", status_code=303)


@router.post("/{person_id}/sub-manager")
def update_person_sub_manager(
    person_id: uuid.UUID,
    request: Request,
    enabled: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    person, account = _controlled_account(db, request, person_id)
    region_id = person.home_region_id
    grant = db.scalar(
        select(RoleGrant).where(
            RoleGrant.user_id == account.id,
            RoleGrant.region_id == region_id,
            RoleGrant.role == Role.SUB_MANAGER.value,
            RoleGrant.status != "REVOKED",
        )
    )
    try:
        if enabled and not grant:
            grant_role(
                db,
                actor=request.state.actor,
                target_user=account,
                role=Role.SUB_MANAGER.value,
                region_id=region_id,
            )
        elif not enabled and grant:
            revoke_role_grant(db, actor=request.state.actor, grant=grant)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/crew/{person_id}#account", status_code=303)


@router.post("/{person_id}/notice-scope")
def update_person_notice_scope(
    person_id: uuid.UUID,
    request: Request,
    region_ids: list[uuid.UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not request.state.actor.is_admin:
        raise HTTPException(403, "Admin authority required.")
    verify_csrf(request, csrf_token)
    require_fresh_auth(request)
    person = _person_in_scope(db, request, person_id, allow_unassigned_admin=True)
    account = db.scalar(
        select(User).join(UserPersonLink).where(UserPersonLink.person_id == person.id)
    )
    if not account:
        raise HTTPException(409, "This Person has no linked account.")
    if not db.scalar(
        select(RoleGrant.id).where(
            RoleGrant.user_id == account.id,
            RoleGrant.status == "ACTIVE",
            RoleGrant.role.in_([Role.MANAGER.value, Role.SUB_MANAGER.value]),
        ).limit(1)
    ):
        raise HTTPException(409, "Notice scope applies only to Managers and Sub-managers.")
    selected = set(region_ids)
    active = set(
        db.scalars(
            select(Region.id).where(Region.id.in_(selected), Region.lifecycle == "ACTIVE")
        )
    ) if selected else set()
    if active != selected:
        raise HTTPException(400, "Select active Regions only.")
    set_notice_publishing_scope(
        db,
        user_id=account.id,
        region_ids=selected,
        configured_by_user_id=request.state.user.id,
    )
    db.commit()
    return RedirectResponse(f"/manage/crew/{person_id}#notice-scope", status_code=303)
@router.post("")
def create_crew_member(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    region_id: uuid.UUID = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    region = db.get(Region, region_id)
    if not region or region.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active region.")
    if not can_administer_region(request.state.actor, region_id):
        raise HTTPException(403, "Regional administration authority required.")
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Enter a crew name between 2 and 120 characters.")
    try:
        clean_email = validated_email(email) if email.strip() else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    person = Person(display_name=clean_name, email=clean_email, home_region_id=region_id)
    db.add(person)
    db.flush()
    record_audit(
        db,
        "person.created",
        "person",
        person.id,
        request.state.user.id,
        region_id=region_id,
        detail={"display_name": clean_name},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={region_id}", status_code=303)


@router.post("/{person_id}/profile")
def update_crew_profile(
    person_id: uuid.UUID,
    request: Request,
    display_name: str = Form(...),
    email: str = Form(""),
    home_region_id: uuid.UUID = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id, allow_unassigned_admin=True)
    destination = db.get(Region, home_region_id)
    if not destination or destination.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(400, "Select an active Primary region.")
    if not can_administer_region(request.state.actor, destination.id):
        raise HTTPException(403, "Regional administration authority required.")
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        raise HTTPException(400, "Enter a crew name between 2 and 120 characters.")
    try:
        clean_email = validated_email(email) if email.strip() else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    previous = {
        "display_name": person.display_name,
        "email": person.email,
        "home_region_id": str(person.home_region_id) if person.home_region_id else None,
    }
    person.display_name = clean_name
    person.email = clean_email
    person.home_region_id = destination.id
    record_audit(
        db,
        "person.profile.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=destination.id,
        detail={
            "previous": previous,
            "display_name": clean_name,
            "email": clean_email,
            "home_region_id": str(destination.id),
        },
    )
    db.commit()
    return RedirectResponse(
        f"/manage/crew?region_id={destination.id}#{person.id}", status_code=303
    )


@router.post("/{person_id}/groups")
def update_groups(
    person_id: uuid.UUID,
    request: Request,
    group_ids: list[uuid.UUID] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    allowed = set(
        db.scalars(
            select(CrewGroup.id).where(
                CrewGroup.id.in_(group_ids), CrewGroup.lifecycle == Lifecycle.ACTIVE.value
            )
        )
    ) if group_ids else set()
    if len(allowed) != len(set(group_ids)):
        raise HTTPException(400, "Select active crew groups only.")
    db.execute(delete(PersonCrewGroup).where(PersonCrewGroup.person_id == person.id))
    db.add_all(
        [PersonCrewGroup(person_id=person.id, crew_group_id=group_id) for group_id in allowed]
    )
    record_audit(
        db,
        "person.groups.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"group_count": len(allowed)},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={person.home_region_id}#{person.id}", status_code=303)


@router.post("/{person_id}/capabilities/{position_id}")
def update_manager_capability(
    person_id: uuid.UUID,
    position_id: uuid.UUID,
    request: Request,
    signal: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    if signal not in {
        CapabilitySignal.MANAGER_ALLOW.value,
        CapabilitySignal.MANAGER_BLOCK.value,
        "CLEAR",
    }:
        raise HTTPException(400, "Invalid Manager capability decision.")
    position = db.get(BasePosition, position_id)
    if not position or position.lifecycle != Lifecycle.ACTIVE.value:
        raise HTTPException(404)
    set_preference_signal(
        db, person.id, position.id, signal, request.state.user.id, family="manager"
    )
    record_audit(
        db,
        "person.capability.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"position_id": str(position.id), "signal": signal},
    )
    db.commit()
    return RedirectResponse(f"/manage/crew?region_id={person.home_region_id}#{person.id}", status_code=303)


@router.post("/{person_id}/capabilities")
def add_manager_capability(
    person_id: uuid.UUID,
    request: Request,
    position_id: uuid.UUID = Form(...),
    signal: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    response = update_manager_capability(
        person_id=person_id,
        position_id=position_id,
        request=request,
        signal=signal,
        csrf_token=csrf_token,
        db=db,
    )
    response.headers["location"] = f"/manage/crew/{person_id}#capabilities"
    return response


@router.post("/{person_id}/lifecycle")
def update_lifecycle(
    person_id: uuid.UUID,
    request: Request,
    lifecycle: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    person = _person_in_scope(db, request, person_id)
    if lifecycle not in {Lifecycle.ACTIVE.value, Lifecycle.ARCHIVED.value}:
        raise HTTPException(400, "Invalid lifecycle.")
    person.lifecycle = lifecycle
    record_audit(
        db,
        "person.lifecycle.updated",
        "person",
        person.id,
        request.state.user.id,
        region_id=person.home_region_id,
        detail={"lifecycle": lifecycle},
    )
    db.commit()
    return RedirectResponse(
        f"/manage/crew?region_id={person.home_region_id}&show_archived=true", status_code=303
    )
