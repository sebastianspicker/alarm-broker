"""Application settings.

All configuration is loaded from environment variables (and optional .env file).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Main application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    database_url: str = "postgresql+asyncpg://alarm:change-me@localhost:5432/alarm"
    redis_url: str = "redis://localhost:6379/0"
    base_url: AnyHttpUrl = "http://localhost:8080"
    log_level: str = "INFO"

    # Yealink inbound
    yelk_token_query_param: str = "token"
    yelk_ip_allowlist: str = ""

    # Rate limiting
    rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)

    # Admin
    admin_api_key: str = ""
    enable_api_docs: bool = False

    # Proxy handling
    trusted_proxy_cidrs: str = ""

    # Zammad
    zammad_base_url: AnyHttpUrl = "https://zammad.example.org"
    zammad_api_token: str = ""
    zammad_group: str = "Notfallstelle"
    zammad_priority_id_p0: int = 3
    zammad_state_id_new: int = 1
    zammad_customer: str = "guess:alarm-system@example.org"

    # SMS (SendXMS)
    sendxms_enabled: bool = False
    sendxms_base_url: AnyHttpUrl = "https://api.sendxms.tld"
    sendxms_api_key: str = ""
    sendxms_from: str = "Notfall"
    sendxms_send_path: str = "/send"
    sendxms_mode: Literal["json"] = "json"

    # Signal
    signal_enabled: bool = False
    signal_cli_endpoint: AnyHttpUrl = "http://signal-cli:8080"
    signal_target_group_id: str = ""
    signal_send_path: str = "/v2/send"

    # Webhook callbacks
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: int = Field(default=5, ge=1, le=60)
    webhook_max_retries: int = Field(default=3, ge=1, le=10)
    webhook_retry_delay_seconds: int = Field(default=30, ge=1, le=300)

    # Escalation timings
    escalate_t1: int = Field(default=60, ge=0)
    escalate_t2: int = Field(default=180, ge=0)
    escalate_t3: int = Field(default=300, ge=0)

    # Simulation mode
    simulation_enabled: bool = False
    simulation_seed_url: str = ""

    # --- Validators ---

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return upper

    @field_validator("admin_api_key")
    @classmethod
    def warn_empty_admin_key(cls, v: str) -> str:
        if not v:
            import warnings

            warnings.warn(
                "ADMIN_API_KEY is not set. Admin endpoints will return 500 errors.",
                UserWarning,
                stacklevel=2,
            )
        return v

    # --- Convenience checks ---

    def is_zammad_enabled(self) -> bool:
        return bool(self.zammad_api_token)

    def is_sms_enabled(self) -> bool:
        return self.sendxms_enabled and bool(self.sendxms_api_key)

    def is_signal_enabled(self) -> bool:
        return self.signal_enabled and bool(self.signal_target_group_id)

    def is_webhook_enabled(self) -> bool:
        return self.webhook_enabled and bool(self.webhook_url)

    def is_simulation_enabled(self) -> bool:
        return self.simulation_enabled


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
