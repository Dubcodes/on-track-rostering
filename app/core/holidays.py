from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache

MATARIKI_DATES = {
    2022: (6, 24),
    2023: (7, 14),
    2024: (6, 28),
    2025: (6, 20),
    2026: (7, 10),
    2027: (6, 25),
    2028: (7, 14),
    2029: (7, 6),
    2030: (6, 21),
    2031: (7, 11),
    2032: (7, 2),
    2033: (6, 24),
    2034: (7, 7),
    2035: (6, 29),
    2036: (7, 18),
    2037: (7, 10),
    2038: (6, 25),
    2039: (7, 15),
    2040: (7, 6),
    2041: (7, 19),
    2042: (7, 11),
    2043: (7, 3),
    2044: (6, 24),
    2045: (7, 7),
    2046: (6, 29),
    2047: (7, 19),
    2048: (7, 3),
    2049: (6, 25),
    2050: (7, 15),
    2051: (6, 30),
    2052: (6, 21),
}


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month = (h + weekday_offset - 7 * m + 114) // 31
    return date(year, month, ((h + weekday_offset - 7 * m + 114) % 31) + 1)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _mondayised(value: date) -> date:
    return value + timedelta(days=2 if value.weekday() == 5 else 1 if value.weekday() == 6 else 0)


def _nearest_monday(value: date) -> date:
    return value + timedelta(days=-value.weekday() if value.weekday() <= 3 else 7 - value.weekday())


def _pair(first: date, second: date) -> tuple[date, date]:
    if first.weekday() == 5:
        return first + timedelta(days=2), second + timedelta(days=2)
    if first.weekday() == 6:
        return first + timedelta(days=1), second + timedelta(days=1)
    return first, second


@lru_cache(maxsize=256)
def holidays_for_year(year: int, region: str = "") -> dict[date, tuple[str, ...]]:
    rows: dict[date, list[str]] = {}

    def add(day: date, name: str) -> None:
        rows.setdefault(day, []).append(name)

    ny1, ny2 = date(year, 1, 1), date(year, 1, 2)
    add(ny1, "New Year's Day")
    add(ny2, "Day after New Year's Day")
    for observed, name, actual in zip(
        _pair(ny1, ny2), ("New Year's Day", "Day after New Year's Day"), (ny1, ny2), strict=True
    ):
        if observed != actual:
            add(observed, name + " (observed)")
    for actual, name in ((date(year, 2, 6), "Waitangi Day"), (date(year, 4, 25), "ANZAC Day")):
        add(actual, name)
        observed = _mondayised(actual)
        if observed != actual:
            add(observed, name + " (observed)")
    easter = _easter(year)
    add(easter - timedelta(days=2), "Good Friday")
    add(easter + timedelta(days=1), "Easter Monday")
    add(_nth_weekday(year, 6, calendar.MONDAY, 1), "King's Birthday")
    if year in MATARIKI_DATES:
        add(date(year, *MATARIKI_DATES[year]), "Matariki")
    add(_nth_weekday(year, 10, calendar.MONDAY, 4), "Labour Day")
    christmas, boxing = date(year, 12, 25), date(year, 12, 26)
    add(christmas, "Christmas Day")
    add(boxing, "Boxing Day")
    for observed, name, actual in zip(
        _pair(christmas, boxing), ("Christmas Day", "Boxing Day"), (christmas, boxing), strict=True
    ):
        if observed != actual:
            add(observed, name + " (observed)")
    region_key = region.strip().casefold().replace("_", "-").replace(" ", "-")
    if region_key in {"auckland", "northland", "waikato", "bay-of-plenty", "gisborne"}:
        add(_nearest_monday(date(year, 1, 29)), "Auckland Anniversary Day")
    elif region_key == "wellington":
        add(_nearest_monday(date(year, 1, 22)), "Wellington Anniversary Day")
    elif region_key in {"nelson", "tasman", "buller's", "buller"}:
        add(_nearest_monday(date(year, 2, 1)), "Nelson Anniversary Day")
    elif region_key == "taranaki":
        add(_nth_weekday(year, 3, calendar.MONDAY, 2), "Taranaki Anniversary Day")
    elif region_key in {"hawkes-bay", "hawke's-bay"}:
        add(
            _nth_weekday(year, 10, calendar.MONDAY, 4) - timedelta(days=3),
            "Hawke's Bay Anniversary Day",
        )
    elif region_key == "marlborough":
        add(_nth_weekday(year, 10, calendar.MONDAY, 4) + timedelta(days=7), "Marlborough Anniversary Day")
    elif region_key == "canterbury":
        second_tuesday = _nth_weekday(year, 11, calendar.TUESDAY, 2)
        add(second_tuesday + timedelta(days=3), "Canterbury Anniversary Day")
    elif region_key == "south-canterbury":
        add(_nth_weekday(year, 9, calendar.MONDAY, 4), "South Canterbury Anniversary Day")
    elif region_key == "westland":
        add(_nearest_monday(date(year, 12, 1)), "Westland Anniversary Day")
    elif region_key == "otago":
        add(_nearest_monday(date(year, 3, 23)), "Otago Anniversary Day")
    elif region_key == "southland":
        add(easter + timedelta(days=2), "Southland Anniversary Day")
    return {day: tuple(names) for day, names in rows.items()}


def holiday_for_date(value: date, region: str = "") -> str:
    return " / ".join(holidays_for_year(value.year, region).get(value, ()))
