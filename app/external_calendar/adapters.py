from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.external_calendar.service import ProviderObservation


@dataclass(frozen=True)
class NormalizedProviderResult:
    observations: list[ProviderObservation]
    warnings: list[str] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)


class CalendarProviderAdapter(Protocol):
    """Small boundary usable by HTTP, file, paste, or a future scheduler."""

    provider: str

    def fetch(self, start: date, end: date) -> object: ...
    def normalize(self, payload: object, start: date, end: date) -> NormalizedProviderResult: ...
