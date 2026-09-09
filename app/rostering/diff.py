from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.rostering.models import Assignment, WorkdayRevision


@dataclass(frozen=True)
class PublicationChange:
    category: str
    label: str
    before: object | None
    after: object | None
    summary: str
    slot_key: uuid.UUID | None = None
    sensitive: bool = False


def _display(value: object | None) -> str:
    if value is None or value == "":
        return "not set"
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value).replace("_", " ").title() if isinstance(value, str) else str(value)


def _field_change(label: str, before: object, after: object) -> PublicationChange:
    sensitive = label == "Normal day notes"
    summary = (
        "Normal day notes were updated."
        if sensitive
        else f"{label} changed from {_display(before)} to {_display(after)}."
    )
    return PublicationChange("workday", label, before, after, summary, sensitive=sensitive)


def publication_diff(
    db: Session, previous: WorkdayRevision | None, draft: WorkdayRevision
) -> list[PublicationChange]:
    changes: list[PublicationChange] = []
    if previous is None:
        changes.append(
            PublicationChange(
                "publication",
                "First publication",
                None,
                draft.revision_number,
                "First publication will make this workday visible to authorized crew.",
            )
        )
    else:
        for attribute, label in (
            ("work_date", "Date"),
            ("title", "Title"),
            ("track_name_snapshot", "Track"),
            ("start_time", "Start"),
            ("on_track_time", "On-track time"),
            ("first_trial_time", "First trial"),
            ("first_race_time", "First race"),
            ("last_race_time", "Last race"),
            ("race_count", "Race count"),
            ("end_time", "Finish"),
            ("day_note", "Normal day notes"),
        ):
            before, after = getattr(previous, attribute), getattr(draft, attribute)
            if before != after:
                changes.append(_field_change(label, before, after))

    old_rows = (
        {
            row.slot_key: row
            for row in db.scalars(
                select(Assignment).where(Assignment.revision_id == previous.id)
            )
        }
        if previous is not None
        else {}
    )
    new_rows = {
        row.slot_key: row
        for row in db.scalars(select(Assignment).where(Assignment.revision_id == draft.id))
    }
    for slot_key in sorted(old_rows.keys() | new_rows.keys(), key=str):
        old, new = old_rows.get(slot_key), new_rows.get(slot_key)
        if old is None and new is not None:
            changes.append(
                PublicationChange(
                    "assignment",
                    new.display_name_snapshot,
                    None,
                    new.person_name_snapshot or new.status,
                    f"{new.display_name_snapshot} was added as {_display(new.person_name_snapshot or new.status)}.",
                    slot_key,
                )
            )
            continue
        if old is not None and new is None:
            changes.append(
                PublicationChange(
                    "assignment",
                    old.display_name_snapshot,
                    old.person_name_snapshot or old.status,
                    None,
                    f"{old.display_name_snapshot} was removed.",
                    slot_key,
                )
            )
            continue
        assert old is not None and new is not None
        role = new.display_name_snapshot or old.display_name_snapshot
        for attribute, label in (
            ("base_position_id", "Base position"),
            ("display_name_snapshot", "Display position"),
            ("slot_index", "Slot number"),
            ("person_name_snapshot", "Person"),
            ("status", "Status"),
            ("start_time", "Start"),
            ("end_time", "Finish"),
            ("note", "Assignment note"),
            ("note_private", "Note visibility"),
            ("vehicle_name_snapshot", "Vehicle"),
            ("accommodation_name", "Accommodation"),
        ):
            before, after = getattr(old, attribute), getattr(new, attribute)
            if before == after:
                continue
            if attribute == "note":
                private = old.note_private or new.note_private
                summary = f"{'Private note' if private else 'Note'} for {role} was updated."
                changes.append(
                    PublicationChange("assignment", f"{role} note", before, after, summary, slot_key, private)
                )
            elif attribute == "note_private":
                summary = (
                    f"Note for {role} changed from "
                    f"{'Private' if before else 'Shared'} to {'Private' if after else 'Shared'}."
                )
                changes.append(
                    PublicationChange("assignment", f"{role} note visibility", before, after, summary, slot_key)
                )
            elif attribute == "person_name_snapshot":
                changes.append(
                    PublicationChange(
                        "assignment", role, before, after,
                        f"{role} changed from {_display(before)} to {_display(after)}.", slot_key,
                    )
                )
            elif attribute == "status":
                changes.append(
                    PublicationChange(
                        "assignment", role, before, after,
                        f"{role} changed from {_display(before)} to {_display(after)}.", slot_key,
                    )
                )
            else:
                changes.append(
                    PublicationChange(
                        "assignment", f"{role} {label}", before, after,
                        f"{label} for {role} changed from {_display(before)} to {_display(after)}.", slot_key,
                    )
                )
    return changes
