from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.policy import require_admin
from app.auth.security import verify_csrf
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utcnow
from app.error_reports.models import ErrorReport
from app.web import context, templates

router = APIRouter()


@router.post("/settings/error-reports")
def submit_error_report(
    request: Request,
    description: str = Form(...),
    page_path: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf_token)
    clean = description.strip()
    if not 3 <= len(clean) <= 1000:
        raise HTTPException(400, "Describe the problem in 3 to 1000 characters.")
    parsed_path = urlsplit(page_path).path[:500] if page_path else ""
    settings = get_settings()
    db.add(
        ErrorReport(
            reporting_user_id=request.state.user.id,
            reporting_person_id=request.state.actor.person_id,
            description=clean,
            build_id=(settings.build_id or settings.app_version)[:120],
            page_path=parsed_path,
        )
    )
    db.commit()
    return RedirectResponse("/settings?report=created#error-report", status_code=303)


@router.get("/admin/error-reports", response_class=HTMLResponse)
def error_report_list(request: Request, db: Session = Depends(get_db)):
    require_admin(request.state.actor)
    reports = list(
        db.scalars(select(ErrorReport).order_by(ErrorReport.created_at.desc()).limit(200))
    )
    return templates.TemplateResponse(
        "error_reports.html", context(request, reports=reports)
    )


@router.post("/admin/error-reports/{report_id}/review")
def review_error_report(
    report_id: uuid.UUID,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request.state.actor)
    verify_csrf(request, csrf_token)
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(404)
    report.status = "REVIEWED"
    report.reviewed_at = utcnow()
    report.reviewed_by_user_id = request.state.user.id
    db.commit()
    return RedirectResponse("/admin/error-reports", status_code=303)
