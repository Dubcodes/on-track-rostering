from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import verify_csrf
from app.catalog.models import Region
from app.core.database import get_db
from app.core.time import utcnow
from app.notices.models import OperationalNotice, OperationalNoticeRegion
from app.notices.service import permitted_notice_region_ids
from app.notifications.service import record_event

router = APIRouter(prefix="/manage/notices")


@router.post("")
def create_notice(
    request: Request,
    message: str = Form(...),
    global_notice: bool = Form(False),
    region_ids: list[uuid.UUID] = Form(default=[]),
    duration_hours: int = Form(12),
    also_push: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    clean = message.strip()
    if not clean or len(clean) > 500 or not 1 <= duration_hours <= 168:
        raise HTTPException(400, "Notice text or duration is invalid.")
    allowed = permitted_notice_region_ids(db, request.state.actor)
    selected = set(region_ids)
    if global_notice:
        if not request.state.actor.is_admin:
            raise HTTPException(403, "Admin access is required for a global notice.")
        scope, primary_region_id = "GLOBAL", None
        selected = set()
    elif selected and selected <= allowed:
        active = set(
            db.scalars(
                select(Region.id).where(
                    Region.id.in_(selected), Region.lifecycle == "ACTIVE"
                )
            )
        )
        if active != selected:
            raise HTTPException(400, "Select active Regions only.")
        scope, primary_region_id = "REGION", sorted(selected, key=str)[0]
    else:
        raise HTTPException(403, "Select at least one permitted Region.")
    now = utcnow()
    notice = OperationalNotice(
        scope=scope,
        region_id=primary_region_id,
        message=clean,
        starts_at=now,
        expires_at=now + timedelta(hours=duration_hours),
        created_by_user_id=request.state.user.id,
    )
    db.add(notice)
    db.flush()
    db.add_all(
        OperationalNoticeRegion(notice_id=notice.id, region_id=region_id)
        for region_id in selected
    )
    if also_push == "on":
        record_event(
            db,
            event_key=f"operational-notice:{notice.id}",
            event_type="OPERATIONAL_NOTICE",
            region_id=primary_region_id,
            workday_id=None,
            payload={
                "notice_id": str(notice.id),
                "message": clean,
                "region_ids": [str(region_id) for region_id in sorted(selected, key=str)],
            },
        )
    db.commit()
    return RedirectResponse("/manage?notice=created#notices", status_code=303)
