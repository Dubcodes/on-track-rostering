from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import BasePosition, Track
from app.core.config import get_settings
from app.core.time import parse_time, utcnow
from app.external_calendar.models import TransitionSourceReference
from app.identity.models import Person
from app.rostering.models import Assignment, Workday, WorkdayRevision
from app.rostering.service import create_workday

SOURCE = "DEPUTY_CAPTURE"
SAFE_IGNORED_FIELDS = (
    "authentication, credentials, cookies, tokens/JWTs",
    "banking, payroll, pay rates/costs and payroll export fields",
    "timesheet approval/pay fields and timesheet identifiers",
    "meal-break, break-slot and other break data",
    "arbitrary captured response bodies",
)
POSITION_ALIASES = {
    "dir": "Director",
    "eng": "Engineer",
    "soundvt": "Sound/VT",
    "sound vt": "Sound/VT",
    "back2": "Back 2",
}


@dataclass
class TransitionPreview:
    counts: dict[str, int] = field(default_factory=dict)
    details: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    valid: bool = True
    payload: dict[str, object] = field(default_factory=dict)


def _json_section(raw: str, heading: str) -> object:
    start = raw.find(heading)
    if start < 0:
        raise ValueError(f"{heading} section was not found.")
    array_start = raw.find("[", start + len(heading))
    if array_start < 0:
        raise ValueError(f"{heading} did not contain a JSON array.")
    try:
        value, _end = json.JSONDecoder().raw_decode(raw[array_start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"{heading} was not valid JSON.") from exc
    if not isinstance(value, list):
        raise ValueError(f"{heading} must be a JSON array.")
    return value


def _first(row: dict[str, object], *keys: str, default=None):  # type: ignore[no-untyped-def]
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _timestamp(value: object) -> datetime:
    if isinstance(value, int | float) or str(value).isdigit():
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=UTC)
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("A published shift had an invalid start/end timestamp.") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _safe_area(row: object) -> dict[str, object] | None:
    if not isinstance(row, dict):
        return None
    area_id = _first(row, "areaId", "AreaId", "Id", "id")
    name = _first(row, "areaName", "AreaName", "Name", "name")
    if area_id in (None, "") or not str(name or "").strip():
        return None
    return {
        "id": str(area_id)[:100],
        "name": " ".join(str(name).split())[:100],
        "order": _first(row, "rosterSortOrder", "RosterSortOrder"),
    }


def _safe_shift(row: object, areas: dict[str, dict[str, object]]) -> dict[str, object] | None:
    if not isinstance(row, dict):
        return None
    start = _first(row, "start", "Start", "startTime", "StartTime")
    end = _first(row, "end", "End", "endTime", "EndTime")
    location = _first(row, "locationName", "LocationName", "location", "Location")
    if start in (None, "") or end in (None, "") or not str(location or "").strip():
        return None
    area_id = str(_first(row, "area", "Area", "areaId", "AreaId", default=""))
    area_name = _first(row, "areaName", "AreaName")
    if not area_name and area_id in areas:
        area_name = areas[area_id]["name"]
    employee = _first(row, "employee", "Employee", "employeeId", "EmployeeId", default=0)
    employee_name = _first(
        row,
        "employeeName",
        "EmployeeName",
        "employeeDisplayName",
        "EmployeeDisplayName",
        default="",
    )
    shift_id = _first(row, "shiftId", "ShiftId", "Id", "id")
    note = _first(row, "note", "Note", "comment", "Comment", default="")
    return {
        "id": str(shift_id or "")[:100],
        "employee_id": str(employee or "0")[:100],
        "employee_name": " ".join(str(employee_name or "").split())[:120],
        "location": " ".join(str(location).split())[:160],
        "area_id": area_id[:100],
        "area_name": " ".join(str(area_name or "Unclassified").split())[:100],
        "start": _timestamp(start).isoformat(),
        "end": _timestamp(end).isoformat(),
        "note": str(note or "").strip()[:2000],
        "published": _bool(_first(row, "isPublished", "IsPublished", default=False)),
        "open": _bool(_first(row, "isOpen", "IsOpen", default=False)),
    }


def parse_capture(raw: str) -> dict[str, object]:
    if len(raw.encode("utf-8")) > 5_000_000:
        raise ValueError("Deputy transition capture is larger than 5 MB.")
    if not raw.lstrip().startswith("Deputy Web Capture"):
        raise ValueError("This is not a Deputy Web Capture.")
    raw_areas = _json_section(raw, "Schedule Area References")
    areas = {
        str(item["id"]): item
        for row in raw_areas
        if (item := _safe_area(row)) is not None
    }
    raw_shifts = _json_section(raw, "Extracted Schedule Shift Records")
    shifts = [
        item
        for row in raw_shifts
        if (item := _safe_shift(row, areas)) is not None
    ]
    return {"version": 1, "source": SOURCE, "areas": list(areas.values()), "shifts": shifts}


