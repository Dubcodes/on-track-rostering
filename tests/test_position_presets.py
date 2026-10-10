from __future__ import annotations

import pytest

from app.catalog.models import BasePosition, PositionPresetItem, Region
from app.catalog.presets import (
    effective_position_presets,
    reset_position_preset,
    save_position_preset,
)
from app.core.enums import Lifecycle
from app.identity.models import User


def test_position_preset_fallback_override_reset_and_inactive_filter(db) -> None:  # type: ignore[no-untyped-def]
    region = Region(name="Preset Region")
    side_two = BasePosition(name="Side 2", display_order=2)
    side_one = BasePosition(name="Side 1", display_order=1)
    inactive = BasePosition(name="Start", lifecycle=Lifecycle.ARCHIVED.value)
    custom = BasePosition(name="Custom", display_order=0)
    actor = User(
        email="preset-admin@example.test",
        display_name="Preset Admin",
        credential_hash="test",
        credential_kind="pin",
    )
    db.add_all([region, side_two, side_one, inactive, custom, actor])
    db.flush()

    fallback = effective_position_presets(db, [region.id])[str(region.id)]
    assert fallback["THOROUGHBRED"] == [str(side_one.id), str(side_two.id)]
    assert str(inactive.id) not in fallback["THOROUGHBRED"]
    with pytest.raises(ValueError, match="active base positions"):
        save_position_preset(
            db,
            region_id=region.id,
            preset_key="HARNESS",
            position_ids=[inactive.id],
            actor_user_id=actor.id,
        )

    row = save_position_preset(
        db,
        region_id=region.id,
        preset_key="THOROUGHBRED",
        position_ids=[custom.id],
        actor_user_id=actor.id,
    )
    db.flush()
    assert effective_position_presets(db, [region.id])[str(region.id)]["THOROUGHBRED"] == [
        str(custom.id)
    ]
    assert [item.base_position_id for item in db.query(PositionPresetItem).all()] == [custom.id]

    reset_position_preset(db, region_id=region.id, preset_key="THOROUGHBRED")
    db.flush()
    assert effective_position_presets(db, [region.id])[str(region.id)]["THOROUGHBRED"] == [
        str(side_one.id), str(side_two.id)
    ]
    assert db.get(type(row), row.id) is None
