from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from app.external_calendar.adapters import NormalizedProviderResult
from app.external_calendar.http import SourceHTTPClient, SourceHTTPError
from app.external_calendar.service import ProviderObservation, normalized_key

EVENTS_URL = "https://events.loveracing.nz/"
CALENDAR_URL = "https://loveracing.nz/ServerScript/RaceInfo.aspx/GetCalendarEvents"
_MONTHS = {
    name.upper(): number
    for number, name in enumerate(
        (
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
    )
    if name
}
_MEETING_TYPES = {"P": "RACE", "R": "RACE", "T": "TRIAL"}
_NEW_ZEALAND = ZoneInfo("Pacific/Auckland")
_DAY_ID = re.compile(r"^[1-9]\d{0,11}$")
_MEETING_PATH = re.compile(
    r"^/RaceInfo/(?P<day_id>[1-9]\d{0,11})/Meeting-Overview\.aspx$",
    re.IGNORECASE,
)


def love_racing_day_id(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _DAY_ID.fullmatch(candidate) else None


def official_day_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    try:
        parsed = urlsplit(urljoin(EVENTS_URL, href))
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    match = _MEETING_PATH.fullmatch(parsed.path)
    if not (
        parsed.scheme.casefold() == "https"
        and host in {"loveracing.nz", "www.loveracing.nz"}
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.query
        and not parsed.fragment
        and match
    ):
        return None
    return love_racing_day_id(match.group("day_id"))


class _EventTileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.tile_depth: int | None = None
        self.capture: str | None = None
        self.capture_depth: int | None = None
        self.current: dict[str, str] = {}
        self.rows: list[dict[str, str]] = []
        self.body_paragraph_depth: int | None = None
        self.body_paragraph_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        self.depth += 1
        if tag == "div" and "tile" in classes and self.tile_depth is None:
            self.tile_depth = self.depth
            self.current = {}
        if self.tile_depth is not None:
            if "tile__heading" in classes:
                self.capture = "heading"
                self.capture_depth = self.depth
            elif "tile__info-string" in classes:
                self.capture = "date"
                self.capture_depth = self.depth
            elif "tile__body" in classes:
                self.capture = "body"
                self.capture_depth = self.depth
            elif tag == "a" and values.get("href"):
                self.current.setdefault("href", values["href"] or "")
            if (
                tag == "p"
                and self.capture == "body"
                and "venue" not in self.current
                and self.body_paragraph_depth is None
            ):
                self.body_paragraph_depth = self.depth
                self.body_paragraph_text = []

    def handle_data(self, data: str) -> None:
        if self.tile_depth is not None and self.capture and data.strip():
            self.current[self.capture] = f"{self.current.get(self.capture, '')} {data.strip()}".strip()
        if self.body_paragraph_depth is not None and data.strip():
            self.body_paragraph_text.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if self.body_paragraph_depth == self.depth and tag == "p":
            venue = " ".join(self.body_paragraph_text).strip()
            if venue:
                self.current.setdefault("venue", venue)
            self.body_paragraph_depth = None
            self.body_paragraph_text = []
        if self.capture_depth == self.depth:
            self.capture = None
            self.capture_depth = None
        if self.tile_depth is not None and self.depth == self.tile_depth and tag == "div":
            if self.current.get("date") and self.current.get("body"):
                self.rows.append(self.current)
            self.tile_depth = None
            self.capture = None
        self.depth -= 1


def parse_event_tiles(html: str, *, reference: date) -> tuple[list[ProviderObservation], list[str]]:
    parser = _EventTileParser()
    parser.feed(html)
    observations: list[ProviderObservation] = []
    warnings: list[str] = []
    previous_month = reference.month
    year = reference.year
    for row in parser.rows:
        match = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?\b", row["date"])
        if not match or match.group(2).upper() not in _MONTHS:
            warnings.append("Love Racing event tile had an unrecognised date.")
            continue
        month = _MONTHS[match.group(2).upper()]
        if match.group(3):
            year = int(match.group(3))
        elif month < previous_month - 6:
            year += 1
        previous_month = month
        try:
            event_date = date(year, month, int(match.group(1)))
        except ValueError:
            warnings.append("Love Racing event tile had an invalid date.")
            continue
        body = " ".join((row.get("venue") or row["body"]).split())
        track = body.split(",")[-1].strip()
        if not track:
            warnings.append("Love Racing event tile had no venue.")
            continue
        provider_id = official_day_id_from_href(row.get("href")) or (
            f"fallback:race:{event_date.isoformat()}:{normalized_key(track)}"
        )
        observations.append(
            ProviderObservation(
                provider="LOVE_RACING",
                provider_event_id=provider_id,
                event_date=event_date,
                source_track_name=track,
                discipline="THOROUGHBRED",
                event_kind="RACE",
                facts={"status": "SCHEDULED", "meeting_name": row.get("heading")},
                raw_payload={"title": row.get("heading"), "date": row["date"], "venue": body, "url": row.get("href")},
            )
        )
    return observations, warnings


def parse_calendar_json(raw: str) -> tuple[list[ProviderObservation], list[str]]:
    try:
        outer = json.loads(raw)
        records = json.loads(outer["d"]) if isinstance(outer, dict) and isinstance(outer.get("d"), str) else outer
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Love Racing calendar response was not valid JSON.") from exc
    if not isinstance(records, list):
        raise ValueError("Love Racing calendar response did not contain an event list.")
    output: list[ProviderObservation] = []
    warnings: list[str] = []
    for record in records:
        try:
            meeting_type = str(record["WebMeetingType"]).strip().upper()
            if meeting_type not in _MEETING_TYPES:
                warnings.append(
                    f"Love Racing calendar record used unknown WebMeetingType {meeting_type or '(blank)'}."
                )
                continue
            kind = _MEETING_TYPES[meeting_type]
            millis = int(re.search(r"-?\d+", str(record["RaceDate"])).group())
            # RaceInfo encodes NZ midnight as an absolute ASP.NET timestamp.
            event_date = datetime.fromtimestamp(millis / 1000, tz=UTC).astimezone(_NEW_ZEALAND).date()
            track = str(record["Racecourse"]).strip()
            provider_id = love_racing_day_id(record.get("DayID")) or (
                f"calendar:{kind.lower()}:{event_date}:{normalized_key(track)}"
            )
            if not track:
                raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError):
            warnings.append("Love Racing calendar record was malformed.")
            continue
        output.append(
            ProviderObservation(
                provider="LOVE_RACING",
                provider_event_id=provider_id,
                event_date=event_date,
                source_track_name=track,
                discipline="THOROUGHBRED",
                event_kind=kind,
                facts={
                    "status": "SCHEDULED",
                    "meeting_name": str(record.get("Club") or "").strip() or None,
                },
                raw_payload={key: record.get(key) for key in ("DayID", "RaceDate", "WebMeetingType", "Racecourse", "Club")},
            )
        )
    return output, warnings


class LoveRacingAdapter:
    provider = "LOVE_RACING"

    def __init__(self, client: SourceHTTPClient):
        self.client = client

    def fetch(self, start: date, end: date) -> dict[str, object]:
        payload: dict[str, object] = {"reference": start.isoformat(), "errors": {}}
        try:
            payload["calendar"] = self.client.post_json(
                CALENDAR_URL,
                {"start": start.strftime("%d-%b-%Y"), "end": end.strftime("%d-%b-%Y")},
            ).text
        except SourceHTTPError as exc:
            payload["errors"]["raceinfo_calendar"] = str(exc)  # type: ignore[index]
            try:
                payload["races"] = self.client.get(EVENTS_URL, accept="text/html").text
            except SourceHTTPError as fallback_exc:
                payload["errors"]["race_events"] = str(fallback_exc)  # type: ignore[index]
        return payload

    def normalize(self, payload: object, start: date, end: date) -> NormalizedProviderResult:
        if not isinstance(payload, dict):
            raise ValueError("Love Racing payload was invalid.")
        observations: list[ProviderObservation] = []
        warnings = list((payload.get("errors") or {}).values())
        components = {key: "ERROR" for key in (payload.get("errors") or {})}
        calendar_available = isinstance(payload.get("calendar"), str)
        if calendar_available:
            parsed, parser_warnings = parse_calendar_json(payload["calendar"])
            observations.extend(parsed)
            warnings.extend(parser_warnings)
            components["raceinfo_calendar"] = "PARTIAL" if parser_warnings else "OK"
        if not calendar_available and isinstance(payload.get("races"), str):
            parsed, parser_warnings = parse_event_tiles(payload["races"], reference=start)
            observations.extend(parsed)
            warnings.extend(parser_warnings)
            components["race_events"] = "OK"
        observations = [item for item in observations if start <= item.event_date <= end]
        if not observations:
            raise ValueError("Love Racing returned no usable calendar observations.")
        return NormalizedProviderResult(observations, warnings, components)