def validate_payload(value: object) -> dict[str, object]:
    """Revalidate the sanitized preview payload before an atomic apply."""
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("source") != SOURCE:
        raise ValueError("The Deputy transition preview payload is invalid or unsupported.")
    raw_shifts = value.get("shifts")
    if not isinstance(raw_shifts, list) or len(raw_shifts) > 50_000:
        raise ValueError("The Deputy transition preview has an invalid shift list.")
    shifts: list[dict[str, object]] = []
    required = {"employee_id", "employee_name", "location", "area_name", "start", "end"}
    for row in raw_shifts:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError("The Deputy transition preview contains an invalid shift row.")
        if not isinstance(row.get("published"), bool) or not isinstance(row.get("open"), bool):
            raise ValueError("The Deputy transition preview contains invalid publication flags.")
        start = _timestamp(row["start"])
        end = _timestamp(row["end"])
        if end <= start:
            raise ValueError("A published shift must end after it starts.")
        shifts.append(
            {
                "id": str(row.get("id") or "")[:100],
                "employee_id": str(row["employee_id"] or "0")[:100],
                "employee_name": " ".join(str(row["employee_name"] or "").split())[:120],
                "location": " ".join(str(row["location"] or "").split())[:160],
                "area_id": str(row.get("area_id") or "")[:100],
                "area_name": " ".join(str(row["area_name"] or "Unclassified").split())[:100],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "note": str(row.get("note") or "").strip()[:2000],
                "published": bool(row.get("published")),
                "open": bool(row.get("open")),
            }
        )
    return {"version": 1, "source": SOURCE, "areas": [], "shifts": shifts}


def _location(value: str) -> tuple[str, str, str] | None:
    label = " ".join(value.split())
    trial = re.match(r"^T-TRIALS?\s*-\s*(.+)$", label, re.IGNORECASE)
    if trial:
        return trial.group(1).strip(), "TRIALS", "THOROUGHBRED"
    thoroughbred = re.match(r"^T-\s*(.+)$", label, re.IGNORECASE)
    if thoroughbred:
        return thoroughbred.group(1).strip(), "RACE_DAY", "THOROUGHBRED"
    harness = re.match(r"^H-\s*(.+)$", label, re.IGNORECASE)
    if harness:
        return harness.group(1).strip(), "RACE_DAY", "HARNESS"
    return None


def _track(db: Session, name: str) -> Track | None:
    aliases = {
        "rotorua": {"rotorua", "arawa park"},
        "cambridge": {"cambridge", "cambridge synthetic"},
        "pukekohe": {"pukekohe", "pukekohe park"},
    }
    key = " ".join(name.casefold().split())
    accepted = aliases.get(key, {key})
    matches = [
        track
        for track in db.scalars(select(Track).where(Track.lifecycle == "ACTIVE"))
        if " ".join(track.name.casefold().split()) in accepted
        or key in aliases.get(" ".join(track.name.casefold().split()), set())
    ]
    return matches[0] if len(matches) == 1 else None


def _reference(db: Session, kind: str, key: str) -> TransitionSourceReference | None:
    return db.scalar(
        select(TransitionSourceReference).where(
            TransitionSourceReference.source == SOURCE,
            TransitionSourceReference.entity_kind == kind,
            TransitionSourceReference.external_key == key,
        )
    )


def _position_name(value: str) -> str:
    clean = " ".join(value.split())
    return POSITION_ALIASES.get(clean.casefold(), clean)


def _local(value: object) -> datetime:
    return _timestamp(value).astimezone(get_settings().timezone)


RACE_NOTE_PATTERNS = (
    re.compile(r"\b(?P<count>\d{1,2})\s+races?\s+(?P<first>\d{3,4})\s*\|\s*(?P<last>\d{3,4})\b", re.I),
    re.compile(r"\bfirst\s+race\s+(?P<first>\d{3,4})\s*\|\s*last\s+race\s+(?P<last>\d{3,4})\b", re.I),
)


def race_note(value: str) -> tuple[int | None, time | None, time | None]:
    for pattern in RACE_NOTE_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        try:
            first = parse_time(match.group("first"))
            last = parse_time(match.group("last"))
        except ValueError:
            return None, None, None
        count = int(match.groupdict().get("count") or 0) or None
        return count, first, last
    return None, None, None


