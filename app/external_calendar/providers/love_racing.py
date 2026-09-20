from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser

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


class _EventTileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.tile_depth: int | None = None
        self.capture: str | None = None
        self.capture_depth: int | None = None
        self.current: dict[str, str] = {}
        self.rows: list[dict[str, str]] = []

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

    def handle_data(self, data: str) -> None:
        if self.tile_depth is not None and self.capture and data.strip():
            self.current[self.capture] = f"{self.current.get(self.capture, '')} {data.strip()}".strip()

    def handle_endtag(self, tag: str) -> None:
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
        body = " ".join(row["body"].split())
        track = body.split(",")[-1].strip()
        if not track:
            warnings.append("Love Racing event tile had no venue.")
            continue
        provider_id = f"race:{event_date.isoformat()}:{normalized_key(track)}"
        observations.append(
            ProviderObservation(
                provider="LOVE_RACING",
                provider_event_id=provider_id,
                event_date=event_date,
                source_track_name=track,
                discipline="THOROUGHBRED",
                event_kind="RACE",
                facts={"status": "SCHEDULED"},
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
            kind = "TRIAL" if record["WebMeetingType"] == "T" else "RACE"
            millis = int(re.search(r"-?\d+", str(record["RaceDate"])).group())
            event_date = datetime.fromtimestamp(millis / 1000, tz=UTC).date()
            track = str(record["Racecourse"]).strip()
            provider_id = str(record.get("DayID") or f"{kind.lower()}:{event_date}:{normalized_key(track)}")
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
                facts={"status": "SCHEDULED"},
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
