from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.accounts.onboarding import (
    ONBOARDING_STATE_ORDER,
    onboarding_records,
    onboarding_state,
)
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
    revoke_outstanding_auth_artifacts,
    revoke_role_grant,
)
from app.catalog.models import BasePosition, CrewGroup, PersonCrewGroup, Region
from app.core.database import get_db
from app.core.enums import CapabilitySignal, Lifecycle, Role
from app.core.forms import optional_uuid
from app.core.time import utcnow
from app.identity.email import person_email_issues, validate_person_work_email
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


def crew_management_response(
    request: Request,
    db: Session,
    *,
    region_id: uuid.UUID | None = None,
    q: str = "",
    account_type: str = "",
    crew_group_id: str = "",
    position_id: str = "",
    show_archived: bool = False,
    add_person: dict[str, str] | None = None,
    add_errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    regions = _regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    selected_crew_group_id = optional_uuid(crew_group_id, "Crew Group")
    selected_position_id = optional_uuid(position_id, "Position")
    selected = next((region for region in regions if region.id == region_id), regions[0])
    can_administer_people = can_administer_region(request.state.actor, selected.id)
    statement = select(Person).where(Person.home_region_id == selected.id)
    if not show_archived:
        statement = statement.where(Person.lifecycle == Lifecycle.ACTIVE.value)
    if q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(or_(Person.display_name.ilike(term), Person.email.ilike(term)))
    if account_type and can_administer_people:
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
    if selected_crew_group_id:
        statement = statement.where(
            Person.id.in_(
                select(PersonCrewGroup.person_id).where(
                    PersonCrewGroup.crew_group_id == selected_crew_group_id
                )
            )
        )
    if selected_position_id:
        statement = statement.where(
            Person.id.in_(
                select(PositionCapability.person_id).where(
                    PositionCapability.base_position_id == selected_position_id,
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
        visible_security_account_ids = [
            account.id
            for person_id, account in accounts.items()
            if (person := next((row for row in visible_people if row.id == person_id), None))
            and person.home_region_id
            and can_administer_user(db, request.state.actor, account, person.home_region_id)
        ]
        if visible_security_account_ids:
            for user_id, role in db.execute(
                select(RoleGrant.user_id, RoleGrant.role).where(
                    RoleGrant.user_id.in_(visible_security_account_ids), RoleGrant.status == "ACTIVE"
                )
            ):
                roles_by_user[user_id].add(role)
            last_seen_by_user = dict(
                db.execute(
                    select(TrustedDevice.user_id, func.max(TrustedDevice.last_seen_at))
                    .where(
                        TrustedDevice.user_id.in_(visible_security_account_ids),
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
                    account.contractor_access_expires_at
                    if account and account.id in visible_security_account_ids
                    else None
                ),
                "email_mismatch": bool(
                    account
                    and account.id in visible_security_account_ids
                    and (person.email or "").casefold() != account.email.casefold()
                ),
                "identity_issues": person_email_issues(db, person) if can_administer_people else (),
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
            selected_crew_group_id=selected_crew_group_id,
            selected_position_id=selected_position_id,
            filter_account_types=[role.value for role in Role] if can_administer_people else [],
            filter_groups=list(db.scalars(select(CrewGroup).where(CrewGroup.lifecycle == "ACTIVE").order_by(CrewGroup.name))),
            filter_positions=list(db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE").order_by(BasePosition.name))),
            show_archived=show_archived,
            add_person=add_person or {
                "display_name": "",
                "email": "",
                "region_id": "" if request.state.actor.is_admin else str(selected.id),
                "account_type": Role.EMPLOYEE.value,
            },
            add_errors=add_errors or {},
            can_administer_people=can_administer_people,
        ),
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def crew_management(
    request: Request,
    region_id: uuid.UUID | None = None,
    q: str = "",
    account_type: str = "",
    crew_group_id: str = "",
    position_id: str = "",
    show_archived: bool = False,
    db: Session = Depends(get_db),
):
    return crew_management_response(
        request,
        db,
        region_id=region_id,
        q=q,
        account_type=account_type,
        crew_group_id=crew_group_id,
        position_id=position_id,
        show_archived=show_archived,
    )


@router.get("/onboarding", response_class=HTMLResponse)
def crew_onboarding(
    request: Request,
    onboarding_q: str = "",
    onboarding_region: str = "",
    onboarding_state: str = "",
    db: Session = Depends(get_db),
):
    regions = _regions(db, request)
    if not regions:
        raise HTTPException(403, "Regional roster authority required")
    allowed_region_ids = None if request.state.actor.is_admin else {row.id for row in regions}
    records = onboarding_records(
        db,
        allowed_region_ids=allowed_region_ids,
        query=onboarding_q,
        region=onboarding_region,
        state=onboarding_state,
    )
    return templates.TemplateResponse(
        "crew_onboarding.html",
        context(
            request,
            regions=regions,
            onboarding_people=records,
            onboarding_states=ONBOARDING_STATE_ORDER,
            onboarding_q=onboarding_q,
            onboarding_region=onboarding_region,
            onboarding_state_filter=onboarding_state,
        ),
    )


def crew_person_response(
    person_id: uuid.UUID,
    request: Request,
    db: Session,
    *,
    direct_account: dict[str, str] | None = None,
    direct_errors: dict[str, str] | None = None,
    profile_values: dict[str, str] | None = None,
    profile_errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    person = _person_in_operational_scope(db, request, person_id)
    regions = _regions(db, request)
    can_edit_person = bool(
        person.home_region_id
        and can_administer_region(request.state.actor, person.home_region_id)
    ) or bool(person.home_region_id is None and request.state.actor.is_admin)
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
        select(User)
        .join(UserPersonLink)
        .where(UserPersonLink.person_id == person.id)
        .with_for_update(of=User)
    )
    can_control_account = bool(
        account
        and person.home_region_id
        and can_administer_user(db, request.state.actor, account, person.home_region_id)
    )
    can_assist_account = bool(
        account
        and person.home_region_id
        and can_assist_user(db, request.state.actor, account, person.home_region_id)
    )
    grants = list(
        db.scalars(
            select(RoleGrant)
            .where(RoleGrant.user_id == account.id, RoleGrant.status != "REVOKED")
            .order_by(RoleGrant.granted_at)
        )
    ) if account and can_control_account else []
    revoked_grants = list(
        db.scalars(
            select(RoleGrant)
            .where(RoleGrant.user_id == account.id, RoleGrant.status == "REVOKED")
            .order_by(RoleGrant.revoked_at.desc())
            .limit(10)
        )
    ) if account and can_control_account else []
    region_names = {
        region.id: region.name
        for region in db.scalars(select(Region))
    }
    authority_roles = [Role.VIEWER, Role.SUB_MANAGER, Role.MANAGER]
    existing_grant_keys = {
        (grant.role, grant.region_id) for grant in grants if grant.status != "REVOKED"
    }
    authority_options = [
        {
            "role": role.value,
            "label": role.value.replace("_", " ").title(),
            "regions": [
                region
                for region in regions
                if can_grant_role(request.state.actor, role.value, region.id)
            ],
        }
        for role in authority_roles
        if any(can_grant_role(request.state.actor, role.value, region.id) for region in regions)
    ] if account and can_control_account else []
    devices = list(
        db.scalars(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == account.id)
            .order_by(TrustedDevice.last_seen_at.desc())
        )
    ) if account and can_control_account else []
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.person_id == person.id)
        .order_by(Invitation.created_at.desc())
        .limit(1)
    ) if can_edit_person else None
    notice_override = (
        db.get(NoticePublishingScope, account.id)
        if account and request.state.actor.is_admin
        else None
    )
    notice_scope_ids = set(
        db.scalars(
            select(NoticePublishingScopeRegion.region_id).where(
                NoticePublishingScopeRegion.user_id == account.id
            )
        )
    ) if account and request.state.actor.is_admin and notice_override else set()
    history = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id.in_([str(person.id), str(account.id) if account else ""]))
            .order_by(AuditEvent.occurred_at.desc())
            .limit(30)
        )
    ) if can_edit_person else []
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
            revoked_grants=revoked_grants,
            region_names=region_names,
            existing_grant_keys=existing_grant_keys,
            revocable_grant_ids={
                grant.id
                for grant in grants
                if can_grant_role(request.state.actor, grant.role, grant.region_id)
            },
            authority_options=authority_options,
            can_stage_admin=bool(
                account
                and can_control_account
                and can_grant_role(request.state.actor, Role.ADMIN.value, None)
                and (Role.ADMIN.value, None) not in existing_grant_keys
            ),
            devices=devices,
            last_seen=devices[0].last_seen_at if devices else None,
            setup_state=setup_state,
            setup_label=setup_label,
            invitation=invitation,
            history=history,
            notice_override=notice_override,
            notice_scope_ids=notice_scope_ids,
            can_control_account=can_control_account,
            can_assist_account=can_assist_account,
            can_edit_person=can_edit_person,
            identity_issues=person_email_issues(db, person) if can_edit_person else (),
            profile_values=profile_values or {
                "display_name": person.display_name,
                "email": person.email or "",
                "home_region_id": str(person.home_region_id or ""),
            },
            profile_errors=profile_errors or {},
            direct_account=direct_account or {"role": Role.EMPLOYEE.value},
            direct_errors=direct_errors or {},
        ),
        status_code=status_code,
    )


