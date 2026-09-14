from __future__ import annotations

import hashlib
import re
import uuid
from datetime import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import verify_csrf
from app.core.config import get_settings
from app.core.database import get_db
from app.notifications.models import NotificationPreference, PushSubscription
from app.notifications.service import record_event, save_subscription

router = APIRouter(prefix="/settings/notifications")


@router.get("/config")
def push_config():
    settings = get_settings()
    return {
        "enabled": bool(settings.vapid_public_key and settings.vapid_private_key),
        "public_key": settings.vapid_public_key,
    }


@router.post("/subscription")
async def register_subscription(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    verify_csrf(request, str(payload.get("csrf_token", "")))
    subscription = payload.get("subscription")
    if not isinstance(subscription, dict):
        raise HTTPException(400, "Missing push subscription.")
    try:
        save_subscription(
            db,
            user_id=request.state.user.id,
            value=subscription,
            label=str(payload.get("label", "Browser")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.post("/subscription/disable")
async def disable_subscription(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    verify_csrf(request, str(payload.get("csrf_token", "")))
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str):
        raise HTTPException(400, "Missing push endpoint.")
    endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == request.state.user.id,
            PushSubscription.endpoint_hash == endpoint_hash,
        )
    )
    if subscription:
        subscription.active = False
        db.commit()
    return {"ok": True}


@router.post("/preferences")
def update_preferences(
    request: Request,
    roster_changes: str = Form(""),
    open_positions: str = Form(""),
    night_before: str = Form(""),
    two_days_before: str = Form(""),
    one_hour_before: str = Form(""),
    notifications_enabled: str = Form(""),
    important_changes_24h: str = Form(""),
    weekly_digest: str = Form(""),
    open_positions_digest: str = Form(""),
    admin_alerts: str = Form(""),
    reminder_time: str = Form("19:00"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    preference = db.get(NotificationPreference, request.state.user.id)
    if preference is None:
        preference = NotificationPreference(user_id=request.state.user.id)
        db.add(preference)
    preference.roster_changes = roster_changes == "on"
    preference.open_positions = open_positions == "on"
    preference.night_before = night_before == "on"
    preference.two_days_before = two_days_before == "on"
    preference.one_hour_before = one_hour_before == "on"
    preference.notifications_enabled = notifications_enabled == "on"
    preference.important_changes_24h = important_changes_24h == "on"
    preference.weekly_digest = weekly_digest == "on"
    preference.open_positions_digest = open_positions_digest == "on"
    preference.admin_alerts = admin_alerts == "on"
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reminder_time):
        raise HTTPException(400, "Reminder time is invalid.")
    try:
        preference.reminder_time = time.fromisoformat(reminder_time)
    except ValueError as exc:
        raise HTTPException(400, "Reminder time is invalid.") from exc
    db.commit()
    return RedirectResponse("/settings?notifications=saved#notifications", status_code=303)


@router.post("/test")
def test_notification(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    if not db.scalar(
        select(PushSubscription.id).where(
            PushSubscription.user_id == request.state.user.id,
            PushSubscription.active.is_(True),
        )
    ):
        return RedirectResponse("/settings?notification_test=no-device#notifications", status_code=303)
    event_id = uuid.uuid4()
    record_event(
        db,
        event_key=f"test-notification:{request.state.user.id}:{event_id}",
        event_type="TEST_NOTIFICATION",
        region_id=None,
        workday_id=None,
        audience_user_id=request.state.user.id,
    )
    db.commit()
    return RedirectResponse("/settings?notification_test=queued#notifications", status_code=303)
