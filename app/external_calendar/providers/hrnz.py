from __future__ import annotations

import re
from datetime import date, datetime, time
from html.parser import HTMLParser

from app.external_calendar.adapters import NormalizedProviderResult
from app.external_calendar.http import SourceHTTPClient, SourceHTTPError
from app.external_calendar.service import ProviderObservation, normalized_key

RACE_ICS_URL = "https://infohorse.hrnz.co.nz/datahrs/calendar/HRNZOfficialMeetings.ics"
TRIALS_DIARY_URL = "https://www.hrnz.co.nz/racing/race-programmes/trials-diary/"


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse_race_ics(text: str) -> tuple[list[ProviderObservation], list[str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.split(";", 1)[0]] = value.replace("\\,", ",").strip()
    output: list[ProviderObservation] = []
    warnings: list[str] = []
    for item in events:
        try:
            event_date = datetime.strptime(item["DTSTART"][:8], "%Y%m%d").date()
            track = item.get("LOCATION")
            if not track:
                track = re.sub(r"\s+(?:Races?|Race Meeting)\b.*$", "", item["SUMMARY"], flags=re.I)
            track = track.strip()
            if not track:
                raise ValueError
        except (KeyError, ValueError):
            warnings.append("HRNZ calendar event was malformed.")
            continue
        output.append(
            ProviderObservation(
                provider="HRNZ",
                provider_event_id=item.get("UID") or f"race:{event_date}:{normalized_key(track)}",
                event_date=event_date,
                source_track_name=track,
                discipline="HARNESS",
                event_kind="RACE",
                facts={"status": "SCHEDULED"},
                raw_payload={key: item.get(key) for key in ("UID", "DTSTART", "SUMMARY", "LOCATION")},
            )
        )
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("HRNZ racing calendar was not iCalendar data.")
    return output, warnings


class _DiaryTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture: str | None = None
        self.value = ""
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h2", "h3", "h4", "p", "li"}:
            self.capture = tag
            self.value = ""

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value and self.capture:
            self.value = f"{self.value} {value}".strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == self.capture:
            if self.value:
                self.blocks.append((tag, self.value))
            self.capture = None
            self.value = ""


def parse_trials_diary(html: str, *, reference: date) -> tuple[list[ProviderObservation], list[str]]:
    parser = _DiaryTextParser()
    parser.feed(html)
    # Venue headings followed by dates are the stable public semantics; uncertain prose is skipped.
    month_groups = (
        (), ("jan", "january"), ("feb", "february"), ("mar", "march"),
        ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
        ("aug", "august"), ("sep", "september"), ("oct", "october"),
        ("nov", "november"), ("dec", "december"),
    )
    month_numbers = {
        name.lower(): index for index, names in enumerate(month_groups) for name in names
    }
    token_pattern = re.compile(
        r"(?i)(?P<month>\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b)"
        r"|(?P<day>\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Thurdsay)\s+(?P<number>\d{1,2})(?:st|nd|rd|th)?\b)"
    )
    output: list[ProviderObservation] = []
    warnings: list[str] = []
    seen: set[tuple[date, str]] = set()
    headings = [
        index for index, (tag, _value) in enumerate(parser.blocks) if tag in {"h2", "h3", "h4"}
    ]
    for position, block_index in enumerate(headings):
        venue = parser.blocks[block_index][1].strip()
        if venue.upper() in {"TRIALS DIARY", "TRIALS", "CONTACT", "RACING"} or "GROUPED" in venue.upper():
            continue
        next_index = headings[position + 1] if position + 1 < len(headings) else len(parser.blocks)
        section = "\n".join(value for _tag, value in parser.blocks[block_index + 1 : next_index])
        explicit_time = None
        time_match_values = None
        for pattern in (
            r"(?i)start\s+time\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
            r"(?i)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s+start\s+for\s+all\s+trials",
            r"(?i)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s+start\b",
        ):
            time_match = re.search(pattern, section)
            if time_match:
                time_match_values = time_match.groups()
                break
        if time_match_values:
            hour = int(time_match_values[0]) % 12 + (12 if time_match_values[2].lower() == "pm" else 0)
            explicit_time = time(hour, int(time_match_values[1] or 0))
        current_month: int | None = None
        for line in section.splitlines():
            line_qualifiers = bool(re.search(r"(?i)qualifiers?\s+before\s+races", line))
            tokens = list(token_pattern.finditer(line))
            for token_index, found in enumerate(tokens):
                if found.group("month"):
                    current_month = month_numbers[found.group("month").lower()]
                    continue
                next_month = None
                if token_index + 1 < len(tokens) and tokens[token_index + 1].group("month"):
                    next_month = month_numbers[tokens[token_index + 1].group("month").lower()]
                month = next_month or current_month
                if month is None:
                    warnings.append(f"HRNZ trials date had no month in {venue}.")
                    continue
                year = reference.year + (month < reference.month - 6)
                try:
                    event_date = date(year, month, int(found.group("number")))
                except ValueError:
                    warnings.append(f"HRNZ trials date was invalid in {venue}.")
                    continue
                identity = (event_date, normalized_key(venue))
                if identity in seen:
                    continue
                seen.add(identity)
                facts: dict[str, object] = {"status": "SCHEDULED"}
                if explicit_time and not line_qualifiers:
                    facts["first_trial_time"] = explicit_time.isoformat(timespec="minutes")
                if line_qualifiers:
                    facts["qualifiers_before_races"] = True
                output.append(
                    ProviderObservation(
                        provider="HRNZ",
                        provider_event_id=f"trial:{event_date}:{normalized_key(venue)}",
                        event_date=event_date,
                        source_track_name=venue,
                        discipline="HARNESS",
                        event_kind="TRIAL",
                        facts=facts,
                        raw_payload={"venue": venue, "date_text": found.group(0), "qualifiers_before_races": line_qualifiers, "explicit_start": explicit_time.isoformat(timespec="minutes") if explicit_time and not line_qualifiers else None},
                    )
                )
    if not output:
        warnings.append("HRNZ Trials Diary contained no recognisable venue/date entries.")
    return output, warnings


class HRNZAdapter:
    provider = "HRNZ"

    def __init__(self, client: SourceHTTPClient):
        self.client = client

    def fetch(self, start: date, end: date) -> dict[str, object]:
        payload: dict[str, object] = {"errors": {}}
        for key, url, accept in (
            ("races", RACE_ICS_URL, "text/calendar,text/plain"),
            ("trials", TRIALS_DIARY_URL, "text/html"),
        ):
            try:
                payload[key] = self.client.get(url, accept=accept).text
            except SourceHTTPError as exc:
                payload["errors"][key] = str(exc)  # type: ignore[index]
        return payload

    def normalize(self, payload: object, start: date, end: date) -> NormalizedProviderResult:
        if not isinstance(payload, dict):
            raise ValueError("HRNZ payload was invalid.")
        output: list[ProviderObservation] = []
        warnings = list((payload.get("errors") or {}).values())
        components = {key: "ERROR" for key in (payload.get("errors") or {})}
        parsers = (("races", parse_race_ics), ("trials", lambda value: parse_trials_diary(value, reference=start)))
        for key, parser in parsers:
            if isinstance(payload.get(key), str):
                try:
                    parsed, parser_warnings = parser(payload[key])
                    output.extend(parsed)
                    warnings.extend(parser_warnings)
                    components[key] = "PARTIAL" if parser_warnings else "OK"
                except ValueError as exc:
                    warnings.append(str(exc))
                    components[key] = "ERROR"
        output = [item for item in output if start <= item.event_date <= end]
        if not output:
            raise ValueError("HRNZ returned no usable calendar observations.")
        return NormalizedProviderResult(output, warnings, components)
