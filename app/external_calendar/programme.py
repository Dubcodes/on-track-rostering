from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from html.parser import HTMLParser


@dataclass(frozen=True)
class ProgrammeResult:
    meeting_name: str | None
    status: str
    race_count: int | None
    first_race_time: time | None
    last_race_time: time | None
    races: tuple[tuple[int, time | None], ...]
    diagnostics: tuple[str, ...]
    content_hash: str


class _ProgrammeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.table_depth: int | None = None
        self.row_depth: int | None = None
        self.cell_depth: int | None = None
        self.cell_tag = ""
        self.cell_text: list[str] = []
        self.row: list[tuple[str, str]] = []
        self.tables: list[tuple[frozenset[str], list[list[tuple[str, str]]]]] = []
        self.table: list[list[tuple[str, str]]] = []
        self.table_classes: frozenset[str] = frozenset()
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.titles: list[str] = []
        self.capture_title = False
        self.capture_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        self.depth += 1
        if tag == "meta" and (values.get("property") or values.get("name", "")).lower() in {
            "og:title", "twitter:title"
        } and values.get("content"):
            self.titles.append(values["content"])
        if tag == "title":
            self.capture_title = True
        if tag in {"h1", "h2", "h3"}:
            self.capture_heading = True
            self.heading_parts = []
        if tag == "table" and self.table_depth is None:
            self.table_depth = self.depth
            self.table = []
            self.table_classes = frozenset(values.get("class", "").casefold().split())
        elif self.table_depth is not None and tag == "tr" and self.row_depth is None:
            self.row_depth = self.depth
            self.row = []
        elif self.row_depth is not None and tag in {"td", "th"}:
            self.cell_depth = self.depth
            self.cell_tag = tag
            self.cell_text = []

    def handle_data(self, data: str) -> None:
        if self.cell_depth is not None:
            self.cell_text.append(data)
        if self.capture_title:
            self.title_parts.append(data)
        if self.capture_heading:
            self.heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.cell_depth == self.depth and tag in {"td", "th"}:
            self.row.append((self.cell_tag, " ".join("".join(self.cell_text).split())))
            self.cell_depth = None
        if self.row_depth == self.depth and tag == "tr":
            self.table.append(self.row)
            self.row_depth = None
        if self.table_depth == self.depth and tag == "table":
            self.tables.append((self.table_classes, self.table))
            self.table_depth = None
            self.table_classes = frozenset()
        if tag == "title":
            self.capture_title = False
        if tag in {"h1", "h2", "h3"} and self.capture_heading:
            heading = " ".join("".join(self.heading_parts).split())
            if heading:
                self.titles.append(heading)
            self.capture_heading = False
        self.depth -= 1


