from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (and an optional `.env` file)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./clinic.db"

    # Meta WhatsApp Business Platform
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v23.0"
    # Local testing only: accept unsigned webhook POSTs when no app secret is configured.
    whatsapp_allow_unsigned: bool = False

    # Clinic behaviour
    clinic_timezone: str = "Asia/Kolkata"
    default_country_code: str = "91"
    slot_interval_minutes: int = Field(default=30, ge=5, le=240)
    booking_window_days: int = Field(default=60, ge=1, le=365)
    session_timeout_minutes: int = Field(default=30, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