@router.get("/{person_id}", response_class=HTMLResponse)
def crew_person_detail(
    person_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    return crew_person_response(person_id, request, db)


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
        message = "Enter a crew name between 2 and 120 characters."
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                {"status": "error", "errors": {"display_name": message}},
                status_code=422,
            )
        return crew_person_response(
            person.id,
            request,
            db,
            profile_values={
                "display_name": display_name,
                "email": email,
                "home_region_id": home_region_id,
            },
            profile_errors={"display_name": message},
            status_code=422,
        )
    try:
        clean_email = validate_person_work_email(db, email, person_id=person.id)
    except ValueError as exc:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"status": "error", "errors": {"email": str(exc)}}, status_code=422)
        return crew_person_response(
            person.id,
            request,
            db,
            profile_values={
                "display_name": display_name,
                "email": email,
                "home_region_id": home_region_id,
            },
            profile_errors={"email": str(exc)},
            status_code=422,
        )
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
        return JSONResponse({
            "status": "saved",
            "refresh_url": f"/manage/crew/{person.id}",
            "setup_state": onboarding_state(db, person),
        })
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
            clean_email = validate_person_work_email(db, email, person_id=person.id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
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
    require_fresh_auth(request, db)
    person = _person_in_scope(db, request, person_id, allow_unassigned_admin=True)
    if role not in {Role.EMPLOYEE.value, Role.CASUAL.value, Role.CONTRACTOR.value, Role.SUB_MANAGER.value, Role.MANAGER.value}:
        raise HTTPException(403, "That account role is not available here.")
    if not person.email or person.home_region_id is None:
        raise HTTPException(409, "Work email and Primary Region are required.")
    if db.scalar(select(UserPersonLink.user_id).where(UserPersonLink.person_id == person.id)):
        raise HTTPException(409, "This Person already has an account.")
    if not can_grant_role(request.state.actor, role, person.home_region_id):
        raise HTTPException(403, "You cannot create that account role in this Region.")
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
            destination_label=f"Return to {person.display_name}",
            invitation_kind="Account setup invitation",
            recipient_name=person.display_name,
            recipient_email=person.email,
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
    require_fresh_auth(request, db)
    _person, account = _controlled_account(
        db, request, person_id, allow_operational_assist=True
    )
    account = db.scalar(select(User).where(User.id == account.id).with_for_update())
    if account is None:
        raise HTTPException(404)
    if account.id == request.state.user.id or account_status not in {"ACTIVE", "DISABLED"}:
        raise HTTPException(400, "Invalid account status change.")
    account.status = account_status
    account.auth_epoch += 1
    now = utcnow()
    revoke_outstanding_auth_artifacts(db, account.id, now)
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
    require_fresh_auth(request, db)
    person, account = _controlled_account(db, request, person_id)
    account = db.scalar(select(User).where(User.id == account.id).with_for_update())
    if account is None:
        raise HTTPException(404)
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
    revoke_outstanding_auth_artifacts(db, account.id, now)
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
    require_fresh_auth(request, db)
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
    require_fresh_auth(request, db)
    _person, account = _controlled_account(
        db, request, person_id, allow_operational_assist=True
    )
    if account.id == request.state.user.id:
        raise HTTPException(400, "Use Settings to change your own credential.")
    try:
        _reset, raw = create_credential_reset(
            db,
            user=account,
            actor=request.state.actor,
            region_id=_person.home_region_id,
            allow_operational_assist=True,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
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
    require_fresh_auth(request, db)
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
    require_fresh_auth(request, db)
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
    require_fresh_auth(request, db)
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
    region_id: str = Form(""),
    account_type: str = Form(Role.EMPLOYEE.value),
    action: str = Form("person"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    form_state = {"display_name": display_name, "email": email, "region_id": region_id, "account_type": account_type}
    errors: dict[str, str] = {}
    try:
        parsed_region_id = optional_uuid(region_id, "Primary Region")
    except HTTPException as exc:
        parsed_region_id = None
        errors["region_id"] = str(exc.detail)
    if parsed_region_id:
        region = db.get(Region, parsed_region_id)
        if not region or region.lifecycle != Lifecycle.ACTIVE.value:
            errors["region_id"] = "Select an active Region."
        elif not can_administer_region(request.state.actor, parsed_region_id):
            raise HTTPException(403, "Regional administration authority required.")
    elif not request.state.actor.is_admin:
        errors["region_id"] = "Select an active Region."
    clean_name = display_name.strip()
    if not 2 <= len(clean_name) <= 120:
        errors["display_name"] = "Enter a crew name between 2 and 120 characters."
    try:
        clean_email = validate_person_work_email(db, email)
    except ValueError as exc:
        clean_email = None
        errors["email"] = str(exc)
    create_setup = action == "person_invitation"
    if create_setup:
        require_fresh_auth(request, db)
        if not clean_email:
            errors["email"] = "A unique work email is required to create a setup link."
        if not parsed_region_id:
            errors["region_id"] = "A Primary Region is required to create a setup link."
        if parsed_region_id and not can_grant_role(request.state.actor, account_type, parsed_region_id):
            raise HTTPException(403, "You cannot create that account role in this Region.")
    duplicate_filters = [func.lower(Person.display_name) == clean_name.casefold()]
    if parsed_region_id:
        duplicate_filters.append(Person.home_region_id == parsed_region_id)
    if not errors.get("display_name") and db.scalar(
        select(Person.id).where(*duplicate_filters).limit(1)
    ):
        errors["display_name"] = (
            "A Person with that name already exists in this Region. Find and manage them in Crew."
        )
    if errors:
        return crew_management_response(
            request,
            db,
            region_id=parsed_region_id,
            add_person=form_state,
            add_errors=errors,
            status_code=422,
        )
    person = Person(display_name=clean_name, email=clean_email, home_region_id=parsed_region_id)
    db.add(person)
    db.flush()
    record_audit(
        db,
        "person.created",
        "person",
        person.id,
        request.state.user.id,
        region_id=parsed_region_id,
        detail={"display_name": clean_name},
    )
    if create_setup:
        try:
            _invitation, raw = create_invitation(
                db,
                email=clean_email or "",
                display_name=clean_name,
                person_id=person.id,
                role=account_type,
                region_id=parsed_region_id,
                actor_user_id=request.state.user.id,
                commit=False,
            )
        except ValueError as exc:
            db.rollback()
            errors["email"] = str(exc)
            return crew_management_response(
                request, db, region_id=parsed_region_id, add_person=form_state,
                add_errors=errors, status_code=422,
            )
    db.commit()
    if create_setup:
        return templates.TemplateResponse(
            "invitation_created.html",
            context(
                request,
                invitation_url=invitation_url(raw),
                destination=f"/manage/crew/{person.id}",
                destination_label=f"Continue to {person.display_name}",
                invitation_kind="Account setup invitation",
                recipient_name=person.display_name,
                recipient_email=person.email,
            ),
            headers={"Cache-Control": "no-store"},
        )
    destination = f"/manage/crew/{person.id}?created=1#profile"
    return RedirectResponse(destination, status_code=303)


@router.post("/{person_id}/grants")
def add_person_role_grant(
    person_id: uuid.UUID,
    request: Request,
    role: str = Form(...),
    region_id: uuid.UUID | None = Form(None),
    region_ids: list[uuid.UUID] = Form([]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request, db)
    _person, account = _controlled_account(db, request, person_id)
    selected_regions: list[uuid.UUID | None]
    if role == Role.ADMIN.value:
        if region_id or region_ids:
            raise HTTPException(400, "System Administrator access is global.")
        selected_regions = [None]
    else:
        selected_regions = list(dict.fromkeys(region_ids or ([region_id] if region_id else [])))
        if not selected_regions:
            raise HTTPException(400, "Select at least one Region.")
    try:
        for selected_region_id in selected_regions:
            grant_role(
                db,
                actor=request.state.actor,
                target_user=account,
                role=role,
                region_id=selected_region_id,
                commit=False,
            )
        db.commit()
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/crew/{person_id}#account", status_code=303)


@router.post("/{person_id}/grants/{grant_id}/revoke")
def revoke_person_role_grant(
    person_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    require_fresh_auth(request, db)
    _person, account = _controlled_account(db, request, person_id)
    grant = db.get(RoleGrant, grant_id)
    if not grant or grant.user_id != account.id:
        raise HTTPException(404)
    try:
        revoke_role_grant(db, actor=request.state.actor, grant=grant)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/manage/crew/{person_id}#account", status_code=303)


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
        return crew_person_response(
            person.id,
            request,
            db,
            profile_values={
                "display_name": display_name,
                "email": email,
                "home_region_id": str(home_region_id),
            },
            profile_errors={
                "display_name": "Enter a crew name between 2 and 120 characters."
            },
            status_code=422,
        )
    try:
        clean_email = validate_person_work_email(db, email, person_id=person.id)
    except ValueError as exc:
        return crew_person_response(
            person.id,
            request,
            db,
            profile_values={
                "display_name": display_name,
                "email": email,
                "home_region_id": str(home_region_id),
            },
            profile_errors={"email": str(exc)},
            status_code=422,
        )
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
