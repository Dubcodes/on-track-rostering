from __future__ import annotations

import hashlib
import os
import re
import struct
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Track, TrackMap
from app.core.config import get_settings
from app.core.time import utcnow
from app.external_calendar.http import SourceHTTPClient

LOVE_RACING_ORIGIN = "https://loveracing.nz"
MAX_MANUAL_MAP_BYTES = 15 * 1024 * 1024
COURSES: dict[str, tuple[str, str]] = {
    "rotorua": ("/RaceInfo/Clubs-And-Courses/1/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Arawa%20Park%20copy.jpg"),
    "avondale": ("/RaceInfo/Clubs-And-Courses/4/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Avondale%202.jpg"),
    "cambridge": ("/RaceInfo/Clubs-And-Courses/7/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg"),
    "ellerslie": ("/RaceInfo/Clubs-And-Courses/9/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png"),
    "matamata": ("/RaceInfo/Clubs-And-Courses/18/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Matamata%20copy.jpg"),
    "pukekohe": ("/RaceInfo/Clubs-And-Courses/26/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Pukekohe%20copy.jpg"),
    "ruakaka": ("/RaceInfo/Clubs-And-Courses/32/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Ruakaka%20copy.jpg"),
    "taupo": ("/RaceInfo/Clubs-And-Courses/34/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Taupo%20copy.jpg"),
    "tauranga": ("/RaceInfo/Clubs-And-Courses/41/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Tauranga-2D_new.jpg"),
    "te aroha": ("/RaceInfo/Clubs-And-Courses/35/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/Te-Aroha_new.jpg"),
    "te rapa": ("/RaceInfo/Clubs-And-Courses/38/Racecourse.aspx", "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te%20Rapa%20copy.jpg"),
}


def canonical_track_name(value: str) -> str:
    clean = " ".join(value.strip().split())
    clean = re.sub(r"^[TH]-", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^TRIALS?\s*[-–—:]?\s*", "", clean, flags=re.IGNORECASE)
    aliases = {
        "arawa park": "rotorua",
        "cambridge synthetic": "cambridge",
        "pukekohe park": "pukekohe",
    }
    return aliases.get(clean.casefold(), clean.casefold())


class _CandidateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta" and values.get("property", "").lower() == "og:image":
            self.values.append(values.get("content", ""))
        if tag != "img":
            return
        joined = " ".join(values.values()).lower()
        if "racecourses/tracks" not in joined and not ("onhorsefiles" in joined and "track" in joined):
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


def trusted_image_url(value: str, base_url: str) -> str | None:
    absolute = urljoin(base_url, unescape(value.strip()))
    parsed = urlsplit(absolute)
    host = (parsed.hostname or "").casefold()
    if host != "loveracing.nz" and not host.endswith(".loveracing.nz"):
        return None
    if not re.search(r"\.(?:jpe?g|png|webp)$", unquote(parsed.path), re.IGNORECASE):
        return None
    return absolute


def image_candidates(html: str, course_url: str, fallback: str = "") -> list[str]:
    parser = _CandidateParser()
    parser.feed(html)
    output: list[str] = []
    for raw in [*parser.values, fallback]:
        candidate = trusted_image_url(raw, course_url) if raw else None
        if candidate and candidate not in output:
            output.append(candidate)
    return output


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


def effective_map(row: TrackMap | None) -> dict[str, object] | None:
    if row is None:
        return None
    manual = bool(row.has_manual_override and row.manual_file_name)
    prefix = "manual" if manual else "automatic"
    file_name = getattr(row, f"{prefix}_file_name")
    if not file_name:
        return None
    return {
        "file_name": file_name,
        "content_type": getattr(row, f"{prefix}_content_type"),
        "width": getattr(row, f"{prefix}_width"),
        "height": getattr(row, f"{prefix}_height"),
        "bytes": getattr(row, f"{prefix}_bytes"),
        "manual": manual,
    }


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
    return (
        row is None
        or row.automatic_checked_at is None
        or now - row.automatic_checked_at >= timedelta(days=28)
    )


def refresh_automatic_map(db: Session, track: Track, client: SourceHTTPClient) -> TrackMap:
    row = db.get(TrackMap, track.id) or TrackMap(track_id=track.id)
    db.add(row)
    course = COURSES.get(canonical_track_name(track.name))
    now = utcnow()
    if not course:
        row.automatic_status = "UNAVAILABLE"
        row.automatic_checked_at = now
        row.automatic_error = "No official Love Racing automatic map is configured for this Track."
        return row
    course_url = urljoin(LOVE_RACING_ORIGIN, course[0])
    try:
        page = client.get(course_url, accept="text/html")
        candidates = image_candidates(page.text, course_url, course[1])
        images: list[tuple[int, int, int, bytes, str, str, str]] = []
        for candidate in candidates:
            response = client.get(candidate, accept="image/jpeg,image/png,image/webp")
            final_url = trusted_image_url(response.url, course_url)
            content_type, extension = image_kind(response.body)
            width, height = image_dimensions(response.body)
            if final_url and content_type and width >= 200 and height >= 200:
                images.append(
                    (
                        width * height, min(width, height), len(response.body),
                        response.body, content_type, extension, final_url,
                    )
                )
        if not images:
            raise ValueError("No valid official map image was returned.")
        _area, _short, size, content, content_type, extension, source_url = max(images)
        width, height = image_dimensions(content)
        previous_score = (row.automatic_width or 0) * (row.automatic_height or 0)
        if row.automatic_file_name and previous_score > width * height:
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
        row.automatic_status = "AVAILABLE" if row.automatic_file_name else "ERROR"
        row.automatic_error = f"{type(exc).__name__}: {str(exc)[:400]}"
    return row


def due_tracks(db: Session) -> list[Track]:
    rows = {row.track_id: row for row in db.scalars(select(TrackMap))}
    return [
        track
        for track in db.scalars(select(Track).where(Track.lifecycle == "ACTIVE"))
        if canonical_track_name(track.name) in COURSES and map_refresh_due(rows.get(track.id))
    ]
