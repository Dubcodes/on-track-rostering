from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

from starlette.requests import Request

from app.auth.security import FreshAuthenticationRequired
from app.core.holidays import holiday_info_for_date
from app.core.time import utcnow
from app.identity.models import User
from app.main import fresh_auth_recovery
from app.notices.models import OperationalNotice
from app.notices.service import prominent_notice


def test_holiday_info_preserves_multiple_accessible_names() -> None:
    waitangi = holiday_info_for_date(date(2026, 2, 6))
    observed = holiday_info_for_date(date(2026, 4, 27))
    regional = holiday_info_for_date(date(2026, 1, 19), "Wellington")
    assert waitangi and waitangi.is_public_holiday and waitangi.names == ("Waitangi Day",)
    assert observed and "observed" in observed.name
    assert regional and regional.aria_label == "Public holiday: Wellington Anniversary Day"
    assert holiday_info_for_date(date(2026, 2, 5)) is None


def test_regional_notice_deterministically_outranks_global(db) -> None:  # type: ignore[no-untyped-def]
    now = utcnow()
    user = User(
        email="notice@example.test",
        display_name="Notice Manager",
        credential_hash="unused",
    )
    db.add(user)
    db.flush()
    region_id = uuid.uuid4()
    from app.catalog.models import Region

    db.add(Region(id=region_id, name="Notice Region"))
    db.flush()
    db.add_all(
        [
            OperationalNotice(
                scope="GLOBAL",
                message="Global",
                starts_at=now,
                expires_at=now + timedelta(hours=12),
                created_by_user_id=user.id,
            ),
            OperationalNotice(
                scope="REGION",
                region_id=region_id,
                message="Regional",
                starts_at=now,
                expires_at=now + timedelta(hours=12),
                created_by_user_id=user.id,
            ),
        ]
    )
    db.commit()
    assert prominent_notice(db, {region_id}, now).message == "Regional"
    assert prominent_notice(db, set(), now).message == "Global"


def test_hours_template_contract_has_all_day_and_fortnight_language() -> None:
    source = open("app/templates/hours.html", encoding="utf-8").read()
    assert "Not worked" in source
    assert "Fortnight total" in source
    assert "no automatic break deduction" in source


def test_shortcut_contract_excludes_deputy_sync() -> None:
    source = open("app/static/app.js", encoding="utf-8").read()
    for key in ('key === "m"', 'key === "l"', 'key === "n"', 'key === "p"'):
        assert key in source
    assert 'key === "s"' not in source
    assert "Math.abs(deltaX) < 70" in source


def test_fresh_auth_html_redirects_without_replaying_post() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/grants/stage",
            "headers": [
                (b"accept", b"text/html"),
                (b"referer", b"https://roster.example/admin?tab=accounts"),
            ],
            "scheme": "https",
            "server": ("roster.example", 443),
            "client": ("127.0.0.1", 1),
            "query_string": b"",
        }
    )
    response = asyncio.run(fresh_auth_recovery(request, FreshAuthenticationRequired()))
    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings?reauth=required&next=")
    assert "csrf" not in response.headers["location"].lower()
    assert "credential" not in response.headers["location"].lower()
