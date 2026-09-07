from __future__ import annotations

import math

from app.catalog.models import Track


def colour_distance(first: str, second: str) -> float:
    """Weighted RGB distance suitable for a non-blocking same-region warning."""
    left = tuple(int(first[index : index + 2], 16) for index in (1, 3, 5))
    right = tuple(int(second[index : index + 2], 16) for index in (1, 3, 5))
    red_mean = (left[0] + right[0]) / 2
    red, green, blue = (left[index] - right[index] for index in range(3))
    return math.sqrt(
        (2 + red_mean / 256) * red * red + 4 * green * green + (2 + (255 - red_mean) / 256) * blue * blue
    )


def close_colour_warnings(tracks: list[Track], threshold: float = 85) -> list[str]:
    warnings: list[str] = []
    active = [track for track in tracks if track.lifecycle == "ACTIVE"]
    for index, first in enumerate(active):
        for second in active[index + 1 :]:
            if (
                first.region_id == second.region_id
                and colour_distance(first.display_colour, second.display_colour) < threshold
            ):
                warnings.append(
                    f"{first.name} and {second.name} use colours that may be difficult to distinguish."
                )
    return warnings