def preview_capture(db: Session, payload: dict[str, object]) -> TransitionPreview:
    payload = validate_payload(payload)
    shifts = [dict(row) for row in payload.get("shifts", []) if isinstance(row, dict)]
    published = [row for row in shifts if row["published"]]
    ignored = len(shifts) - len(published)
    preview = TransitionPreview(
        counts={
            "schedule_rows": len(shifts),
            "published_rows": len(published),
            "unpublished_ignored": ignored,
            "open_positions": sum(bool(row["open"]) for row in published),
        },
        details={"people": [], "tracks": [], "positions": [], "workdays": []},
        warnings=[
            f"{ignored} unpublished schedule row(s) will be ignored.",
            *(f"Ignored: {value}." for value in SAFE_IGNORED_FIELDS),
        ],
        payload=payload,
    )
    people_seen: set[str] = set()
    for row in published:
        employee_id = str(row["employee_id"])
        employee_name = str(row["employee_name"])
        if employee_id != "0" and employee_id not in people_seen:
            people_seen.add(employee_id)
            mapping = _reference(db, "PERSON", employee_id)
            matches = [
                person
                for person in db.scalars(select(Person).where(Person.lifecycle == "ACTIVE"))
                if person.display_name.casefold() == employee_name.casefold()
            ]
            if mapping:
                preview.details["people"].append(f"{employee_name}: already mapped")
            elif len(matches) == 1:
                preview.details["people"].append(f"{employee_name}: match existing Person")
            elif len(matches) > 1 or not employee_name:
                preview.conflicts.append(f"Employee {employee_id} has an ambiguous or missing Person identity.")
                preview.valid = False
            else:
                preview.details["people"].append(f"{employee_name}: create Person (No Primary Region)")
        classified = _location(str(row["location"]))
        if not classified:
            preview.details["tracks"].append(f"{row['location']}: unresolved operational location; skip")
            continue
        track_name, category, _discipline = classified
        track = _track(db, track_name)
        if not track:
            preview.details["tracks"].append(f"{row['location']}: unresolved Track; skip")
            continue
        preview.details["tracks"].append(f"{row['location']}: {track.name}")
        position = _position_name(str(row["area_name"]))
        preview.details["positions"].append(f"{row['area_name']}: {position}")
        work_date = _local(row["start"]).date()
        preview.details["workdays"].append(f"{work_date.isoformat()} · {track.name} · {category}")
    preview.counts["people"] = len(people_seen)
    preview.counts["safe_workdays"] = len(set(preview.details["workdays"]))
    preview.counts["assignments"] = sum(
        1
        for row in published
        if (classified := _location(str(row["location"])))
        and _track(db, classified[0])
    )
    existing_keys: set[tuple[date, uuid.UUID, str]] = set()
    for row in published:
        classified = _location(str(row["location"]))
        if not classified or not (track := _track(db, classified[0])):
            continue
        key = (_local(row["start"]).date(), track.id, classified[1])
        if key in existing_keys:
            continue
        existing_keys.add(key)
        existing = db.scalar(
            select(Workday)
            .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                WorkdayRevision.work_date == key[0],
                WorkdayRevision.track_id == key[1],
                Workday.category == key[2],
            )
        )
        if existing:
            preview.warnings.append(
                f"{key[0].isoformat()} · {track.name} · {key[2]} already has a publication and will not be changed."
            )
    preview.counts["existing_publications"] = sum(
        "already has a publication" in warning for warning in preview.warnings
    )
    return preview


def _payload_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _add_reference(
    db: Session,
    kind: str,
    key: str,
    target_type: str,
    target_id: uuid.UUID,
    payload: object,
) -> None:
    db.add(
        TransitionSourceReference(
            source=SOURCE,
            entity_kind=kind,
            external_key=key,
            target_type=target_type,
            target_id=target_id,
            payload_hash=_payload_hash(payload),
        )
    )


