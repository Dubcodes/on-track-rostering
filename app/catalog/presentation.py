"""Theme-independent presentation tokens; business snapshots do not own colours."""


def track_token(slot: int | None, category: str = "RACE_DAY") -> str:
    if category in {"OFFICE_DAY", "TRAINING_DAY"}:
        return category.removesuffix("_DAY").lower()
    return f"track-{slot:02d}" if slot is not None and 1 <= slot <= 20 else "unconfirmed"
