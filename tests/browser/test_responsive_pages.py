from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import date
from datetime import time as clock_time
from pathlib import Path

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
        cross_region = Region(name=f"Cross Region {suffix}")
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
        viewer = User(
            email=f"viewer-{suffix}@example.com",
            display_name="Browser Viewer",
            credential_hash=hash_credential("112233"),
            credential_kind="pin",
        )
        person = Person(display_name="Browser Crew Member")
        other_person = Person(display_name="Unrelated Browser Crew")
        db.add_all(
            [region, cross_region, group, manager, employee, admin, viewer, person, other_person]
        )
        db.flush()
        track = Track(
            name=f"Browser Track {suffix}", region_id=region.id, display_colour="#2E7D6A"
        )
        cross_track = Track(
            name=f"Cross Track {suffix}",
            region_id=cross_region.id,
            display_colour="#8A2BE2",
        )
        position = BasePosition(name=f"Browser Position {suffix}", crew_group_id=group.id)
        person.home_region_id = region.id
        db.add_all([track, cross_track, position])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
                RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
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
            on_track_time=clock_time(9),
            first_trial_time=clock_time(10),
            first_race_time=clock_time(11),
            last_race_time=clock_time(18),
            race_count=10,
            end_time=clock_time(19, 30),
            day_note="Browser normal Day note",
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(revision)
        db.flush()
        db.add_all(
            [Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
                note="Browser private roster detail",
                note_private=True,
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Unrelated position",
                person_id=other_person.id,
                person_name_snapshot=other_person.display_name,
                status="ASSIGNED",
                note="Unrelated private browser detail",
                note_private=True,
            )]
        )
        workday.current_published_revision_id = revision.id
        cross_workday = Workday(region_id=cross_region.id, created_by_user_id=manager.id)
        db.add(cross_workday)
        db.flush()
        cross_revision = WorkdayRevision(
            workday_id=cross_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date.today(),
            track_id=cross_track.id,
            track_name_snapshot=cross_track.name,
            track_colour_snapshot=cross_track.display_colour,
            title="Cross-region qualification day",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(cross_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=cross_revision.id,
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id,
                person_name_snapshot=person.display_name,
                status="ASSIGNED",
            )
        )
        cross_workday.current_published_revision_id = cross_revision.id
        db.commit()
        values = {
            "manager": (manager.email, "123456"),
            "employee": (employee.email, "654321"),
            "admin": (admin.email, "12345678"),
            "viewer": (viewer.email, "112233"),
            "workday_id": str(workday.id),
            "cross_workday_id": str(cross_workday.id),
            "region_id": str(region.id),
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
            configured_browser = os.environ.get("ONTRACK_PLAYWRIGHT_CHROMIUM_PATH", "")
            launch_options = (
                {"executable_path": str(Path(configured_browser).resolve())}
                if configured_browser and Path(configured_browser).is_file()
                else {}
            )
            browser = playwright.chromium.launch(**launch_options)
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
    _assert_no_horizontal_overflow(page)


def _assert_no_horizontal_overflow(page: Page) -> None:
    fits_viewport = page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    if fits_viewport:
        return

    details = page.evaluate(
        """
        () => {
          const viewportWidth = window.innerWidth;
          const describe = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            const classes = Array.from(element.classList)
              .map((name) => `.${CSS.escape(name)}`)
              .join("");
            const selector = `${element.tagName.toLowerCase()}${
              element.id ? `#${CSS.escape(element.id)}` : ""
            }${classes}`;
            return {
              selector,
              left: Math.round(rect.left * 100) / 100,
              right: Math.round(rect.right * 100) / 100,
              width: Math.round(rect.width * 100) / 100,
              scrollWidth: element.scrollWidth,
              clientWidth: element.clientWidth,
              display: style.display,
              minWidth: style.minWidth,
              computedWidth: style.width,
              flex: style.flex,
              gridTemplateColumns: style.gridTemplateColumns,
              whiteSpace: style.whiteSpace,
              overflowX: style.overflowX,
            };
          };
          const offenders = Array.from(document.body.querySelectorAll("*"))
            .filter((element) => {
              const rect = element.getBoundingClientRect();
              const style = getComputedStyle(element);
              const outsideViewport =
                rect.right > viewportWidth + 1 || rect.left < -1;
              const internallyOverflowing =
                element.scrollWidth > element.clientWidth + 1 &&
                !["auto", "scroll"].includes(style.overflowX);
              return outsideViewport || internallyOverflowing;
            })
            .map(describe);
          return {
            url: location.href,
            viewportWidth,
            documentScrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth,
            offenders,
          };
        }
        """
    )
    raise AssertionError(f"horizontal overflow details: {details!r}")


def _watch_browser_errors(page: Page) -> list[str]:
    errors: list[str] = []

    def capture_console(message) -> None:  # type: ignore[no-untyped-def]
        if message.type == "error":
            errors.append(f"{message.text} @ {message.location}")

    page.on("console", capture_console)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_key_pages_are_responsive(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _assert_page(page, base_url + "/login")
    _login(page, base_url, values["employee"])
    for path in (
        "/month",
        f"/day/{values['workday_id']}",
        "/crew",
        "/settings",
        "/open-positions",
        "/hours",
    ):
        _assert_page(page, base_url + path)
    page.goto(base_url + f"/day/{values['workday_id']}")
    assert page.locator(".hero-card").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim().toUpperCase()"
    ) == "#2E7D6A"
    page.goto(base_url + "/month")
    assert page.locator(".shift-chip.cross-region").count() == 1
    assert page.locator(".shift-chip.cross-region").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim().toUpperCase()"
    ) == "#8A2BE2"
    if width <= 760:
        page.locator('[data-view="list"]').click()
        assert page.locator("#list-view").is_visible()
    else:
        assert page.locator("#calendar-view").is_visible()
    page.goto(base_url + "/settings")
    page.locator('select[name="theme"]').select_option("moss")
    page.locator('form[action="/settings/theme"] button').click()
    page.wait_for_url("**/settings?theme=saved")
    assert page.locator("html").get_attribute("data-theme") == "moss"
    assert not errors
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    for path in (
        "/month",
        "/crew",
        "/manage/workdays/new",
        f"/manage/workdays/{values['workday_id']}",
        f"/manage/workdays/{values['workday_id']}/preview",
        "/manage/crew",
        "/manage/accounts",
        "/manage/catalog",
        "/manage/hours",
    ):
        _assert_page(page, base_url + path)
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    if width == 320:
        assert page.locator("main .panel").first.is_visible()
        preview = page.locator('a.button[href$="/preview"]')
        assert preview.is_visible()
        editor = page.locator("details.assignment-editor").first
        editor.locator("summary").click()
        assert editor.locator('select[name="person_id"]').is_visible()
        assert editor.get_by_role("button", name="Update slot").is_visible()
        _assert_no_horizontal_overflow(page)
    page.locator("[data-crew-search]").fill("Browser Crew")
    assert page.locator("[data-crew-picker] option", has_text="Browser Crew Member").count() >= 1
    assert not errors
    context.close()
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["viewer"])
    _assert_page(page, base_url + f"/day/{values['workday_id']}")
    assert page.get_by_text("Browser private roster detail").is_visible()
    csrf = next(
        cookie["value"] for cookie in context.cookies() if cookie["name"] == "ontrack_csrf"
    )
    response = page.request.post(
        base_url + "/manage/workdays",
        form={
            "region_id": values["region_id"],
            "category": "RACE_DAY",
            "work_date": date.today().isoformat(),
            "csrf_token": csrf,
        },
    )
    assert response.status == 403
    assert not errors
    context.close()

    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["admin"])
    for path in ("/admin", "/manage/catalog", "/manage/accounts"):
        _assert_page(page, base_url + path)
    page.goto(base_url + "/admin#branding")
    configured_name = "Trackside Operations Rostering Portal"
    page.locator('input[name="product_name"]').fill(configured_name)
    page.get_by_role("button", name="Save product name").click()
    page.wait_for_url("**/admin#branding")
    assert page.locator(".brand strong").inner_text() == configured_name
    assert configured_name in page.title()
    _assert_no_horizontal_overflow(page)
    page.goto(base_url + "/manage/catalog")
    assert page.locator(".brand strong").inner_text() == configured_name
    assert page.get_by_text("Regions", exact=True).is_visible()
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 430])
def test_specific_personal_day_renders_from_cache_while_physically_offline(
    browser_site, width: int
) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["employee"])
    page.goto(base_url + "/month")
    page.evaluate("async () => { await navigator.serviceWorker.ready; return true; }")
    page.reload()
    assert page.evaluate("navigator.serviceWorker.controller !== null")
    page.wait_for_function(
        """async (workdayId) => {
          const shell = await caches.open("ontrack-shell-v2");
          const marker = await shell.match("/__ontrack_active_user");
          if (!marker) return false;
          const namespace = await marker.text();
          const roster = await caches.open("ontrack-roster-" + namespace);
          return Boolean(await roster.match("/api/day/" + workdayId));
        }""",
        arg=values["workday_id"],
        timeout=10_000,
    )
    page.goto(base_url + f"/day/{values['workday_id']}")
    page.wait_for_timeout(500)
    context.set_offline(True)
    page.goto(base_url + f"/day/{values['workday_id']}", wait_until="domcontentloaded")
    assert page.get_by_text("Offline — showing this Day as cached at").is_visible()
    assert page.get_by_text("Browser normal Day note").is_visible()
    assert page.get_by_text("Browser private roster detail").is_visible()
    assert page.get_by_text("07:30").is_visible()
    assert page.get_by_text("10", exact=True).is_visible()
    assert page.get_by_text("Unrelated Browser Crew").count() == 0
    assert page.get_by_text("Unrelated private browser detail").count() == 0
    assert page.locator("form").count() == 0
    assert page.get_by_text("Edit private draft").count() == 0
    assert page.get_by_text("I’m not available").count() == 0
    _assert_no_horizontal_overflow(page)
    assert not errors
    context.set_offline(False)
    context.close()
