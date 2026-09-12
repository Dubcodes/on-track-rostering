from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def optional_uuid(value: str, label: str) -> uuid.UUID | None:
    if not value.strip():
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, f"Select a valid {label}.") from exc


@contextmanager
def controlled_integrity(db: Session, detail: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, detail) from exc
