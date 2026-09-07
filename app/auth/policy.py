from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import Role
from app.identity.models import RoleGrant, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision


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
    for grant in db.scalars(select(RoleGrant).where(RoleGrant.user_id == user.id)):
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


def can_crew_view(actor: Actor, region_id: uuid.UUID) -> bool:
    allowed = {Role.EMPLOYEE.value, Role.SUB_MANAGER.value, Role.MANAGER.value, Role.VIEWER.value}
    return actor.is_admin or bool(actor.roles_for(region_id) & allowed)


def can_apply_for_open_position(actor: Actor, region_id: uuid.UUID) -> bool:
    """Only linked Employees may self-apply; broad Viewer access is never write authority."""
    return bool(actor.person_id and Role.EMPLOYEE.value in actor.roles_for(region_id))


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
