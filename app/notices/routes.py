from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.policy import can_manage_region
from app.auth.security import verify_csrf
from app.core.database import get_db
from app.core.time import utcnow
from app.notices.models import OperationalNotice
from app.notifications.service import record_event

router = APIRouter(prefix="/manage/notices")


@router.post("")
def create_notice(
    request: Request,
    message: str = Form(...),
    scope: str = Form("REGION"),
    region_id: uuid.UUID | None = Form(None),
    duration_hours: int = Form(12),
    also_push: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    clean = message.strip()
    if not clean or len(clean) > 500 or not 1 <= duration_hours <= 168:
        raise HTTPException(400, "Notice text or duration is invalid.")
    if scope == "GLOBAL":
        if not request.state.actor.is_admin:
            raise HTTPException(403, "Admin access is required for a global notice.")
        region_id = None
    elif scope == "REGION" and region_id and can_manage_region(request.state.actor, region_id):
        pass
    else:
        raise HTTPException(403, "Regional management access is required.")
    now = utcnow()
    notice = OperationalNotice(
        scope=scope,
        region_id=region_id,
        message=clean,
        starts_at=now,
        expires_at=now + timedelta(hours=duration_hours),
        created_by_user_id=request.state.user.id,
    )
    db.add(notice)
    db.flush()
    if also_push == "on":
        record_event(
            db,
            event_key=f"operational-notice:{notice.id}",
            event_type="OPERATIONAL_NOTICE",
            region_id=region_id,
            workday_id=None,
            payload={"notice_id": str(notice.id), "message": clean},
        )
    db.commit()
    return RedirectResponse("/settings?notice=created#notices", status_code=303)
