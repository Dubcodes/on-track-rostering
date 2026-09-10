from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.branding.models import SystemBranding
from app.core.time import utcnow

DEFAULT_PRODUCT_NAME = "On Track"
MAX_PRODUCT_NAME_LENGTH = 80


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
