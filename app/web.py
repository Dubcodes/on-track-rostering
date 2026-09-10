from __future__ import annotations

from calendar import Calendar
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth.security import CSRF_COOKIE
from app.branding.service import DEFAULT_BRANDING
from app.core.config import get_settings
from app.core.holidays import holiday_for_date
from app.core.time import display_datetime, display_time, local_today


class OnTrackTemplates(Jinja2Templates):
    """Keep route rendering compact while adapting to Starlette's request-first API."""

    def TemplateResponse(  # noqa: N802
        self,
        name: str,
        values: dict[str, object],
        status_code: int = 200,
        **kwargs: object,
    ):
        return super().TemplateResponse(values["request"], name, values, status_code=status_code, **kwargs)


templates = OnTrackTemplates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["display_datetime"] = display_datetime
templates.env.filters["display_time"] = display_time


def context(request: Request, **values: object) -> dict[str, object]:
    actor = getattr(request.state, "actor", None)
    show_manage = bool(
        actor
        and (
            actor.is_admin
            or any({"MANAGER", "SUB_MANAGER"} & set(roles) for roles in actor.regional_roles.values())
        )
    )
    show_crew = bool(
        actor
        and (
            actor.is_admin
            or any(
                {"EMPLOYEE", "SUB_MANAGER", "MANAGER", "VIEWER"} & set(roles)
                for roles in actor.regional_roles.values()
            )
        )
    )
    show_open_positions = bool(
        actor and actor.person_id and any("EMPLOYEE" in roles for roles in actor.regional_roles.values())
    )
    show_accounts = bool(
        actor
        and (
            actor.is_admin
            or any("MANAGER" in roles for roles in actor.regional_roles.values())
        )
    )
    show_regional_admin = bool(
        actor
        and (
            actor.is_admin
            or any("MANAGER" in roles for roles in actor.regional_roles.values())
        )
    )
    show_hours_management = bool(
        actor
        and (
            actor.is_admin
            or any(
                {"MANAGER", "SUB_MANAGER", "VIEWER"} & set(roles)
                for roles in actor.regional_roles.values()
            )
        )
    )
    return {
        "request": request,
        "user": getattr(request.state, "user", None),
        "actor": actor,
        "branding": getattr(request.state, "branding", DEFAULT_BRANDING),
        "show_manage": show_manage,
        "show_crew": show_crew,
        "show_open_positions": show_open_positions,
        "show_accounts": show_accounts,
        "show_regional_admin": show_regional_admin,
        "show_hours_management": show_hours_management,
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "app_version": get_settings().app_version,
        "build_id": get_settings().build_id,
        **values,
    }


def month_grid(year: int, month: int) -> list[list[dict[str, object]]]:
    rows = []
    for week in Calendar(firstweekday=0).monthdatescalendar(year, month):
        rows.append(
            [
                {
                    "date": day,
                    "in_month": day.month == month,
                    "holiday": holiday_for_date(day),
                    "today": day == local_today(),
                }
                for day in week
            ]
        )
    return rows
