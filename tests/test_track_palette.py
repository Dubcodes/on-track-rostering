from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.catalog.models import Region, Track
from app.catalog.service import allocate_palette_slot
from app.core.themes import THEME_VALUES


def test_allocation_region_reuse_stability_and_reactivation(db):  # type: ignore[no-untyped-def]
    north, central = Region(name="North"), Region(name="Central")
    db.add_all([north, central])
    db.flush()
    first = Track(name="A", region_id=north.id, palette_slot=allocate_palette_slot(db, north.id))
    db.add(first)
    db.flush()
    second = Track(name="B", region_id=north.id, palette_slot=allocate_palette_slot(db, north.id))
    db.add(second)
    db.flush()
    assert (first.palette_slot, second.palette_slot) == (1, 2)
    assert allocate_palette_slot(db, central.id, preferred=first.palette_slot) == 1
    assert allocate_palette_slot(db, north.id, preferred=first.palette_slot, exclude_track_id=first.id) == 1
    second.lifecycle = "ARCHIVED"
    db.flush()
    replacement = Track(name="C", region_id=north.id, palette_slot=allocate_palette_slot(db, north.id))
    db.add(replacement)
    db.flush()
    assert replacement.palette_slot == 2
    assert allocate_palette_slot(db, north.id, preferred=second.palette_slot, exclude_track_id=second.id) == 3


def test_palette_capacity_and_database_constraint(db):  # type: ignore[no-untyped-def]
    region = Region(name="Full Region")
    db.add(region)
    db.flush()
    db.add_all([Track(name=f"Track {slot}", region_id=region.id, palette_slot=slot) for slot in range(1, 21)])
    db.flush()
    with pytest.raises(ValueError, match="20 active tracks"):
        allocate_palette_slot(db, region.id)
    with db.begin_nested(), pytest.raises(IntegrityError):
        db.add(Track(name="Collision", region_id=region.id, palette_slot=1))
        db.flush()


def test_every_supported_theme_defines_twenty_track_and_semantic_tokens():
    css = (Path(__file__).parents[1] / "app/static/track-palette.css").read_text()
    for theme in THEME_VALUES:
        body = css.split(f'html[data-theme="{theme}"] {{', 1)[1].split("}", 1)[0]
        for slot in range(1, 21):
            assert f"--track-{slot:02d}:" in body
        for token in (
            "source-only",
            "source-love-racing",
            "source-harness",
            "source-api",
            "unconfirmed",
            "office",
            "training",
        ):
            assert f"--{token}:" in body
