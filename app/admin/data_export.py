from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent, HumanChange
from app.catalog.models import BasePosition, CrewGroup, Region, Track, Vehicle
from app.external_calendar.models import (
    ExternalCalendarEvent,
    ExternalEventObservation,
    ExternalTrackMapping,
)
from app.identity.models import Person, RoleGrant, User, UserPersonLink
from app.rostering.models import (
    Assignment,
    OpenPositionApplication,
    ProgrammeItem,
    Workday,
    WorkdayRevision,
)


def _csv_bytes(rows: Iterable[object], fields: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(fields)
    for row in rows:
        writer.writerow([getattr(row, field) if getattr(row, field) is not None else "" for field in fields])
    return stream.getvalue().encode("utf-8-sig")


def safe_data_export(db: Session) -> bytes:
    """Return business data only; authentication material and raw provider payloads are excluded."""
    exports = (
        ("regions.csv", Region, ("id", "name", "lifecycle", "decline_policy", "statutory_holiday_region")),
        ("tracks.csv", Track, ("id", "region_id", "name", "palette_slot", "map_reference", "lifecycle")),
        ("crew_groups.csv", CrewGroup, ("id", "name", "lifecycle")),
        ("base_positions.csv", BasePosition, ("id", "crew_group_id", "name", "lifecycle")),
        (
            "vehicles.csv",
            Vehicle,
            ("id", "name", "category", "model", "home_region_id", "fuel_type", "lifecycle"),
        ),
        (
            "people.csv",
            Person,
            ("id", "display_name", "email", "home_region_id", "lifecycle", "created_at", "updated_at"),
        ),
        (
            "accounts.csv",
            User,
            ("id", "email", "display_name", "status", "theme", "created_at", "updated_at"),
        ),
        ("account_people.csv", UserPersonLink, ("user_id", "person_id", "linked_at")),
        (
            "role_grants.csv",
            RoleGrant,
            ("id", "user_id", "role", "region_id", "status", "granted_at", "activated_at", "revoked_at"),
        ),
        (
            "workdays.csv",
            Workday,
            (
                "id",
                "region_id",
                "external_event_id",
                "category",
                "current_published_revision_id",
                "current_draft_revision_id",
                "created_at",
            ),
        ),
        (
            "workday_revisions.csv",
            WorkdayRevision,
            (
                "id",
                "workday_id",
                "revision_number",
                "state",
                "based_on_revision_id",
                "work_date",
                "track_id",
                "track_name_snapshot",
                "title",
                "start_time",
                "end_time",
                "on_track_time",
                "first_trial_time",
                "first_race_time",
                "last_race_time",
                "race_count",
                "start_origin",
                "finish_destination",
                "day_note",
                "change_reason",
                "created_at",
                "published_at",
            ),
        ),
        (
            "assignments.csv",
            Assignment,
            (
                "id",
                "revision_id",
                "slot_key",
                "base_position_id",
                "slot_index",
                "display_name_snapshot",
                "person_id",
                "person_name_snapshot",
                "status",
                "start_time",
                "end_time",
                "note",
                "note_private",
                "transport_mode",
                "vehicle_id",
                "vehicle_name_snapshot",
                "custom_transport_text",
                "accommodation_name",
            ),
        ),
        (
            "external_events.csv",
            ExternalCalendarEvent,
            (
                "id",
                "event_date",
                "track_id",
                "external_track_name",
                "discipline",
                "event_kind",
                "status",
                "first_trial_time",
                "first_race_time",
                "last_race_time",
                "race_count",
                "presentation_provider",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "open_position_applications.csv",
            OpenPositionApplication,
            (
                "id", "revision_id", "slot_key", "person_id", "status",
                "selected_draft_revision_id", "created_at", "decided_at",
            ),
        ),
        (
            "programme_items.csv",
            ProgrammeItem,
            (
                "id", "revision_id", "kind", "sequence", "operational_time",
                "source_time", "source_provider", "source_retrieved_at", "manual_override",
            ),
        ),
        (
            "external_observations.csv",
            ExternalEventObservation,
            (
                "id", "event_id", "provider", "provider_event_id", "retrieved_at",
                "source_track_name", "parsed_facts", "mapping_state", "reconciliation_state",
            ),
        ),
        (
            "external_track_mappings.csv",
            ExternalTrackMapping,
            (
                "id",
                "provider",
                "external_track_key",
                "external_track_name",
                "track_id",
                "confirmed_by_user_id",
                "confirmed_at",
            ),
        ),
        (
            "human_changes.csv",
            HumanChange,
            ("id", "workday_id", "revision_id", "actor_user_id", "summary", "occurred_at"),
        ),
        (
            "audit_events.csv",
            AuditEvent,
            (
                "id", "actor_user_id", "action", "target_type", "target_id",
                "region_id", "detail", "occurred_at",
            ),
        ),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            "On Track business-data export. Credential hashes, tokens, MFA secrets, cookies, push endpoints and raw provider payloads are intentionally excluded.\n",
        )
        for filename, model, fields in exports:
            statement = select(model).order_by(*model.__mapper__.primary_key)
            archive.writestr(filename, _csv_bytes(db.scalars(statement), fields))
    return output.getvalue()