def apply_capture(db: Session, payload: dict[str, object], actor_user_id: uuid.UUID) -> dict[str, int]:
    payload = validate_payload(payload)
    preview = preview_capture(db, payload)
    if not preview.valid:
        raise ValueError("Resolve ambiguous People before applying this transition capture.")
    shifts = [
        dict(row)
        for row in payload.get("shifts", [])
        if isinstance(row, dict) and row.get("published")
    ]
    grouped: dict[tuple[date, uuid.UUID, str, str], list[dict[str, object]]] = {}
    for row in shifts:
        classified = _location(str(row["location"]))
        if not classified:
            continue
        track_name, category, discipline = classified
        track = _track(db, track_name)
        if not track:
            continue
        work_date = _local(row["start"]).date()
        grouped.setdefault((work_date, track.id, category, discipline), []).append(row)

    counts = {"people": 0, "workdays": 0, "assignments": 0, "open": 0, "existing": 0}
    eligible: dict[tuple[date, uuid.UUID, str, str], list[dict[str, object]]] = {}
    for key, rows in grouped.items():
        work_date, track_id, category, _discipline = key
        workday_key = f"{work_date.isoformat()}:{track_id}:{category}"
        existing = db.scalar(
            select(Workday)
            .join(WorkdayRevision, Workday.current_published_revision_id == WorkdayRevision.id)
            .where(
                WorkdayRevision.work_date == work_date,
                WorkdayRevision.track_id == track_id,
                Workday.category == category,
            )
        )
        if _reference(db, "WORKDAY", workday_key) or existing:
            counts["existing"] += 1
        else:
            eligible[key] = rows

    people: dict[str, Person] = {}
    for row in (row for rows in eligible.values() for row in rows):
        employee_id = str(row["employee_id"])
        if employee_id == "0" or employee_id in people:
            continue
        mapped = _reference(db, "PERSON", employee_id)
        if mapped:
            person = db.get(Person, mapped.target_id)
            if person:
                people[employee_id] = person
                continue
        matches = [
            person
            for person in db.scalars(select(Person).where(Person.lifecycle == "ACTIVE"))
            if person.display_name.casefold() == str(row["employee_name"]).casefold()
        ]
        if len(matches) > 1:
            raise ValueError(f"Employee {employee_id} is ambiguous.")
        person = matches[0] if matches else Person(
            display_name=str(row["employee_name"]),
            home_region_id=None,
        )
        if not matches:
            db.add(person)
            db.flush()
        _add_reference(db, "PERSON", employee_id, "PERSON", person.id, row)
        people[employee_id] = person
    counts["people"] = len(people)

    for (work_date, track_id, category, discipline), rows in eligible.items():
        track = db.get(Track, track_id)
        assert track is not None
        workday_key = f"{work_date.isoformat()}:{track_id}:{category}"
        workday = create_workday(
            db,
            region_id=track.region_id,
            category=category,
            work_date=work_date,
            track_id=track.id,
            title=f"{track.name} {'Trials' if category == 'TRIALS' else 'Race Day'}",
            actor_user_id=actor_user_id,
            racing_discipline=discipline,
            commit=False,
        )
        draft = db.get(WorkdayRevision, workday.current_draft_revision_id)
        assert draft is not None
        starts = [_timestamp(row["start"]) for row in rows]
        ends = [_timestamp(row["end"]) for row in rows]
        draft.start_time = min(starts).astimezone(get_settings().timezone).time().replace(tzinfo=None)
        draft.end_time = max(ends).astimezone(get_settings().timezone).time().replace(tzinfo=None)
        note_counts = Counter(str(row["note"]).strip() for row in rows if str(row["note"]).strip())
        shared_notes = {note for note, count in note_counts.items() if count > 1}
        day_notes: set[str] = set(shared_notes)
        for note in note_counts:
            count, first, last = race_note(note)
            if first and last:
                draft.race_count = count
                draft.first_race_time = first
                draft.last_race_time = last
                draft.day_note = note
                day_notes.add(note)
                break
        for row in rows:
            shift_key = str(row["id"] or _payload_hash(row))
            if _reference(db, "ASSIGNMENT", shift_key):
                continue
            position_name = _position_name(str(row["area_name"]))
            position = next(
                (
                    item
                    for item in db.scalars(select(BasePosition).where(BasePosition.lifecycle == "ACTIVE"))
                    if item.name.casefold() == position_name.casefold()
                ),
                None,
            )
            if not position:
                position = BasePosition(name=position_name, crew_group_id=None)
                db.add(position)
                db.flush()
            employee_id = str(row["employee_id"])
            person = None if bool(row["open"]) else people.get(employee_id)
            status = "OPEN" if bool(row["open"]) else ("ASSIGNED" if person else "TBC")
            assignment = Assignment(
                revision_id=draft.id,
                slot_key=uuid.uuid4(),
                base_position_id=position.id,
                display_name_snapshot=position.name,
                person_id=person.id if person else None,
                person_name_snapshot=person.display_name if person else None,
                status=status,
                start_time=_local(row["start"]).time().replace(tzinfo=None),
                end_time=_local(row["end"]).time().replace(tzinfo=None),
                note="" if str(row["note"]).strip() in day_notes else str(row["note"]),
                note_private=False,
            )
            db.add(assignment)
            db.flush()
            _add_reference(db, "ASSIGNMENT", shift_key, "ASSIGNMENT", assignment.id, row)
            counts["assignments"] += 1
            counts["open"] += status == "OPEN"
        draft.state = "PUBLISHED"
        draft.published_at = utcnow()
        draft.published_by_user_id = actor_user_id
        workday.current_published_revision_id = draft.id
        workday.current_draft_revision_id = None
        workday.lock_version += 1
        _add_reference(db, "WORKDAY", workday_key, "WORKDAY", workday.id, rows)
        counts["workdays"] += 1
    db.flush()
    return counts