def _meeting_name(candidates: list[str]) -> str | None:
    generic = {
        "raceinfo",
        "meetings / fields",
        "meeting overview",
        "loveracing",
        "loveracing.nz",
    }
    usable: list[str] = []
    for raw in candidates:
        raw = re.sub(
            r"\s*\bLast\s+updated\s+\d{1,2}/\d{1,2}/\d{4}\s+"
            r"\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)\s*$",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*"
            r"\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        for part in re.split(r"\s*[|–—]\s*", value):
            clean = part.strip(" -|–—:")
            key = clean.casefold()
            if (
                not clean
                or key in generic
                or "meeting overview" in key
                or "meetings / fields" in key
                or "loveracing" in key
            ):
                continue
            if re.search(r"\brace\s+\d+\b", clean, re.IGNORECASE):
                continue
            if 2 <= len(clean) <= 160:
                usable.append(clean)
    if not usable:
        return None
    return max(usable, key=lambda value: ("@" in value, "racing" in value.casefold(), len(value)))


_PROGRAMME_CLOCK = re.compile(
    r"^(?:(?P<hour24>[01]\d|2[0-3]):(?P<minute24>[0-5]\d)|"
    r"(?P<hour12>1[0-2]|[1-9]):(?P<minute12>[0-5]\d)\s*(?P<meridiem>a\.?m\.?|p\.?m\.?))$",
    re.IGNORECASE,
)


def parse_programme_clock(value: str) -> time:
    """Parse the explicit clock formats published by programme providers.

    Roster-entry parsing deliberately has a different, compact input contract.
    Keeping this parser here prevents a public-source convention from widening
    that operational input surface.
    """
    match = _PROGRAMME_CLOCK.fullmatch(" ".join(value.split()))
    if not match:
        raise ValueError("Programme clock must be an unambiguous HH:MM or h:MM am/pm value.")
    if match["hour24"]:
        return time(int(match["hour24"]), int(match["minute24"]))
    hour = int(match["hour12"])
    minute = int(match["minute12"])
    meridiem = match["meridiem"].casefold().replace(".", "")
    if meridiem == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return time(hour, minute)


def parse_love_racing_programme(html: str) -> ProgrammeResult:
    parser = _ProgrammeParser()
    parser.feed(html)
    candidates: dict[int, list[time | None]] = {}
    saw_header = False
    diagnostics: list[str] = []
    header_tables: list[list[list[tuple[str, str]]]] = []
    overview_tables: list[list[list[tuple[str, str]]]] = []
    for classes, table in parser.tables:
        if "overview-info" in classes:
            overview_tables.append(table)
        headers = {text.casefold() for row in table for tag, text in row if tag == "th"}
        if "race" not in headers or not ({"start", "scheduled start"} & headers):
            continue
        saw_header = True
        header_tables.append(table)

    race_tables = overview_tables if saw_header and overview_tables else header_tables
    for table in race_tables:
        for row in table:
            if len(row) < 2 or not row[0][1].isdigit():
                continue
            number = int(row[0][1])
            if number <= 0:
                continue
            clock_text = next((text for _tag, text in row[1:] if text.strip()), "")
            try:
                scheduled_start = parse_programme_clock(clock_text)
            except ValueError:
                scheduled_start = None
                diagnostics.append(f"Race {number} had an invalid scheduled start.")
            candidates.setdefault(number, []).append(scheduled_start)
    races: list[tuple[int, time | None]] = []
    conflicting = False
    for number in sorted(candidates):
        values = {value for value in candidates[number] if value is not None}
        if len(candidates[number]) > 1:
            diagnostics.append(f"Race {number} appeared {len(candidates[number])} times.")
        if len(values) > 1:
            diagnostics.append(f"Race {number} has conflicting scheduled starts.")
            conflicting = True
            continue
        races.append((number, next(iter(values), None)))
    maximum = max(candidates, default=0)
    contiguous = bool(maximum and set(candidates) == set(range(1, maximum + 1)))
    by_number = dict(races)
    scheduled_count = sum(value is not None for value in by_number.values())
    if contiguous and not conflicting and scheduled_count == maximum:
        status = "COMPLETE"
    elif scheduled_count:
        status = "PARTIAL"
    else:
        status = "AWAITING_SCHEDULE"
    if not saw_header:
        diagnostics.append("Race/Start summary header was not found.")
    if candidates and not contiguous:
        diagnostics.append("Race numbers were not a contiguous sequence.")
    return ProgrammeResult(
        meeting_name=_meeting_name([*parser.titles, " ".join(parser.title_parts)]),
        status=status,
        race_count=maximum if contiguous else None,
        first_race_time=by_number.get(1),
        last_race_time=by_number.get(maximum) if maximum else None,
        races=tuple(races),
        diagnostics=tuple(diagnostics),
        content_hash=hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest(),
    )


def programme_refresh_due(event_date, checked_at, status: str, now: datetime, *, next_due=None):  # type: ignore[no-untyped-def]
    if next_due and now < next_due:
        return False, "failure backoff"
    if event_date < now.date():
        return False, "past meeting"
    if status == "COMPLETE":
        race_morning = event_date == now.date() and time(6) <= now.time() < time(11)
        return (bool(race_morning and (checked_at is None or checked_at.date() < now.date())), "race-morning confirmation")
    hours_until = (datetime.combine(event_date, time.min, tzinfo=now.tzinfo) - now).total_seconds() / 3600
    if event_date == now.date():
        interval, reason = timedelta(hours=1), "race-day incomplete"
    elif hours_until <= 24:
        interval, reason = timedelta(hours=2), "inside 24 hours"
    elif hours_until <= 72:
        interval, reason = timedelta(hours=6), "inside 72 hours"
    else:
        interval, reason = timedelta(hours=24), "daily programme check"
    return checked_at is None or now - checked_at >= interval, reason


def failure_backoff(attempts: int) -> timedelta:
    minutes = (15, 30, 60, 120, 240, 360)
    return timedelta(minutes=minutes[min(max(attempts, 1) - 1, len(minutes) - 1)])
