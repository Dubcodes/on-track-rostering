from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import Region, Track


def allocate_palette_slot(
    db: Session,
    region_id: uuid.UUID,
    *,
    preferred: int | None = None,
    exclude_track_id: uuid.UUID | None = None,
) -> int:
    """Serialize active slot allocation per region; callers retain the transaction."""
    with db.no_autoflush:
        region = db.scalar(select(Region).where(Region.id == region_id).with_for_update())
        if not region or region.lifecycle != "ACTIVE":
            raise ValueError("Select an active destination region.")
        query = select(Track.palette_slot).where(Track.region_id == region_id, Track.lifecycle == "ACTIVE")
        if exclude_track_id is not None:
            query = query.where(Track.id != exclude_track_id)
        occupied = set(db.scalars(query))
        occupied.update(
            row.palette_slot
            for row in db.new
            if isinstance(row, Track) and row.region_id == region_id and row.lifecycle != "ARCHIVED"
        )
    if preferred in range(1, 21) and preferred not in occupied:
        return preferred
    for slot in range(1, 21):
        if slot not in occupied:
            return slot
    raise ValueError("This region already has 20 active tracks. Archive a track before adding another.")
