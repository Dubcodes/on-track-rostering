from typing import Protocol

from app.external_calendar.service import ProviderObservation


class CalendarProviderAdapter(Protocol):
    """Small boundary usable by HTTP, file, paste, or a future scheduler."""

    def fetch(self) -> object: ...
    def normalize(self, payload: object) -> list[ProviderObservation]: ...
