from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from datetime import time as clock_time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select

from app.auth.security import hash_credential, token_hash
from app.branding.models import SystemBranding
from app.catalog.models import BasePosition, CrewGroup, Region, Track
from app.core.database import Base, SessionLocal, engine
from app.core.enums import Role
from app.core.time import utcnow
from app.external_calendar.models import ExternalCalendarEvent, ExternalEventObservation
from app.identity.models import Person, RoleGrant, TrustedDevice, User, UserPersonLink
from app.notices.models import OperationalNotice
from app.notifications.models import NotificationPreference
from app.rostering.models import Assignment, Workday, WorkdayRevision

pytestmark = pytest.mark.skipif(
    os.environ.get("ONTRACK_RUN_BROWSER_TESTS") != "1",
    reason="browser qualification is opt-in and requires installed Playwright Chromium",
)


@pytest.fixture(scope="module")
def browser_site():  # type: ignore[no-untyped-def]
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    suffix = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        region = Region(name=f"Browser Region {suffix}", statutory_holiday_region="Auckland")
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
        manager_person = Person(display_name="Browser Roster Manager")
        other_person = Person(display_name="Unrelated Browser Crew")
        db.add_all(
            [
                region,
                cross_region,
                group,
                manager,
                employee,
                admin,
                viewer,
                person,
                manager_person,
                other_person,
            ]
        )
        db.flush()
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "On Track"
            branding.updated_by_user_id = admin.id
        else:
            db.add(SystemBranding(id=1, product_name="On Track", updated_by_user_id=admin.id))
        track = Track(
            name=f"Browser Track {suffix}", region_id=region.id, palette_slot=1
        )
        cross_track = Track(
            name=f"Cross Track {suffix}",
            region_id=cross_region.id,
            palette_slot=1,
        )
        position = BasePosition(name=f"Browser Position {suffix}", crew_group_id=group.id)
        person.home_region_id = region.id
        manager_person.home_region_id = region.id
        db.add_all([track, cross_track, position])
        db.flush()
        db.add_all(
            [
                UserPersonLink(user_id=employee.id, person_id=person.id),
                UserPersonLink(user_id=manager.id, person_id=manager_person.id),
                RoleGrant(user_id=employee.id, role=Role.EMPLOYEE.value, region_id=region.id),
                RoleGrant(user_id=manager.id, role=Role.MANAGER.value, region_id=region.id),
                RoleGrant(user_id=admin.id, role=Role.ADMIN.value),
                RoleGrant(user_id=viewer.id, role=Role.VIEWER.value, region_id=region.id),
                NotificationPreference(user_id=employee.id, weekly_digest=True),
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
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Roster manager",
                person_id=manager_person.id,
                person_name_snapshot=manager_person.display_name,
                status="ASSIGNED",
            ),
            Assignment(
                revision_id=revision.id,
                base_position_id=position.id,
                display_name_snapshot="Reserve camera",
                status="TBC",
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
        travel_workday = Workday(
            region_id=region.id,
            category="TRAVEL_DAY",
            created_by_user_id=manager.id,
        )
        db.add(travel_workday)
        db.flush()
        travel_revision = WorkdayRevision(
            workday_id=travel_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date.today().replace(day=8),
            track_name_snapshot="Operations Transit",

            title="Travel to race meeting",
            start_time=clock_time(9),
            end_time=clock_time(16, 30),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(travel_revision)
        db.flush()
        db.add_all(
            [
                Assignment(
                    revision_id=travel_revision.id,
                    base_position_id=position.id,
                    display_name_snapshot="Travel",
                    person_id=person.id,
                    person_name_snapshot=person.display_name,
                    status="ASSIGNED",
                ),
                Assignment(
                    revision_id=travel_revision.id,
                    base_position_id=position.id,
                    display_name_snapshot="Travel lead",
                    person_id=manager_person.id,
                    person_name_snapshot=manager_person.display_name,
                    status="ASSIGNED",
                ),
            ]
        )
        travel_workday.current_published_revision_id = travel_revision.id
        open_workday = Workday(region_id=region.id, created_by_user_id=manager.id)
        db.add(open_workday)
        db.flush()
        open_revision = WorkdayRevision(
            workday_id=open_workday.id,
            revision_number=1,
            state="PUBLISHED",
            work_date=date.today().replace(day=22),
            track_name_snapshot="Browser Track Open Day",

            title="Open race day",
            start_time=clock_time(8),
            end_time=clock_time(17),
            created_by_user_id=manager.id,
            published_by_user_id=manager.id,
        )
        db.add(open_revision)
        db.flush()
        db.add(
            Assignment(
                revision_id=open_revision.id,
                base_position_id=position.id,
                display_name_snapshot="Open CCU",
                status="OPEN",
            )
        )
        open_workday.current_published_revision_id = open_revision.id
        notice_now = utcnow()
        active_notice_text = "Do not park on the grass today."
        expired_notice_text = "Expired browser notice"
        db.add_all(
            [
                OperationalNotice(
                    scope="GLOBAL",
                    message="Global browser crew notice",
                    starts_at=notice_now - timedelta(minutes=10),
                    expires_at=notice_now + timedelta(hours=12),
                    created_by_user_id=admin.id,
                ),
                OperationalNotice(
                    scope="REGION",
                    region_id=region.id,
                    message=active_notice_text,
                    starts_at=notice_now - timedelta(minutes=5),
                    expires_at=notice_now + timedelta(hours=12),
                    created_by_user_id=manager.id,
                ),
                OperationalNotice(
                    scope="REGION",
                    region_id=region.id,
                    message=expired_notice_text,
                    starts_at=notice_now - timedelta(days=1),
                    expires_at=notice_now - timedelta(minutes=1),
                    created_by_user_id=manager.id,
                ),
            ]
        )
        external_event = ExternalCalendarEvent(
            event_date=date.today(),
            track_id=track.id,
            external_track_name=track.name,
            discipline="THOROUGHBRED",
            event_kind="RACE",
            first_race_time=clock_time(12, 30),
            race_count=8,
            presentation_provider="LOVE_RACING",
            field_provenance={"race_count": ["LOVE_RACING"]},
        )
        db.add(external_event)
        db.flush()
        db.add_all(
            [
                ExternalEventObservation(
                    provider="HRNZ",
                    source_track_name="Unmatched Browser Track",
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={
                        "event_date": date.today().isoformat(),
                        "discipline": "HARNESS",
                        "event_kind": "TRIAL",
                    },
                    raw_payload={"meeting": "Unmatched Browser Track"},
                    mapping_state="UNMATCHED",
                    reconciliation_state="REVIEW",
                ),
                ExternalEventObservation(
                    event_id=external_event.id,
                    provider="API",
                    provider_event_id=f"conflict-{suffix}",
                    source_track_name=track.name,
                    payload_hash=uuid.uuid4().hex,
                    parsed_facts={"race_count": 9},
                    raw_payload={"race_count": 9},
                    mapping_state="MAPPED",
                    reconciliation_state="CONFLICT",
                ),
            ]
        )
        db.commit()
        values = {
            "manager": (manager.email, "123456"),
            "employee": (employee.email, "654321"),
            "admin": (admin.email, "12345678"),
            "viewer": (viewer.email, "112233"),
            "workday_id": str(workday.id),
            "cross_workday_id": str(cross_workday.id),
            "region_id": str(region.id),
            "cross_region_id": str(cross_region.id),
            "track_id": str(track.id),
            "cross_track_id": str(cross_track.id),
            "active_notice_text": active_notice_text,
            "expired_notice_text": expired_notice_text,
            "external_event_id": str(external_event.id),
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


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_workday_region_tracks_and_friendly_time(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/manage/workdays/new")
    region = page.locator("[data-track-region]")
    tracks = page.locator("[data-region-track]")
    region.select_option(values["region_id"])
    assert tracks.locator(f'option[value="{values["track_id"]}"]').count() == 1
    assert tracks.locator(f'option[value="{values["cross_track_id"]}"]').count() == 0
    tracks.select_option(values["track_id"])
    region.select_option(values["cross_region_id"])
    assert tracks.input_value() == ""
    assert tracks.locator(f'option[value="{values["track_id"]}"]').count() == 0
    tracks.select_option(values["cross_track_id"])
    region.select_option(values["region_id"])
    assert tracks.input_value() == ""
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"region-track-filter-{width}.png")
    page.goto(base_url + f'/manage/workdays/{values["workday_id"]}')
    start = page.locator('input[name="start_time"]')
    assert start.get_attribute("type") == "text"
    assert start.get_attribute("inputmode") == "numeric"
    for value in ("930", "0930", "9:30", "09:30"):
        start.fill(value)
        start.press("Tab")
        assert start.input_value() == "09:30"
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_master_data_palette_changes_with_theme(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    colours = []
    for theme in ("race-night", "daylight", "high-contrast", "track-colours"):
        _select_theme(page, base_url, theme)
        page.goto(base_url + "/manage/catalog")
        assert page.locator('input[type="color"]').count() == 0
        record = page.locator(f'form[action="/manage/catalog/tracks/{values["track_id"]}"]')
        record.locator("..").locator("summary").click()
        assert record.locator('select[name="region_id"]').is_visible()
        assert record.locator('input[name="map_reference"]').is_visible()
        colours.append(page.locator('.track-dot[data-presentation="track-01"]').first.evaluate("element => getComputedStyle(element).getPropertyValue('--track').trim()"))
        _assert_no_horizontal_overflow(page)
        _capture_page(page, f"master-data-palette-{theme}-{width}.png")
    assert len(set(colours)) == 4
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_external_source_import_preferences_and_detail(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/settings#calendar")
    minimal = page.locator('input[name="minimal_external_detail"]')
    if minimal.is_checked():
        minimal.uncheck()
        page.locator('form[action="/settings/calendar"] button').click()
        page.wait_for_url("**/settings?calendar=saved#calendar")
    page.goto(base_url + "/month")
    event_card = page.locator(f'a[href="/external-events/{values["external_event_id"]}"]')
    assert event_card.count() == 1
    _capture_page(page, f"external-event-normal-{width}.png")
    event_card.click()
    assert page.get_by_text("Raw Race Day Data").count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"external-event-detail-{width}.png")

    page.goto(base_url + "/admin/online-sources")
    assert page.get_by_text("Unmatched Browser Track").count() >= 1
    assert page.get_by_text("Conflicting observations").count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"online-sources-{width}.png")

    page.goto(base_url + "/admin/data-import")
    unique_region = f"Preview Browser {uuid.uuid4().hex[:8]}"
    page.locator('textarea[name="pasted_json"]').fill(
        '{"version":"1","regions":[{"name":"' + unique_region + '"}]}'
    )
    page.get_by_role("button", name="Parse and preview").click()
    assert page.get_by_text("Preview", exact=True).count() == 1
    assert page.get_by_text("create 1", exact=False).count() >= 1
    assert page.get_by_role("button", name="Import these records").count() == 1
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"data-import-preview-{width}.png")

    page.goto(base_url + "/settings#calendar")
    minimal = page.locator('input[name="minimal_external_detail"]')
    minimal.check()
    page.locator('form[action="/settings/calendar"] button').click()
    page.wait_for_url("**/settings?calendar=saved#calendar")
    page.goto(base_url + "/month")
    event_href = f'/external-events/{values["external_event_id"]}'
    marker = page.locator(f'.external-marker:has(a[href="{event_href}"])')
    assert marker.count() == 1
    marker.locator("summary").click()
    assert marker.get_attribute("open") is not None
    event_link = marker.locator(f'a[href="{event_href}"]')
    assert event_link.is_visible()
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"external-event-minimal-{width}.png")
    event_link.click()
    page.wait_for_url(f"**{event_href}")
    context.close()


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


def _capture_page(page: Page, name: str) -> None:
    if os.environ.get("ONTRACK_CAPTURE_BROWSER_SCREENSHOTS") != "1":
        return
    output = Path("test-results/ui-fidelity")
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate(
        "async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }"
    )
    page.screenshot(path=str(output / name), full_page=True)


def _capture_locator(page: Page, selector: str, name: str) -> None:
    if os.environ.get("ONTRACK_CAPTURE_BROWSER_SCREENSHOTS") != "1":
        return
    output = Path("test-results/ui-fidelity")
    output.mkdir(parents=True, exist_ok=True)
    page.locator(selector).screenshot(path=str(output / name))


def _select_theme(page: Page, base_url: str, theme: str) -> None:
    page.goto(base_url + "/settings")
    page.locator(".theme-picker-details summary").click()
    page.locator(f'input[name="theme"][value="{theme}"]').check()
    page.locator('form[action="/settings/theme"] button').click()
    page.wait_for_url("**/settings?theme=saved")
    assert page.locator("html").get_attribute("data-theme") == theme


@pytest.mark.parametrize("width", [1280, 430, 375, 320])
def test_key_pages_are_responsive(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "On Track"
            db.commit()
    context = browser.new_context(
        viewport={"width": width, "height": 900}, has_touch=width <= 760
    )
    page = context.new_page()
    errors = _watch_browser_errors(page)
    _assert_page(page, base_url + "/login")
    if width in {1280, 320}:
        _capture_page(page, f"login-{width}.png")
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
        if width <= 760:
            assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + f"/day/{values['workday_id']}")
    assert page.locator(".hero-card").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim() === getComputedStyle(document.documentElement).getPropertyValue('--track-01').trim()"
    )
    page.goto(base_url + "/month")
    assert page.locator(".shift-card.cross-region").count() == 1
    assert page.locator(".shift-card.cross-region").evaluate(
        "element => getComputedStyle(element).getPropertyValue('--track').trim() === getComputedStyle(document.documentElement).getPropertyValue('--track-01').trim()"
    )
    assert page.locator(".site-header.has-month-nav").is_visible()
    assert page.locator(".month-nav").is_visible()
    assert page.locator(".calendar-grid").is_visible()
    assert page.locator(".weekday:not(.weekday-total)").count() == 7
    assert page.locator(".weekday-total").count() == 1
    assert page.locator(".month-head").count() == 0
    assert page.get_by_text("Your authoritative roster").count() == 0
    assert page.get_by_role("link", name="Previous month").is_visible()
    assert page.get_by_role("link", name="Next month").is_visible()
    assert page.locator("[data-roster-nav]").count() == 1
    if width <= 760:
        assert page.locator(".brand > strong:first-child").is_visible()
        assert page.locator(".brand > strong:first-child").inner_text().strip() == "On Track"
    else:
        assert page.locator(".brand-compact").count() == 0
        assert page.locator(".brand > strong:first-child").is_visible()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    upcoming_box = page.locator(".upcoming-strip").bounding_box()
    assert calendar_box and upcoming_box and upcoming_box["y"] > calendar_box["y"]
    if width <= 760:
        assert page.locator(".week-total").first.is_hidden()
        column_count = page.locator(".calendar-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert column_count == 7
        assert page.locator(".day-cell").first.evaluate(
            "element => getComputedStyle(element).minHeight"
        ) == "72px"
        assert page.locator(".upcoming-list").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        ) == 1
        page.get_by_role("link", name="List view").click()
        page.wait_for_url("**view=list")
        assert page.locator(".roster-list").is_visible()
        page.get_by_role("link", name="Month view").click()
        page.wait_for_url("**view=month")
        next_url = page.locator("[data-roster-nav]").get_attribute("data-next-url")
        page.evaluate(
            """() => {
              const start = new Event("touchstart");
              Object.defineProperty(start, "touches", {value: [{clientX: 280, clientY: 120}]});
              document.dispatchEvent(start);
              const end = new Event("touchend");
              Object.defineProperty(end, "changedTouches", {value: [{clientX: 120, clientY: 125}]});
              document.dispatchEvent(end);
            }"""
        )
        page.wait_for_url(f"**{next_url}")
        previous_url = page.locator("[data-roster-nav]").get_attribute("data-prev-url")
        page.evaluate(
            """() => {
              const start = new Event("touchstart");
              Object.defineProperty(start, "touches", {value: [{clientX: 90, clientY: 120}]});
              document.dispatchEvent(start);
              const end = new Event("touchend");
              Object.defineProperty(end, "changedTouches", {value: [{clientX: 250, clientY: 125}]});
              document.dispatchEvent(end);
            }"""
        )
        page.wait_for_url(f"**{previous_url}")
        unchanged_url = page.url
        for end_x, end_y in ((240, 122), (275, 260)):
            page.evaluate(
                """([endX, endY]) => {
                  const start = new Event("touchstart");
                  Object.defineProperty(start, "touches", {value: [{clientX: 280, clientY: 120}]});
                  document.dispatchEvent(start);
                  const end = new Event("touchend");
                  Object.defineProperty(end, "changedTouches", {value: [{clientX: endX, clientY: endY}]});
                  document.dispatchEvent(end);
                }""",
                [end_x, end_y],
            )
            assert page.url == unchanged_url
    else:
        assert page.locator(".week-total").first.is_visible()
        column_count = page.locator(".calendar-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert column_count == 8
        page.keyboard.press("l")
        page.wait_for_url("**view=list")
        assert page.locator(".roster-list").is_visible()
        page.keyboard.press("m")
        page.wait_for_url("**view=month")
        assert page.locator(".calendar-grid").is_visible()
        original_label = page.locator(".month-nav > strong").inner_text()
        page.evaluate(
            """() => {
              const editor = document.createElement("div");
              editor.contentEditable = "true";
              editor.id = "keyboard-guard";
              document.body.appendChild(editor);
              editor.focus();
            }"""
        )
        guarded_url = page.url
        page.keyboard.press("n")
        assert page.url == guarded_url
        page.locator("#keyboard-guard").evaluate("element => element.remove()")
        page.locator("body").click(position={"x": 1, "y": 1})
        page.keyboard.press("n")
        page.wait_for_function(
            "label => document.querySelector('.month-nav > strong')?.textContent.trim() !== label",
            arg=original_label,
        )
        page.keyboard.press("p")
        page.wait_for_function(
            "label => document.querySelector('.month-nav > strong')?.textContent.trim() === label",
            arg=original_label,
        )
        page.goto(base_url + "/crew")
        page.keyboard.press("l")
        page.wait_for_url("**view=list")
        page.keyboard.press("m")
        page.wait_for_url("**view=month")
    page.goto(base_url + "/settings")
    page.locator(".theme-picker-details summary").click()
    assert page.locator('input[name="theme"]').count() == 20
    swatches = page.locator(".theme-swatch")
    for index in range(swatches.count()):
        rendered = swatches.nth(index).evaluate(
            "element => { const s=getComputedStyle(element); return s.backgroundImage !== 'none' || s.backgroundColor !== 'rgba(0, 0, 0, 0)'; }"
        )
        assert rendered
    target_theme = {1280: "high-contrast", 430: "race-night", 375: "daylight", 320: "jade"}[width]
    page.locator(f'input[name="theme"][value="{target_theme}"]').check()
    page.locator('form[action="/settings/theme"] button').click()
    page.wait_for_url("**/settings?theme=saved")
    assert page.locator("html").get_attribute("data-theme") == target_theme
    for path in ("/settings", "/month", f"/day/{values['workday_id']}", "/help"):
        _assert_page(page, base_url + path)
        assert page.locator("html").get_attribute("data-theme") == target_theme
        if width in {1280, 320}:
            slug = path.strip("/").replace("/", "-") or "home"
            _capture_page(page, f"{slug}-{target_theme}-{width}.png")
    if width == 1280:
        page.goto(base_url + "/month")
        theme_values = page.locator("html").evaluate(
            "element => { const style = getComputedStyle(element); return [style.getPropertyValue('--bg').trim(), style.getPropertyValue('--text').trim(), style.getPropertyValue('--accent').trim()]; }"
        )
        assert [value.lower() for value in theme_values] == ["#000000", "#ffffff", "#ffe45c"]
        card_colours = page.locator(".shift-card").first.evaluate(
            "element => { const style = getComputedStyle(element); return [style.color, style.backgroundColor, style.borderLeftColor]; }"
        )
        assert len(set(card_colours)) == 3
        page.goto(base_url + "/settings")
        assert page.locator("label").first.evaluate(
            "element => getComputedStyle(element).color"
        ) == "rgb(255, 255, 255)"
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
        if width in {1280, 320}:
            slug = path.strip("/").replace("/", "-") or "home"
            _capture_page(page, f"manager-{slug}-{width}.png")
        if width <= 760:
            assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + "/month")
    assert page.get_by_text("Operations Transit", exact=True).count() == 1
    assert page.get_by_text("Travel lead", exact=True).count() == 1
    assert page.locator(".available-shift-dot", has_text="Open").count() == 1
    _capture_page(page, f"month-{width}.png")
    if width == 1280:
        _select_theme(page, base_url, "race-night")
        page.goto(base_url + "/month")
        _capture_page(page, "month-race-night-1280.png")
    page.goto(base_url + f"/manage/workdays/{values['workday_id']}")
    if width in {1280, 320}:
        _capture_page(page, f"builder-{width}.png")
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
    if width > 760:
        for selector in ('input[name="title"]', 'textarea[name="day_note"]', 'select[name="track_id"]'):
            page.locator(selector).focus()
            guarded_url = page.url
            page.keyboard.press("n")
            assert page.url == guarded_url
    assert not errors
    context.close()


@pytest.mark.parametrize("width", [1280, 320])
def test_notice_holiday_hours_and_fresh_auth_browser_flows(browser_site, width: int) -> None:  # type: ignore[no-untyped-def]
    browser, base_url, values = browser_site
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        if branding:
            branding.product_name = "Demo it"
            db.commit()

    manager_context = browser.new_context(viewport={"width": width, "height": 900})
    page = manager_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["manager"])
    page.goto(base_url + "/month")
    fixture_notice = page.locator(".crew-notice-strip")
    assert fixture_notice.is_visible()
    assert page.get_by_text("Global browser crew notice", exact=True).count() == 0
    assert page.get_by_text(values["expired_notice_text"], exact=True).count() == 0
    notice_box = fixture_notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    if width == 320:
        assert notice_box["height"] < 50

    build_id = page.locator("body").get_attribute("data-build-id")
    assert build_id
    shared_asset_urls = page.eval_on_selector_all(
        'link[rel="stylesheet"], script[src]',
        "elements => elements.map(element => element.href || element.src).filter(url => url.includes('/static/'))",
    )
    assert len(shared_asset_urls) == 7
    assert any("/static/track-palette.css?v=" in url for url in shared_asset_urls)
    assert all(f"?v={build_id}" in url for url in shared_asset_urls)

    settings_link = page.locator('a[aria-label="Settings"]')
    gear = settings_link.locator('svg[viewBox="0 0 24 24"]')
    assert gear.is_visible()
    gear_box = gear.bounding_box()
    settings_box = settings_link.bounding_box()
    assert gear_box and settings_box and gear_box["width"] > 0 and gear_box["height"] > 0
    assert gear_box["x"] >= settings_box["x"] and gear_box["y"] >= settings_box["y"]
    assert gear_box["x"] + gear_box["width"] <= settings_box["x"] + settings_box["width"]
    assert gear_box["y"] + gear_box["height"] <= settings_box["y"] + settings_box["height"]
    _capture_locator(page, ".site-header", f"header-settings-gear-{width}.png")

    for product_name in ("Demo it", "On Track", "Trackside Crew"):
        with SessionLocal() as db:
            branding = db.get(SystemBranding, 1)
            assert branding
            branding.product_name = product_name
            db.commit()
        page.reload()
        brand_text = page.locator(".brand > strong")
        assert brand_text.is_visible()
        assert brand_text.inner_text() == product_name
        assert brand_text.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        _assert_no_horizontal_overflow(page)
    with SessionLocal() as db:
        branding = db.get(SystemBranding, 1)
        assert branding
        branding.product_name = "Demo it"
        db.commit()

    page.goto(base_url + "/settings#notices")
    assert page.get_by_text(values["active_notice_text"], exact=True).count() == 1
    assert page.get_by_text(values["expired_notice_text"], exact=True).count() == 1
    page.locator("#notices > details > summary").click()
    page.locator('#notices select[name="scope"]').select_option("REGION")
    page.locator('#notices select[name="region_id"]').select_option(values["region_id"])
    notice_text = f"Call time moved for browser review {width}"
    page.locator('#notices textarea[name="message"]').fill(notice_text)
    page.locator('#notices button', has_text="Create notice").click()
    page.wait_for_url("**/settings?notice=created*", wait_until="domcontentloaded")
    _select_theme(page, base_url, "race-night")
    page.goto(base_url + "/month")
    notice = page.locator(".crew-notice-strip", has_text=notice_text)
    assert notice.is_visible()
    notice_box = notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    _assert_no_horizontal_overflow(page)
    _capture_page(page, f"race-night-personal-month-notice-{width}.png")
    page.goto(base_url + "/crew")
    notice = page.locator(".crew-notice-strip", has_text=notice_text)
    assert notice.is_visible()
    notice_box = notice.bounding_box()
    calendar_box = page.locator(".calendar-grid").bounding_box()
    assert notice_box and calendar_box and notice_box["y"] < calendar_box["y"]
    _capture_page(page, f"race-night-crew-month-notice-{width}.png")

    page.goto(base_url + "/month?year=2026&month=9")
    brand = page.get_by_role("link", name="Demo it home")
    assert brand.is_visible()
    page.get_by_role("link", name="Next month").click()
    page.wait_for_url("**/month?year=2026&month=10*")
    assert page.locator(".month-nav > strong").inner_text() == "October 2026"
    page.get_by_role("link", name="Demo it home").click()
    page.wait_for_url(base_url + "/month")
    assert page.locator(".month-nav > strong").inner_text() == date.today().strftime("%B %Y")
    _capture_page(page, f"brand-current-month-return-{width}.png")
    assert not errors
    manager_context.close()

    admin_context = browser.new_context(viewport={"width": width, "height": 900})
    page = admin_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["admin"])
    page.goto(base_url + "/settings")
    raw_session = next(
        cookie["value"] for cookie in admin_context.cookies() if cookie["name"] == "ontrack_session"
    )
    with SessionLocal() as db:
        device = db.scalar(
            select(TrustedDevice)
            .where(
                TrustedDevice.token_hash == token_hash(raw_session),
                TrustedDevice.revoked_at.is_(None),
            )
        )
        assert device
        device.primary_authenticated_at = utcnow() - timedelta(hours=1)
        db.commit()
    page.goto(base_url + "/admin#branding")
    page.get_by_text("Send a one-time invitation", exact=True).click()
    invitation_form = page.locator('form[action="/admin/invitations"]')
    invitation_form.locator('input[name="display_name"]').fill("Fresh auth candidate")
    invitation_form.locator('input[name="email"]').fill(f"fresh-{width}@example.test")
    invitation_form.locator('select[name="role"]').select_option("EMPLOYEE")
    invitation_form.locator('select[name="region_id"]').select_option(values["region_id"])
    invitation_form.get_by_role("button", name="Create invitation").click()
    page.wait_for_url("**/settings?reauth=required*", wait_until="domcontentloaded")
    assert page.get_by_text("The privileged action will not be replayed automatically.").is_visible()
    page.locator('#reauthenticate input[name="credential"]').fill(values["admin"][1])
    page.locator("#reauthenticate button", has_text="Re-authenticate").click()
    page.wait_for_url("**/admin", wait_until="domcontentloaded")
    assert page.locator('input[name="product_name"]').input_value() == "Demo it"
    assert page.get_by_text("Fresh auth candidate", exact=True).count() == 0
    assert not errors
    admin_context.close()

    employee_context = browser.new_context(viewport={"width": width, "height": 900})
    page = employee_context.new_page()
    errors = _watch_browser_errors(page)
    _login(page, base_url, values["employee"])
    page.goto(base_url + "/month")
    assert page.locator(".timesheet-dot").count() >= 1
    page.goto(base_url + "/settings")
    assert page.locator('input[name="weekly_digest"]').is_checked()
    page.goto(base_url + "/hours")
    assert page.locator(".hours-total strong").inner_text() != "0h 0m"
    page.goto(base_url + "/month?year=2026&month=1")
    holiday = page.locator(".holiday-marker").first
    assert page.locator('.holiday-marker summary[title*="Auckland Anniversary Day"]').count() == 1
    holiday.locator("summary").click()
    assert holiday.locator(".holiday-popover").is_visible()
    _assert_no_horizontal_overflow(page)
    if width in {1280, 320}:
        _capture_page(page, f"holiday-popover-{width}.png")
    assert not errors
    employee_context.close()
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
    if width in {1280, 320}:
        _capture_page(page, f"admin-{width}.png")
    configured_name = "Trackside Operations Rostering Portal"
    page.locator('input[name="product_name"]').fill(configured_name)
    page.get_by_role("button", name="Save product name").click()
    page.wait_for_url("**/admin#branding")
    assert page.locator(".brand > strong:first-child").inner_text() == configured_name
    assert configured_name in page.title()
    _assert_no_horizontal_overflow(page)
    assert page.locator(".brand-compact").count() == 0
    assert page.locator(".brand > strong:first-child").is_visible()
    page.goto(base_url + "/manage/catalog")
    assert page.locator(".brand > strong:first-child").inner_text() == configured_name
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
    page.goto(base_url + f"/day/{values['workday_id']}")
    page.evaluate(
        """async (workdayId) => {
          const response = await fetch("/api/day/" + workdayId, {
            headers: {"X-OnTrack-Prefetch": "1"},
          });
          if (!response.ok) throw new Error("personal Day cache warm failed");
          await response.json();
        }""",
        values["workday_id"],
    )
    page.wait_for_function(
        """async (workdayId) => {
          const shellNames = (await caches.keys()).filter((key) => key.startsWith("ontrack-shell-v3-"));
          if (shellNames.length !== 1) return false;
          const shell = await caches.open(shellNames[0]);
          const marker = await shell.match("/__ontrack_active_user");
          if (!marker) return false;
          const namespace = await marker.text();
          const roster = await caches.open("ontrack-roster-" + namespace);
          return Boolean(await roster.match("/api/day/" + workdayId));
        }""",
        arg=values["workday_id"],
        timeout=10_000,
    )
    context.set_offline(True)
    page.goto(
        base_url + f"/day/{values['workday_id']}?offline-test=1",
        wait_until="domcontentloaded",
    )
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
