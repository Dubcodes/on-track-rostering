from app.core.config import get_settings
from app.external_calendar.http import SourceHTTPClient
from app.external_calendar.providers.hrnz import HRNZAdapter
from app.external_calendar.providers.love_racing import LoveRacingAdapter


def provider_adapters():
    settings = get_settings()
    client = SourceHTTPClient(
        timeout=settings.racing_source_timeout_seconds,
        max_bytes=settings.racing_source_max_bytes,
    )
    return {
        "LOVE_RACING": LoveRacingAdapter(client),
        "HRNZ": HRNZAdapter(client),
    }


__all__ = ["HRNZAdapter", "LoveRacingAdapter", "provider_adapters"]
