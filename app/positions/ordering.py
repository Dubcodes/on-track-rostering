"""Operational display order, independent of position and assignment identity."""

from __future__ import annotations

import re
import unicodedata


def position_order(name: str) -> tuple[int, int, int, str]:
    """Normalize aliases only for sorting; never alter stored display names."""
    key = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name).casefold())
    number = re.search(r"(\d+)$", key)
    index = int(number.group()) if number else 1
    stem = key[: number.start()] if number else key
    fixed = {
        "side": 0,
        "headon": 1,
        "back": 2,
        "turn": 3,
        "rts": 4,
        "gimble": 5,
        "gimbal": 5,
        "gimbleassist": 5,
        "gimbalassist": 5,
        "steady": 6,
        "steadicam": 6,
        "steadyassist": 6,
        "steadicamassist": 6,
        "director": 7,
        "vt": 8,
        "sound": 9,
        "soundvt": 10,
        "ccu": 11,
        "eng": 12,
        "engineer": 12,
    }
    rank = fixed.get(stem, 100)
    # Keep assistants adjacent to their parent rather than among custom roles.
    assist = int(stem.endswith("assist")) if rank in {5, 6} else 0
    return rank, index if rank != 100 else 0, assist, key
