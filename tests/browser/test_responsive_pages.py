from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import date
from datetime import time as clock_time

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright

from app.auth.security import hash_credential
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import SessionLocal
from app.core.enums import Role
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import Assignment, Workday, WorkdayRevision

pytestmark = pytest.mark.skipif(
    os.environ.get("ONTRACK_RUN_BROWSER_TESTS") != "1",
    reason="browser qualification is opt-in and requires installed Playwright Chromium",
)


@pytest.fixture(scope="module")
def browser_site():  # type: ignore[no-untyped-def]
    suffix = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        region = Region(name=f"Browser Region {suffix}")
        group = CrewGroup(name=f"Browser Crew {suffix}")
        manager = User(
            email=f"manager-{suffix}@example.com",
            display_name="Browser Manager",
            credential_hash=hash_credential("123456"),
            credential_kind="pin",
        )
        employee = User(
            email=f"employee-{suffix}@example.com",
            display_name="Browser Employee",
            credential_hash=hash_credential("654321"),
            credential_kind="pin",
        )
        admin = User(
            email=f"admin-{suffix}@example.com",
            display_name="Browser Admin",
            credential_hash=hash_credential("12345678"),
            credential_kind="pin",
        )
        person = Person(display_name="Browser Crew Member")
        db.add_all([region, group, manager, employee, admin, person])
        db.flush()
        track = Track(
            name=f"Browser Track {suffix}", region_id=region.id, display_colour="#2E7D6A"
        )
        position = BasePosition(name=f"Browser Position {suffix}", crew_group_id=group.id)
        person.home_region_id = region.id
        db.add_all([track, position])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
            ]
        )
        workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(workday)
        db.flush()
        revision = WorkdayRevision(
            workday_id=workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date.today(),
            track_id=track.id,
            track_name_snapshot=track.name,
            track_colour_snapshot=track.display_colour,
            title="Browser qualification day",
            start_time=clock_time(7, 30),
            end_time=clock_time(19, 30),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        workday.current_published_revision_id = revision.id
        db.commit()
        values = {
            "manager": (manager.email, "123456"),
            "employee": (employee.email, "654321"),
            "admin": (admin.email, "12345678"),
            "workday_id": str(workday.id),
        }

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(base_url + "/health/live", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Browser qualification server did not start.")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                yield browser, base_url, values
            finally:
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def _login(page: Page, base_url: str, credentials: tuple[str, str]) -> None:
    page.goto(base_url + "/login")
    page.locator('input[name="email"]').fill(credentials[0])
    page.locator('input[name="credential"]').fill(credentials[1])
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/month")


def _assert_page(page: Page, url: str) -> None:
    response = page.goto(url)
    assert response and response.status < 400
    assert page.locator("main").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_key_pages_are_responsive(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _assert_page(page, base_url + "/login")
    _login(page, base_url, values["employee"])
    for path in (
        "/month",
        f"/day/{values['workday_id']}",
        "/settings",
        "/open-positions",
        "/hours",
    ):
        _assert_page(page, base_url + path)
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["manager"])
    for path in (
        "/crew",
        "/manage/workdays/new",
        "/manage/crew",
        "/manage/accounts",
        "/manage/hours",
    ):
        _assert_page(page, base_url + path)
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    _assert_page(page, base_url + "/admin")
    context.close()
