from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import Role
from app.core.time import utcnow
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.participation import person_day_participation


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    person_id: uuid.UUID | None
    global_roles: frozenset[str]
    regional_roles: dict[uuid.UUID, frozenset[str]]

    @property
    def is_admin(self) -> bool:
        return Role.ADMIN.value in self.global_roles

    def roles_for(self, region_id: uuid.UUID) -> frozenset[str]:
        return self.global_roles | self.regional_roles.get(region_id, frozenset())


def actor_for(db: Session, user: User) -> Actor:
    regional: dict[uuid.UUID, set[str]] = {}
    global_roles: set[str] = set()
    for grant in db.scalars(
        select(RoleGrant).where(RoleGrant.user_id == user.id, RoleGrant.status == "ACTIVE")
    ):
        if grant.region_id is None:
            global_roles.add(grant.role)
        else:
            regional.setdefault(grant.region_id, set()).add(grant.role)
    link = db.get(UserPersonLink, user.id)
    return Actor(
        user_id=user.id,
        person_id=link.person_id if link else None,
        global_roles=frozenset(global_roles),
        regional_roles={key: frozenset(value) for key, value in regional.items()},
    )


def can_manage_region(actor: Actor, region_id: uuid.UUID) -> bool:
    return actor.is_admin or bool(actor.roles_for(region_id) & {Role.MANAGER.value, Role.SUB_MANAGER.value})


def can_administer_region(actor: Actor, region_id: uuid.UUID) -> bool:
    return actor.is_admin or Role.MANAGER.value in actor.roles_for(region_id)


def can_administer_person(actor: Actor, person: Person, region_id: uuid.UUID) -> bool:
    if person.lifecycle != "ACTIVE" or person.home_region_id != region_id:
        return False
    return can_administer_region(actor, region_id)


def can_administer_user(db: Session, actor: Actor, user: User, region_id: uuid.UUID) -> bool:
    if actor.is_admin:
        return True
    if not can_administer_region(actor, region_id):
        return False
    link = db.get(UserPersonLink, user.id)
    person = db.get(Person, link.person_id) if link else None
    if person and can_administer_person(actor, person, region_id):
        return True
    return bool(
        db.scalar(
            select(RoleGrant.id).where(
                RoleGrant.user_id == user.id,
                RoleGrant.region_id == region_id,
                RoleGrant.status != "REVOKED",
            ).limit(1)
        )
    )


def can_grant_role(actor: Actor, role: str, region_id: uuid.UUID | None) -> bool:
    if actor.is_admin:
        return role in {item.value for item in Role} and (
            (role == Role.ADMIN.value and region_id is None)
            or (role != Role.ADMIN.value and region_id is not None)
        )
    return bool(
        role == Role.SUB_MANAGER.value
        and region_id is not None
        and Role.MANAGER.value in actor.roles_for(region_id)
    )


def can_crew_view(actor: Actor, region_id: uuid.UUID) -> bool:
    allowed = {Role.EMPLOYEE.value, Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value}
    return actor.is_admin or bool(actor.roles_for(region_id) & allowed)


def can_view_management_detail(actor: Actor, region_id: uuid.UUID) -> bool:
    """Read-only operational/HR detail, independent from roster write authority."""
    allowed = {Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value}
    return actor.is_admin or bool(actor.roles_for(region_id) & allowed)


def can_apply_for_open_position(actor: Actor, region_id: uuid.UUID) -> bool:
    """Only linked Employees may self-apply; broad Viewer access is never write authority."""
    return bool(actor.person_id and Role.EMPLOYEE.value in actor.roles_for(region_id))


def can_self_decline_assignment(
    actor: Actor,
    workday: Workday,
    revision: WorkdayRevision,
    assignments: list[Assignment],
    *,
    now: datetime | None = None,
) -> bool:
    """Allow linked Employee/Contractor self-service only before their shift begins."""
    roles = actor.roles_for(workday.region_id)
    if actor.person_id is None or not roles & {
        Role.EMPLOYEE.value,
        Role.CONTRACTOR.value,
    }:
        return False
    own_assigned = [
        row
        for row in assignments
        if row.person_id == actor.person_id and row.status == "ASSIGNED"
    ]
    if not own_assigned:
        return False
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=get_settings().timezone)
    local_now = current.astimezone(get_settings().timezone)
    if revision.work_date < local_now.date():
        return False
    if revision.work_date > local_now.date():
        return True
    start = person_day_participation(revision, own_assigned).start
    if start is None:
        return False
    start_date = revision.work_date
    if (
        revision.start_time is not None
        and revision.end_time is not None
        and revision.end_time < revision.start_time
        and start < revision.start_time
    ):
        start_date += timedelta(days=1)
    start_at = datetime.combine(start_date, start, tzinfo=get_settings().timezone)
    return local_now < start_at


def assigned_to_revision(db: Session, actor: Actor, revision_id: uuid.UUID) -> bool:
    return bool(
        actor.person_id
        and db.scalar(
            select(Assignment.id)
            .where(Assignment.revision_id == revision_id, Assignment.person_id == actor.person_id)
            .limit(1)
        )
    )


def can_view_published(db: Session, actor: Actor, workday: Workday, revision: WorkdayRevision) -> bool:
    return can_crew_view(actor, workday.region_id) or assigned_to_revision(db, actor, revision.id)


def require_manage_region(actor: Actor, region_id: uuid.UUID) -> None:
    if not can_manage_region(actor, region_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Regional roster authority required"
        )


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin authority required")
