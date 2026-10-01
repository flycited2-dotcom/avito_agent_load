"""Feed-level safety checks shared by scheduled and Studio publication."""
from __future__ import annotations

import math
from pathlib import Path

from defusedxml import ElementTree as ET


def existing_ad_count(path: Path) -> int | None:
    """Return the count of a valid existing feed, or None when unavailable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    if root.tag != "Ads":
        return None
    return len(root.findall("Ad"))


def validate_count_drop(
    candidate_count: int,
    previous_count: int | None,
    max_drop_fraction: float,
) -> None:
    """Fail closed when a candidate unexpectedly loses too much inventory."""
    fraction = float(max_drop_fraction)
    if not 0 <= fraction <= 1:
        raise ValueError("feed.max_drop_fraction must be between 0 and 1")
    if not previous_count or fraction >= 1:
        return
    minimum = math.ceil(previous_count * (1 - fraction))
    if candidate_count < minimum:
        lost = previous_count - candidate_count
        raise ValueError(
            "Защитная проверка фида: "
            f"было {previous_count}, стало {candidate_count} объявлений "
            f"(падение {lost}, допустимо не более {fraction:.0%}). "
            "Предыдущий фид не заменён."
        )
