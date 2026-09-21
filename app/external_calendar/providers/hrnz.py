from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from app.external_calendar.adapters import NormalizedProviderResult
from app.external_calendar.http import SourceHTTPClient, SourceHTTPError
from app.external_calendar.service import ProviderObservation, normalized_key

RACE_ICS_URL = "https://infohorse.hrnz.co.nz/datahrs/calendar/HRNZOfficialMeetings.ics"
RACE_DATES_INDEX_URL = "https://infohorse.hrnz.co.nz/datahrs/calendar/Raceday/dates_index.htm"
PROGRAMMES_URL = "https://infohorse.hrnz.co.nz/datahrs/programmes/programm.htm"
TRIALS_DIARY_URL = "https://www.hrnz.co.nz/racing/race-programmes/trials-diary/"
_INFOHORSE_HOST = "infohorse.hrnz.co.nz"
_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    )
    if name
}


@dataclass(frozen=True)
class RaceMonthLink:
    month: date
    url: str


@dataclass(frozen=True)
class ProgrammeEntry:
    event_date: date
    club: str
    title: str
    programme_id: str | None
    tentative: bool
    venue: str | None


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_href: str | None = None
        self.current_text = ""
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = ""

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text = f"{self.current_text} {' '.join(data.split())}".strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href is not None:
            self.links.append((self.current_href, self.current_text))
            self.current_href = None
            self.current_text = ""


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cell_text = ""
        self.cell_links: list[tuple[str, str]] = []
        self.link_href: str | None = None
        self.link_text = ""
        self.row: list[dict[str, object]] = []
        self.rows: list[list[dict[str, object]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_text = ""
            self.cell_links = []
        elif self.in_cell and tag == "a":
            self.link_href = dict(attrs).get("href")
            self.link_text = ""

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self.in_cell and text:
            self.cell_text = f"{self.cell_text} {text}".strip()
            if self.link_href is not None:
                self.link_text = f"{self.link_text} {text}".strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.link_href is not None:
            self.cell_links.append((self.link_href, self.link_text))
            self.link_href = None
            self.link_text = ""
        elif tag in {"td", "th"} and self.in_cell:
            self.row.append({"text": self.cell_text, "links": list(self.cell_links)})
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False


def _allowed_infohorse_url(url: str, *, prefix: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _INFOHORSE_HOST
        and parsed.path.lower().startswith(prefix.lower())
        and parsed.query == ""
    )


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def parse_race_dates_index(html: str, *, start: date, end: date) -> list[RaceMonthLink]:
    parser = _LinkParser()
    parser.feed(html)
    links: dict[date, RaceMonthLink] = {}
    for href, label in parser.links:
        match = re.fullmatch(r"\s*([A-Za-z]+)\s+(\d{4})\s*", label)
        if not match or match.group(1).lower() not in _MONTHS:
            continue
        month = date(int(match.group(2)), _MONTHS[match.group(1).lower()], 1)
        url = urljoin(RACE_DATES_INDEX_URL, href)
        if not _allowed_infohorse_url(url, prefix="/datahrs/calendar/raceday/"):
            continue
        if month <= end and _next_month(month) > start:
            links[month] = RaceMonthLink(month, url)
    if not links:
        raise ValueError("HRNZ Racing Dates index contained no allowed month links in range.")
    return [links[key] for key in sorted(links)]


def _explicit_venue(text: str) -> str | None:
    for pattern in (
        r"(?i)\bmoved\s+to\s+([A-Za-z][A-Za-z .'-]*?)(?:\)|$)",
        r"(?i)\bATC\s+at\s+([A-Za-z][A-Za-z .'-]*?)(?:\)|$)",
    ):
        found = re.search(pattern, text)
        if found:
            return found.group(1).strip()
    return None


def _programme_venue(title: str) -> str | None:
    for pattern in (
        r"(?i)\((?:AT|MOVED\s+TO)\s+([A-Za-z][A-Za-z .'-]+)\)\s*$",
        r"(?i)(?:@|\bAT)\s+([A-Za-z][A-Za-z .'-]+)\s*$",
    ):
        found = re.search(pattern, title)
        if found:
            return found.group(1).strip()
    return None


def parse_programme_index(html: str) -> tuple[list[ProgrammeEntry], list[str]]:
    parser = _TableParser()
    parser.feed(html)
    entries: list[ProgrammeEntry] = []
    warnings: list[str] = []
    previous_club = ""
    for row in parser.rows:
        if len(row) < 3:
            continue
        texts = [str(cell["text"]).strip() for cell in row]
        date_index = next((i for i, value in enumerate(texts) if re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b", value)), None)
        linked = next(
            (
                (href, text)
                for cell in row
                for href, text in cell["links"]
                if text
                and _allowed_infohorse_url(
                    urljoin(PROGRAMMES_URL, href), prefix="/datahrs/programmes/"
                )
            ),
            None,
        )
        if date_index is None or linked is None:
            continue
        date_match = re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b", texts[date_index])
        try:
            event_date = datetime.strptime(date_match.group(), "%d %b %Y").date()  # type: ignore[union-attr]
        except ValueError:
            warnings.append("HRNZ programme row had an invalid date.")
            continue
        club = texts[0] or previous_club
        if club:
            previous_club = club
        if not club:
            warnings.append(f"HRNZ programme for {event_date} had no club identity.")
            continue
        href, title = linked
        url = urljoin(PROGRAMMES_URL, href)
        programme_id = None
        if _allowed_infohorse_url(url, prefix="/datahrs/programmes/"):
            programme_id = urlparse(url).path.rsplit("/", 1)[-1].split(".", 1)[0]
        entries.append(
            ProgrammeEntry(
                event_date=event_date,
                club=club,
                title=title.strip(),
                programme_id=programme_id,
                tentative=any("tentative" in value.lower() for value in texts),
                venue=_programme_venue(title),
            )
        )
    if not entries:
        warnings.append("HRNZ programme index contained no recognisable entries.")
    return entries, warnings


def parse_race_dates_month(
    html: str,
    *,
    month: date,
    programmes: list[ProgrammeEntry] | None = None,
) -> tuple[list[ProviderObservation], list[str]]:
    parser = _TableParser()
    parser.feed(html)
    output: list[ProviderObservation] = []
    warnings: list[str] = []
    programmes = programmes or []
    for row in parser.rows:
        if len(row) < 3:
            continue
        texts = [str(cell["text"]).strip() for cell in row]
        if not re.fullmatch(r"\d{1,2}", texts[0]):
            continue
        club_cell = row[-1]
        linked_club = next((text for _href, text in club_cell["links"] if text), "")
        club = linked_club.strip()
        if not club:
            warnings.append(f"HRNZ Racing Dates row {texts[0]} {month:%B %Y} had no club.")
            continue
        try:
            event_date = date(month.year, month.month, int(texts[0]))
        except ValueError:
            warnings.append(f"HRNZ Racing Dates row had an invalid date in {month:%B %Y}.")
            continue
        marker = next((value for value in texts[1:-1] if value in {"*", "+"}), "")
        row_text = texts[-1]
        explicit_venue = _explicit_venue(row_text)
        candidates = [
            entry
            for entry in programmes
            if entry.event_date == event_date and normalized_key(entry.club) == normalized_key(club)
        ]
        programme = candidates[0] if len(candidates) == 1 else None
        if len(candidates) > 1:
            warnings.append(f"HRNZ programme match was ambiguous for {club} on {event_date}.")
        venue = explicit_venue or (programme.venue if programme else None)
        tentative = "(P)" in row_text.upper() or bool(programme and programme.tentative)
        facts: dict[str, object] = {
            "status": "SCHEDULED",
            "club": club,
            "venue_confidence": "EXPLICIT" if venue else "CLUB_ONLY",
            "tentative": tentative,
        }
        if marker == "*":
            facts["meeting_period"] = "NIGHT"
        elif marker == "+":
            facts["meeting_period"] = "TWILIGHT"
        if venue:
            facts["venue_evidence"] = venue
        if programme:
            facts["programme_title"] = programme.title
            if programme.programme_id:
                facts["programme_id"] = programme.programme_id
        source_name = venue or club
        output.append(
            ProviderObservation(
                provider="HRNZ",
                provider_event_id=f"race-html:{event_date}:{normalized_key(club)}",
                event_date=event_date,
                source_track_name=source_name,
                discipline="HARNESS",
                event_kind="RACE",
                facts=facts,
                raw_payload={
                    "club": club,
                    "row": row_text,
                    "meeting_marker": marker or None,
                    "programme_id": programme.programme_id if programme else None,
                    "programme_title": programme.title if programme else None,
                },
            )
        )
    if not output:
        warnings.append(f"HRNZ Racing Dates page for {month:%B %Y} contained no recognisable meetings.")
    return output, warnings


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

    def _get_infohorse(self, url: str, *, accept: str, prefix: str) -> str:
        if not _allowed_infohorse_url(url, prefix=prefix):
            raise SourceHTTPError("HRNZ source URL was outside the allowlist.")
        response = self.client.get(url, accept=accept)
        final_url = getattr(response, "url", url)
        if not _allowed_infohorse_url(final_url, prefix=prefix):
            raise SourceHTTPError("HRNZ source redirected outside the allowlist.")
        return response.text

    def fetch(self, start: date, end: date) -> dict[str, object]:
        payload: dict[str, object] = {"errors": {}}
        try:
            race_ics = self._get_infohorse(
                RACE_ICS_URL,
                accept="text/calendar,text/plain",
                prefix="/datahrs/calendar/",
            )
            try:
                parsed_ics, _ics_warnings = parse_race_ics(race_ics)
            except ValueError as exc:
                raise SourceHTTPError(str(exc)) from exc
            if not parsed_ics:
                raise SourceHTTPError("HRNZ racing calendar contained no usable meetings.")
            payload["race_ics"] = race_ics
        except SourceHTTPError as exc:
            payload["errors"]["race_ics"] = str(exc)  # type: ignore[index]
            try:
                index_html = self._get_infohorse(
                    RACE_DATES_INDEX_URL,
                    accept="text/html",
                    prefix="/datahrs/calendar/raceday/",
                )
                month_links = parse_race_dates_index(index_html, start=start, end=end)
                months: dict[str, str] = {}
                for link in month_links:
                    try:
                        months[link.month.isoformat()] = self._get_infohorse(
                            link.url,
                            accept="text/html",
                            prefix="/datahrs/calendar/raceday/",
                        )
                    except SourceHTTPError as month_exc:
                        payload["errors"][f"race_month_{link.month:%Y_%m}"] = str(  # type: ignore[index]
                            month_exc
                        )
                payload["race_months"] = months
                try:
                    payload["programmes"] = self._get_infohorse(
                        PROGRAMMES_URL,
                        accept="text/html",
                        prefix="/datahrs/programmes/",
                    )
                except SourceHTTPError as programme_exc:
                    payload["errors"]["race_programmes"] = str(programme_exc)  # type: ignore[index]
            except (SourceHTTPError, ValueError) as fallback_exc:
                payload["errors"]["race_dates_fallback"] = str(fallback_exc)  # type: ignore[index]
        try:
            payload["trials"] = self.client.get(TRIALS_DIARY_URL, accept="text/html").text
        except SourceHTTPError as exc:
            payload["errors"]["trials"] = str(exc)  # type: ignore[index]
        return payload

    def normalize(self, payload: object, start: date, end: date) -> NormalizedProviderResult:
        if not isinstance(payload, dict):
            raise ValueError("HRNZ payload was invalid.")
        output: list[ProviderObservation] = []
        errors = payload.get("errors") if isinstance(payload.get("errors"), dict) else {}
        warnings: list[str] = []
        components: dict[str, str] = {}

        if isinstance(payload.get("race_ics"), str):
            try:
                parsed, parser_warnings = parse_race_ics(payload["race_ics"])
                output.extend(parsed)
                warnings.extend(parser_warnings)
                components["races"] = "PARTIAL" if parser_warnings else "OK"
            except ValueError as exc:
                warnings.append(str(exc))
                components["races"] = "ERROR"
        elif isinstance(payload.get("race_months"), dict):
            programmes: list[ProgrammeEntry] = []
            fallback_warnings: list[str] = []
            if isinstance(payload.get("programmes"), str):
                programmes, programme_warnings = parse_programme_index(payload["programmes"])
                fallback_warnings.extend(programme_warnings)
            elif "race_programmes" in errors:
                fallback_warnings.append("HRNZ programme enrichment was unavailable.")
            for key, html in payload["race_months"].items():
                if not isinstance(key, str) or not isinstance(html, str):
                    continue
                try:
                    month = date.fromisoformat(key)
                except ValueError:
                    fallback_warnings.append("HRNZ fallback payload used an invalid month key.")
                    continue
                parsed, parser_warnings = parse_race_dates_month(
                    html, month=month, programmes=programmes
                )
                output.extend(parsed)
                fallback_warnings.extend(parser_warnings)
            warnings.extend(fallback_warnings)
            race_errors = [key for key in errors if key.startswith("race_month_")]
            warnings.extend(
                f"HRNZ Racing Dates month {key.removeprefix('race_month_')} was unavailable."
                for key in race_errors
            )
            if any(item.event_kind == "RACE" for item in output):
                components["races"] = (
                    "PARTIAL_FALLBACK" if fallback_warnings or race_errors else "OK_FALLBACK"
                )
            else:
                components["races"] = "ERROR"
        else:
            components["races"] = "ERROR"
            for key in ("race_ics", "race_dates_fallback"):
                if key in errors:
                    warnings.append(str(errors[key]))

        if isinstance(payload.get("trials"), str):
            try:
                parsed, parser_warnings = parse_trials_diary(payload["trials"], reference=start)
                output.extend(parsed)
                warnings.extend(parser_warnings)
                components["trials"] = "PARTIAL" if parser_warnings else "OK"
            except ValueError as exc:
                warnings.append(str(exc))
                components["trials"] = "ERROR"
        else:
            components["trials"] = "ERROR"
            if "trials" in errors:
                warnings.append(str(errors["trials"]))
        output = [item for item in output if start <= item.event_date <= end]
        if not output:
            raise ValueError("HRNZ returned no usable calendar observations.")
        return NormalizedProviderResult(output, warnings, components)
