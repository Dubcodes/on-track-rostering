from __future__ import annotations

import hashlib
import os
import re
import struct
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Track, TrackMap
from app.core.config import get_settings
from app.core.time import utcnow
from app.external_calendar.http import SourceHTTPClient, SourceHTTPError

LOVE_RACING_ORIGIN = "https://loveracing.nz"
MAX_MANUAL_MAP_BYTES = 15 * 1024 * 1024
MAP_HEADER_BYTES = 64 * 1024
SUCCESSFUL_MAP_REFRESH_INTERVAL = timedelta(days=90)
FAILED_MAP_REFRESH_INTERVAL = timedelta(days=1)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_IMAGE_PATH = re.compile(r"\.(?:jpe?g|png|webp)$", re.IGNORECASE)
COURSES: dict[str, tuple[str, str]] = {
    "rotorua": ("/RaceInfo/Clubs-And-Courses/1/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Arawa%20Park%20copy.jpg"),
    "avondale": ("/RaceInfo/Clubs-And-Courses/4/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Avondale%202.jpg"),
    "cambridge synthetic": ("/RaceInfo/Clubs-And-Courses/7/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg"),
    "ellerslie": ("/RaceInfo/Clubs-And-Courses/9/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png"),
    "matamata": ("/RaceInfo/Clubs-And-Courses/18/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Matamata%20copy.jpg"),
    "pukekohe": ("/RaceInfo/Clubs-And-Courses/26/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Pukekohe%20copy.jpg"),
    "ruakaka": ("/RaceInfo/Clubs-And-Courses/32/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Ruakaka%20copy.jpg"),
    "taupo": ("/RaceInfo/Clubs-And-Courses/34/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Taupo%20copy.jpg"),
    "tauranga": ("/RaceInfo/Clubs-And-Courses/41/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Tauranga-2D_new.jpg"),
    "te aroha": ("/RaceInfo/Clubs-And-Courses/35/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te-Aroha_new.jpg"),
    "te rapa": ("/RaceInfo/Clubs-And-Courses/38/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te%20Rapa%20copy.jpg"),
}


def canonical_track_name(value: str) -> str:
    clean = " ".join(value.strip().split())
    clean = re.sub(r"^[TH]-", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^TRIALS?\s*[-–—:]?\s*", "", clean, flags=re.IGNORECASE)
    aliases = {
        "arawa park": "rotorua",
        "pukekohe park": "pukekohe",
    }
    return aliases.get(clean.casefold(), clean.casefold())


class _CandidateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self._links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "a":
            href = values.get("href", "")
            self._links.append(href)
            if "racecourses/tracks" in unquote(href).casefold() or "/image.ashx" in href.casefold():
                self.values.append(href)
            return
        if tag == "meta" and values.get("property", "").lower() == "og:image":
            candidate = values.get("content", "")
            if "racecourses/tracks" in unquote(candidate).casefold():
                self.values.append(candidate)
            return
        if tag != "img":
            return
        joined = " ".join(values.values()).lower()
        credible = (
            "track - 2d" in values.get("alt", "").casefold()
            or "racecourses/tracks" in joined
            or ("onhorsefiles" in joined and "track" in joined)
        )
        if not credible:
            return
        for key in ("src", "data-src", "data-original"):
            if values.get(key):
                self.values.append(values[key])
        for key in ("srcset", "data-srcset"):
            self.values.extend(
                item.strip().split(" ", 1)[0]
                for item in values.get(key, "").split(",")
                if item.strip()
            )
        if self._links and self._links[-1]:
            self.values.append(self._links[-1])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._links:
            self._links.pop()


def _normalized_https_url(value: str, base_url: str) -> str | None:
    raw = unescape(value.strip())
    if not raw or _CONTROL_CHARACTERS.search(raw) or _INVALID_PERCENT_ESCAPE.search(raw):
        return None
    try:
        parsed = urlsplit(urljoin(base_url, raw))
        port = parsed.port
        decoded_path = unquote(parsed.path, errors="strict")
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or _CONTROL_CHARACTERS.search(decoded_path)
        or any(
            _CONTROL_CHARACTERS.search(key) or _CONTROL_CHARACTERS.search(item)
            for key, item in query_pairs
        )
    ):
        return None
    normalized_path = quote(decoded_path, safe="/!$&'()*+,;=:@-._~")
    normalized_query = urlencode(query_pairs, doseq=True, quote_via=quote, safe="")
    host = parsed.hostname.casefold()
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, normalized_path, normalized_query, ""))


