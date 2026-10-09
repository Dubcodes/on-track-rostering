from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _image_build_id() -> str:
    marker = Path("/app/.ontrack-build-id")
    try:
        return marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ONTRACK_", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://ontrack:ontrack@localhost:5432/ontrack",
        validation_alias=AliasChoices("DATABASE_URL", "ONTRACK_DATABASE_URL"),
    )
    secret_key: str = Field(default="development-only-change-this-secret-key", min_length=32)
    credential_pepper: str = ""
    cookie_secure: bool = False
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("localhost", "127.0.0.1", "testserver")
    trusted_proxy_cidrs: Annotated[tuple[str, ...], NoDecode] = ("127.0.0.1/32",)
    trusted_device_days_standard: int = Field(default=90, ge=1, le=365)
    trusted_device_days_elevated: int = Field(default=14, ge=1, le=90)
    trusted_device_limit: int = Field(default=10, ge=1, le=100)
    fresh_auth_minutes: int = Field(default=15, ge=1, le=120)
    webauthn_rp_id: str = "localhost"
    webauthn_origin: str = "http://localhost:8000"
    webauthn_challenge_minutes: int = Field(default=5, ge=1, le=15)
    mfa_required_admin: bool = False
    mfa_required_manager: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:operations@example.invalid"
    notification_max_attempts: int = Field(default=5, ge=1, le=20)
    timezone_name: str = "Pacific/Auckland"
    fortnight_anchor: str = "2026-08-31"
    lunch_allowance_hours: float = Field(default=12.0, ge=0)
    app_version: str = "0.3.0"
    build_id: str = Field(default_factory=_image_build_id)
    retention_days: int = Field(default=365, ge=1, le=3650)
    racing_source_timeout_seconds: float = Field(default=15.0, ge=2.0, le=60.0)
    racing_source_max_bytes: int = Field(default=2_000_000, ge=100_000, le=10_000_000)
    racing_source_lookback_days: int = Field(default=14, ge=0, le=90)
    racing_source_horizon_days: int = Field(default=366, ge=30, le=730)
    racing_sources_enabled_default: bool = True
    data_dir: Path = Path("data")
    scheduler_interval_seconds: int = Field(default=300, ge=60, le=86400)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: object,
        env_settings: object,
        dotenv_settings: object,
        file_secret_settings: object,
    ) -> tuple[object, ...]:
        def without_runtime_build_id(source: object) -> dict[str, object]:
            values = source()  # type: ignore[operator]
            return {key: value for key, value in values.items() if key != "build_id"}

        return (
            init_settings,
            lambda: without_runtime_build_id(env_settings),
            lambda: without_runtime_build_id(dotenv_settings),
            file_secret_settings,
        )

    @field_validator("allowed_hosts", "trusted_proxy_cidrs", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@lru_cache
def get_settings() -> Settings:
    return Settings()
