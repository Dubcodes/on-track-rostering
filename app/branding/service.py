from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import unicodedata
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.branding.models import SystemBranding
from app.core.config import get_settings
from app.core.time import utcnow

DEFAULT_PRODUCT_NAME = "On Track"
MAX_PRODUCT_NAME_LENGTH = 80
MAX_FAVICON_BYTES = 2 * 1024 * 1024
MIN_FAVICON_DIMENSION = 32
MAX_FAVICON_DIMENSION = 1024
FAVICON_URL = "/branding/favicon"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_FAVICON_PATH = Path(__file__).resolve().parents[1] / "static" / "ontrack-favicon.svg"


@dataclass(frozen=True)
class Branding:
    product_name: str

    @property
    def mark(self) -> str:
        words = re.findall(r"[^\W_]+", self.product_name, flags=re.UNICODE)
        if len(words) >= 2:
            return (words[0][0] + words[1][0]).upper()
        return (words[0][:2] if words else "R").upper()


DEFAULT_BRANDING = Branding(DEFAULT_PRODUCT_NAME)


@dataclass(frozen=True)
class FaviconAsset:
    content: bytes
    media_type: str
    width: int | None
    height: int | None
    is_custom: bool
    digest: str

    @property
    def sizes(self) -> str:
        return f"{self.width}x{self.height}" if self.width and self.height else "any"


@dataclass(frozen=True)
class StoredFavicon:
    width: int
    height: int
    byte_count: int
    digest: str


def custom_favicon_path() -> Path:
    return get_settings().data_dir / "branding" / "favicon.png"


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if not content.startswith(PNG_SIGNATURE):
        raise ValueError("Upload a PNG image; other file types are not supported.")
    offset = len(PNG_SIGNATURE)
    width = height = 0
    saw_header = saw_data = saw_end = False
    while offset < len(content):
        if offset + 12 > len(content):
            raise ValueError("Upload a valid, uncorrupted PNG image.")
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_type = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(content):
            raise ValueError("Upload a valid, uncorrupted PNG image.")
        chunk_data = content[data_start:data_end]
        expected_crc = struct.unpack(">I", content[data_end:crc_end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("Upload a valid, uncorrupted PNG image.")
        if not saw_header:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("Upload a valid, uncorrupted PNG image.")
            width, height, bit_depth, colour_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                not width
                or not height
                or bit_depth not in valid_depths.get(colour_type, set())
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise ValueError("Upload a valid, uncorrupted PNG image.")
            saw_header = True
        elif chunk_type == b"IHDR":
            raise ValueError("Upload a valid, uncorrupted PNG image.")
        elif chunk_type == b"IDAT":
            saw_data = True
        elif chunk_type == b"IEND":
            if length != 0 or not saw_data or crc_end != len(content):
                raise ValueError("Upload a valid, uncorrupted PNG image.")
            saw_end = True
        offset = crc_end
        if saw_end:
            break
    if not saw_header or not saw_data or not saw_end:
        raise ValueError("Upload a valid, uncorrupted PNG image.")
    return width, height


def validate_favicon_png(content: bytes) -> StoredFavicon:
    if not content:
        raise ValueError("Choose a PNG image to upload.")
    if len(content) > MAX_FAVICON_BYTES:
        raise ValueError("Application icon PNGs must be 2 MB or smaller.")
    width, height = _png_dimensions(content)
    if width != height:
        raise ValueError("Application icon PNGs must be square.")
    if width < MIN_FAVICON_DIMENSION:
        raise ValueError("Application icon PNGs must be at least 32×32 pixels.")
    if width > MAX_FAVICON_DIMENSION:
        raise ValueError("Application icon PNGs must be no larger than 1024×1024 pixels.")
    return StoredFavicon(
        width=width,
        height=height,
        byte_count=len(content),
        digest=hashlib.sha256(content).hexdigest(),
    )


def save_custom_favicon(content: bytes) -> StoredFavicon:
    metadata = validate_favicon_png(content)
    destination = custom_favicon_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".favicon-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return metadata


def reset_custom_favicon() -> bool:
    path = custom_favicon_path()
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def favicon_asset() -> FaviconAsset:
    custom_path = custom_favicon_path()
    try:
        custom = custom_path.read_bytes()
        metadata = validate_favicon_png(custom)
    except (OSError, ValueError):
        content = DEFAULT_FAVICON_PATH.read_bytes()
        return FaviconAsset(
            content=content,
            media_type="image/svg+xml",
            width=None,
            height=None,
            is_custom=False,
            digest=hashlib.sha256(content).hexdigest(),
        )
    return FaviconAsset(
        content=custom,
        media_type="image/png",
        width=metadata.width,
        height=metadata.height,
        is_custom=True,
        digest=metadata.digest,
    )


def validated_product_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Product name is required.")
    if len(cleaned) > MAX_PRODUCT_NAME_LENGTH:
        raise ValueError(f"Product name must be {MAX_PRODUCT_NAME_LENGTH} characters or fewer.")
    if any(unicodedata.category(character).startswith("C") for character in cleaned):
        raise ValueError("Product name must contain plain visible text only.")
    if "<" in cleaned or ">" in cleaned:
        raise ValueError("Product name must not contain markup.")
    return cleaned


def branding_for(db: Session) -> Branding:
    row = db.get(SystemBranding, 1)
    return Branding(row.product_name) if row else DEFAULT_BRANDING


def update_branding(db: Session, *, product_name: str, actor_user_id: uuid.UUID) -> SystemBranding:
    clean_name = validated_product_name(product_name)
    row = db.get(SystemBranding, 1)
    if row is None:
        row = SystemBranding(id=1)
        db.add(row)
    row.product_name = clean_name
    row.updated_at = utcnow()
    row.updated_by_user_id = actor_user_id
    return row