def _trusted_direct_image_url(absolute: str) -> bool:
    parsed = urlsplit(absolute)
    host = (parsed.hostname or "").casefold()
    if host != "loveracing.nz" and not host.endswith(".loveracing.nz"):
        return False
    path = unquote(parsed.path).casefold()
    if not _IMAGE_PATH.search(path) or "/onhorsefiles/racecourses/tracks/" not in path:
        return False
    return not any(word in path for word in ("satellite", "aerial"))


def trusted_image_url(value: str, base_url: str) -> str | None:
    absolute = _normalized_https_url(value, base_url)
    if not absolute:
        return None
    parsed = urlsplit(absolute)
    host = (parsed.hostname or "").casefold()
    if host != "loveracing.nz" and not host.endswith(".loveracing.nz"):
        return None
    if _trusted_direct_image_url(absolute):
        return absolute
    if parsed.path.casefold() != "/common/image.ashx":
        return None
    nested_values = [
        item
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() in {"p", "path"}
    ]
    if len(nested_values) != 1:
        return None
    nested = _normalized_https_url(nested_values[0], LOVE_RACING_ORIGIN)
    return absolute if nested and _trusted_direct_image_url(nested) else None


def image_candidates(html: str, course_url: str, fallback: str = "") -> list[str]:
    parser = _CandidateParser()
    parser.feed(html)
    output: list[str] = []
    for raw in [*parser.values, fallback]:
        candidate = trusted_image_url(raw, course_url) if raw else None
        if candidate and candidate not in output:
            output.append(candidate)
    return output


def _candidate_score(url: str, fallback_url: str | None, width: int, height: int) -> tuple[int, int, int]:
    parsed = urlsplit(url)
    path = unquote(parsed.path).casefold()
    if path == "/common/image.ashx":
        path = next(
            (
                item.casefold()
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() in {"p", "path"}
            ),
            path,
        )
    known_fallback = 1 if fallback_url and url == fallback_url else 0
    course_specific = 2 if "2d" in path else 1
    return known_fallback, course_specific, width * height


