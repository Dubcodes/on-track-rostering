from __future__ import annotations

import re

_FORMULA_PREFIX = re.compile(r"^[\s\x00-\x1f\x7f]*[=+\-@]")


def spreadsheet_safe_cell(value: object) -> object:
    """Neutralise text that spreadsheet programs may interpret as a formula."""
    if isinstance(value, str) and _FORMULA_PREFIX.match(value):
        return "'" + value
    return value