def image_kind(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8"):
        return "image/jpeg", ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "", ""


def image_dimensions(content: bytes) -> tuple[int, int]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return struct.unpack(">II", content[16:24])
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        if len(content) >= 30 and content[12:16] == b"VP8X":
            return (
                1 + int.from_bytes(content[24:27], "little"),
                1 + int.from_bytes(content[27:30], "little"),
            )
        if len(content) >= 30 and content[12:16] == b"VP8 " and content[23:26] == b"\x9d\x01\x2a":
            return (
                int.from_bytes(content[26:28], "little") & 0x3FFF,
                int.from_bytes(content[28:30], "little") & 0x3FFF,
            )
        if len(content) >= 25 and content[12:16] == b"VP8L" and content[20] == 0x2F:
            bits = int.from_bytes(content[21:25], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if content[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            length = int.from_bytes(content[offset : offset + 2], "big")
            if length < 2 or offset + length > len(content):
                break
            if marker in {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }:
                return (
                    int.from_bytes(content[offset + 5 : offset + 7], "big"),
                    int.from_bytes(content[offset + 3 : offset + 5], "big"),
                )
            offset += length
    return 0, 0


def _directory() -> Path:
    directory = get_settings().data_dir / "track_maps"
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def _available_map(
    row: TrackMap,
    prefix: str,
    *,
    manual: bool,
) -> dict[str, object] | None:
    file_name = getattr(row, f"{prefix}_file_name")
    path = map_path(file_name or "")
    if not path:
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(MAP_HEADER_BYTES)
    except OSError:
        return None
    content_type, _extension = image_kind(header)
    width, height = image_dimensions(header)
    expected_type = getattr(row, f"{prefix}_content_type")
    expected_width = getattr(row, f"{prefix}_width")
    expected_height = getattr(row, f"{prefix}_height")
    expected_bytes = getattr(row, f"{prefix}_bytes")
    if (
        not content_type
        or width < 200
        or height < 200
        or expected_type != content_type
        or expected_width != width
        or expected_height != height
        or expected_bytes != size
    ):
        return None
    return {
        "file_name": file_name,
        "content_type": content_type,
        "width": width,
        "height": height,
        "bytes": size,
        "manual": manual,
    }


def _fully_valid_map(row: TrackMap, prefix: str) -> bool:
    available = _available_map(row, prefix, manual=prefix == "manual")
    path = map_path(str(available["file_name"])) if available else None
    if not path:
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    expected_hash = getattr(row, f"{prefix}_hash")
    return not expected_hash or hashlib.sha256(content).hexdigest() == expected_hash


def effective_map(row: TrackMap | None) -> dict[str, object] | None:
    if row is None:
        return None
    manual = bool(row.has_manual_override)
    return _available_map(row, "manual" if manual else "automatic", manual=manual)


def map_path(file_name: str) -> Path | None:
    directory = _directory()
    path = (directory / Path(file_name).name).resolve()
    return path if path.parent == directory and path.is_file() else None


def save_manual_map(db: Session, track: Track, content: bytes) -> TrackMap:
    if not content:
        raise ValueError("Choose an image to upload.")
    if len(content) > MAX_MANUAL_MAP_BYTES:
        raise ValueError("Track map images must be 15 MB or smaller.")
    content_type, extension = image_kind(content)
    width, height = image_dimensions(content)
    if not content_type or width < 200 or height < 200:
        raise ValueError("Upload a valid JPEG, PNG, or WebP image with usable dimensions.")
    row = db.get(TrackMap, track.id) or TrackMap(track_id=track.id)
    db.add(row)
    file_name = f"manual-{track.id.hex}{extension}"
    destination = _directory() / file_name
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, destination)
    row.manual_file_name = file_name
    row.manual_content_type = content_type
    row.manual_hash = hashlib.sha256(content).hexdigest()
    row.manual_width, row.manual_height = width, height
    row.manual_bytes = len(content)
    row.manual_updated_at = utcnow()
    row.has_manual_override = True
    return row


def reset_manual_map(db: Session, row: TrackMap) -> Path | None:
    previous = map_path(row.manual_file_name or "")
    row.manual_file_name = row.manual_content_type = row.manual_hash = None
    row.manual_width = row.manual_height = row.manual_bytes = None
    row.manual_updated_at = None
    row.has_manual_override = False
    return previous


def map_refresh_due(row: TrackMap | None, *, now=None) -> bool:  # type: ignore[no-untyped-def]
    now = now or utcnow()
    if row is None or row.automatic_checked_at is None:
        return True
    interval = (
        FAILED_MAP_REFRESH_INTERVAL
        if row.automatic_error or row.automatic_status == "ERROR"
        else SUCCESSFUL_MAP_REFRESH_INTERVAL
    )
    return now - row.automatic_checked_at >= interval


class _MapRefreshFailure(RuntimeError):
    pass


def _refresh_error(exc: Exception) -> str:
    if isinstance(exc, _MapRefreshFailure):
        return str(exc)[:500]
    if isinstance(exc, SourceHTTPError):
        return f"HTTP/source failure: {exc}"[:500]
    if isinstance(exc, PermissionError):
        return "File permission/storage failure: the Track-map cache is not writable."
    if isinstance(exc, OSError):
        return "File permission/storage failure: the Track-map cache could not be updated."
    return f"Map refresh failed safely ({type(exc).__name__})."


def _source_get(
    client: SourceHTTPClient,
    url: str,
    *,
    accept: str,
    destination_validator,
) -> object:  # type: ignore[no-untyped-def]
    """Keep narrow test adapters compatible while production enforces preflight redirects."""
    if isinstance(client, SourceHTTPClient):
        return client.get(
            url,
            accept=accept,
            destination_validator=destination_validator,
        )
    return client.get(url, accept=accept)


def refresh_automatic_map(db: Session, track: Track, client: SourceHTTPClient) -> TrackMap:
    row = db.get(TrackMap, track.id) or TrackMap(track_id=track.id)
    db.add(row)
    reference = canonical_track_name(track.map_reference or track.name)
    course = COURSES.get(reference)
    now = utcnow()
    if not course:
        row.automatic_status = "UNAVAILABLE"
        row.automatic_checked_at = now
        row.automatic_error = "No official Love Racing automatic map is configured for this Track."
        return row
    course_url = urljoin(LOVE_RACING_ORIGIN, course[0])
    fallback_url = trusted_image_url(course[1], course_url)
    try:
        if not fallback_url:
            raise _MapRefreshFailure("Invalid URL or image path: the configured official map path is unsafe.")
        try:
            page = _source_get(
                client,
                course_url,
                accept="text/html",
                destination_validator=lambda url: (
                    (parsed := urlsplit(url)).scheme.casefold() == "https"
                    and (parsed.hostname or "").casefold() in {"loveracing.nz", "www.loveracing.nz"}
                ),
            )
        except SourceHTTPError:
            candidates = [fallback_url]
        else:
            candidates = image_candidates(page.text, course_url, course[1])
        if not candidates:
            raise _MapRefreshFailure("Invalid URL or image path: no trusted official map URL was found.")
        images: list[tuple[tuple[int, int, int], int, bytes, str, str, str]] = []
        failures: list[_MapRefreshFailure] = []
        for candidate in candidates:
            try:
                response = _source_get(
                    client,
                    candidate,
                    accept="image/jpeg,image/png,image/webp",
                    destination_validator=lambda url: trusted_image_url(url, course_url) is not None,
                )
            except SourceHTTPError as exc:
                failures.append(_MapRefreshFailure(f"HTTP/source failure: {exc}"))
                continue
            final_url = trusted_image_url(response.url, course_url)
            if not final_url:
                failures.append(
                    _MapRefreshFailure(
                        "Invalid URL or image path: the source redirected outside the trusted map boundary."
                    )
                )
                continue
            content_type, extension = image_kind(response.body)
            width, height = image_dimensions(response.body)
            declared_type = response.content_type.split(";", 1)[0].strip().casefold()
            if not content_type or declared_type != content_type:
                failures.append(
                    _MapRefreshFailure(
                        "Invalid or unsupported image: the source type did not match JPEG, PNG or WebP content."
                    )
                )
                continue
            if width < 200 or height < 200:
                failures.append(
                    _MapRefreshFailure("Invalid or unsupported image: dimensions were smaller than 200×200.")
                )
                continue
            images.append(
                (
                    _candidate_score(final_url, fallback_url, width, height), len(response.body),
                    response.body, content_type, extension, final_url,
                )
            )
        if not images:
            raise failures[-1] if failures else _MapRefreshFailure(
                "Invalid or unsupported image: no usable official map image was returned."
            )
        _score, size, content, content_type, extension, source_url = max(images)
        width, height = image_dimensions(content)
        previous_score = (row.automatic_width or 0) * (row.automatic_height or 0)
        if _fully_valid_map(row, "automatic") and previous_score > width * height:
            row.automatic_checked_at = now
            row.automatic_status = "AVAILABLE"
            row.automatic_error = None
            return row
        file_name = f"auto-{track.id.hex}{extension}"
        destination = _directory() / file_name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        row.automatic_file_name = file_name
        row.automatic_content_type = content_type
        row.automatic_hash = hashlib.sha256(content).hexdigest()
        row.automatic_width, row.automatic_height = width, height
        row.automatic_bytes = size
        row.automatic_source_url = source_url
        row.automatic_status = "AVAILABLE"
        row.automatic_checked_at = now
        row.automatic_error = None
    except Exception as exc:
        row.automatic_checked_at = now
        try:
            automatic_available = _fully_valid_map(row, "automatic")
        except OSError:
            automatic_available = False
        row.automatic_status = "AVAILABLE" if automatic_available else "ERROR"
        row.automatic_error = _refresh_error(exc)
    return row


def due_tracks(db: Session) -> list[Track]:
    rows = {row.track_id: row for row in db.scalars(select(TrackMap))}
    return [
        track
        for track in db.scalars(select(Track).where(Track.lifecycle == "ACTIVE"))
        if canonical_track_name(track.map_reference or track.name) in COURSES
        and map_refresh_due(rows.get(track.id))
    ]


def map_status(row: TrackMap | None) -> dict[str, object]:
    """Return an operator-facing status without reading an entire cached image."""
    if row is None:
        return {
            "code": "UNCHECKED",
            "label": "Unchecked",
            "detail": "No automatic map check has been recorded.",
            "available": False,
        }
    available = effective_map(row)
    if row.has_manual_override:
        if available:
            return {
                "code": "MANUAL_OVERRIDE",
                "label": "Manual override",
                "detail": "The uploaded map is authoritative until deliberately reverted.",
                "available": True,
            }
        return {
            "code": "MISSING_MANUAL_FILE",
            "label": "Manual file unavailable",
            "detail": "The manual map metadata exists, but its file is missing or invalid.",
            "available": False,
        }
    if available:
        retained_after_failure = bool(row.automatic_error)
        return {
            "code": "SOURCE_FAILED_RETAINED" if retained_after_failure else "AVAILABLE",
            "label": "Available · last refresh failed" if retained_after_failure else "Available",
            "detail": (
                "The last valid cached map is retained. " + row.automatic_error
                if retained_after_failure
                else "A validated Love Racing map is cached."
            ),
            "available": True,
        }
    if row.automatic_file_name:
        return {
            "code": "MISSING_FILE",
            "label": "Cached file unavailable",
            "detail": (
                "Map metadata exists, but the cached file is missing or fails validation. "
                + (row.automatic_error or "")
            ).strip(),
            "available": False,
        }
    if row.automatic_status == "UNAVAILABLE":
        return {
            "code": "UNAVAILABLE",
            "label": "No automatic source",
            "detail": row.automatic_error or "No official automatic map is configured.",
            "available": False,
        }
    if row.automatic_status == "ERROR":
        return {
            "code": "SOURCE_FAILED",
            "label": "Source failed",
            "detail": row.automatic_error or "The official source did not return a valid map.",
            "available": False,
        }
    return {
        "code": "MISSING",
        "label": "No map cached",
        "detail": row.automatic_error or "The last check did not produce a usable map.",
        "available": False,
    }
