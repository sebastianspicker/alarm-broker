"""Application settings with grouped configuration.

This module provides configuration management using Pydantic Settings
with logical grouping for different concerns.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    """Core infrastructure settings.

    Attributes:
        database_url: PostgreSQL connection URL
        redis_url: Redis connection URL
        base_url: Public base URL for ACK links
        log_level: Logging level
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://alarm:change-me@localhost:5432/alarm"
    redis_url: str = "redis://localhost:6379/0"
    base_url: AnyHttpUrl = "http://localhost:8080"
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is a valid Python logging level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return upper


class SecuritySettings(BaseSettings):
    """Security-related settings.

    Attributes:
        admin_api_key: API key for admin endpoints (empty = fail closed)
        enable_api_docs: Whether to enable /docs and /redoc endpoints
        yelk_ip_allowlist: Comma-separated IPs/CIDRs for Yealink endpoints
        trusted_proxy_cidrs: Trusted proxy CIDRs for X-Forwarded-For
        rate_limit_per_minute: Rate limit per device token per minute
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    admin_api_key: str = ""  # Empty = fail closed
    enable_api_docs: bool = False
    yelk_ip_allowlist: str = ""  # Comma-separated IPs/CIDRs; empty disables
    trusted_proxy_cidrs: str = ""  # Comma-separated trusted proxy CIDRs
    rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)

    @field_validator("admin_api_key")
    @classmethod
    def warn_empty_admin_key(cls, v: str) -> str:
        """Warn if admin key is empty (but allow it for development)."""
        if not v:
            import warnings

            warnings.warn(
                "ADMIN_API_KEY is not set. Admin endpoints will return 500 errors.",
                UserWarning,
                stacklevel=2,
            )
        return v


class YealinkSettings(BaseSettings):
    """Yealink-specific settings.

    Attributes:
        yelk_token_query_param: Query parameter name for device token
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    yelk_token_query_param: str = "token"


class ZammadSettings(BaseSettings):
    """Zammad integration settings.

    Attributes:
        zammad_base_url: Zammad instance URL
        zammad_api_token: API token for authentication
        zammad_group: Default ticket group
        zammad_priority_id_p0: Priority ID for P0 alarms
        zammad_state_id_new: State ID for new tickets
        zammad_customer: Default customer identifier
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    zammad_base_url: AnyHttpUrl = "https://zammad.example.org"
    zammad_api_token: str = ""  # Empty = disabled
    zammad_group: str = "Notfallstelle"
    zammad_priority_id_p0: int = 3
    zammad_state_id_new: int = 1
    zammad_customer: str = "guess:alarm-system@example.org"

    def is_enabled(self) -> bool:
        """Check if Zammad integration is configured."""
        return bool(self.zammad_api_token)


class SmsSettings(BaseSettings):
    """SMS provider settings (SendXMS or compatible).

    Attributes:
        sendxms_enabled: Whether SMS sending is enabled
        sendxms_base_url: SMS provider API URL
        sendxms_api_key: API key for authentication
        sendxms_from: Sender name/number
        sendxms_send_path: API endpoint path
        sendxms_mode: API mode (currently only 'json' supported)
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    sendxms_enabled: bool = False
    sendxms_base_url: AnyHttpUrl = "https://api.sendxms.tld"
    sendxms_api_key: str = ""  # Empty = disabled
    sendxms_from: str = "Notfall"
    sendxms_send_path: str = "/send"
    sendxms_mode: Literal["json"] = "json"

    def is_enabled(self) -> bool:
        """Check if SMS integration is configured."""
        return self.sendxms_enabled and bool(self.sendxms_api_key)


class SignalSettings(BaseSettings):
    """Signal messaging settings.

    Attributes:
        signal_enabled: Whether Signal sending is enabled
        signal_cli_endpoint: signal-cli-rest-api endpoint URL
        signal_target_group_id: Default target group ID
        signal_send_path: API endpoint path
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    signal_enabled: bool = False
    signal_cli_endpoint: AnyHttpUrl = "http://signal-cli:8080"
    signal_target_group_id: str = ""
    signal_send_path: str = "/v2/send"

    def is_enabled(self) -> bool:
        """Check if Signal integration is configured."""
        return self.signal_enabled and bool(self.signal_target_group_id)


class WebhookSettings(BaseSettings):
    """Webhook callback settings."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    webhook_enabled: bool = False
    webhook_url: AnyHttpUrl | None = None
    webhook_secret: str = ""
    webhook_timeout_seconds: int = Field(default=5, ge=1, le=60)
    webhook_max_retries: int = Field(default=3, ge=1, le=10)
    webhook_retry_delay_seconds: int = Field(default=30, ge=1, le=300)

    def is_enabled(self) -> bool:
        return self.webhook_enabled and self.webhook_url is not None


class EscalationSettings(BaseSettings):
    """Escalation timing settings.

    Attributes:
        escalate_t1: First escalation delay in seconds
        escalate_t2: Second escalation delay in seconds
        escalate_t3: Third escalation delay in seconds
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    escalate_t1: int = Field(default=60, ge=0)
    escalate_t2: int = Field(default=180, ge=0)
    escalate_t3: int = Field(default=300, ge=0)


class SimulationSettings(BaseSettings):
    """Simulation/Demo mode settings.

    When enabled, the system uses mock connectors instead of real external
    services, allowing full demonstration without external dependencies.

    Attributes:
        simulation_enabled: Enable simulation mode with mock connectors
        simulation_seed_url: URL to fetch demo seed data (optional, uses bundled data if empty)
    """

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    simulation_enabled: bool = False
    simulation_seed_url: str = ""

    def is_enabled(self) -> bool:
        """Check if simulation mode is enabled."""
        return self.simulation_enabled


class Settings(BaseSettings):
    """Main application settings combining all configuration groups.

    This class composes all setting groups into a single configuration object.
    Each group can be accessed directly as an attribute.

    Example:
        settings = get_settings()
        if settings.zammad.is_enabled():
            # Use Zammad integration
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Compose all setting groups
    # Note: Pydantic will still load from env vars directly for backward compatibility
    # These are kept as direct attributes for backward compatibility

    # Core settings (direct attributes for backward compatibility)
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

    # Convenience properties for grouped access
    @property
    def core(self) -> CoreSettings:
        """Get core settings as a group."""
        return CoreSettings(
            database_url=self.database_url,
            redis_url=self.redis_url,
            base_url=self.base_url,
            log_level=self.log_level,
        )

    @property
    def security(self) -> SecuritySettings:
        """Get security settings as a group."""
        return SecuritySettings(
            admin_api_key=self.admin_api_key,
            enable_api_docs=self.enable_api_docs,
            yelk_ip_allowlist=self.yelk_ip_allowlist,
            trusted_proxy_cidrs=self.trusted_proxy_cidrs,
            rate_limit_per_minute=self.rate_limit_per_minute,
        )

    @property
    def yealink(self) -> YealinkSettings:
        """Get Yealink settings as a group."""
        return YealinkSettings(yelk_token_query_param=self.yelk_token_query_param)

    @property
    def zammad(self) -> ZammadSettings:
        """Get Zammad settings as a group."""
        return ZammadSettings(
            zammad_base_url=self.zammad_base_url,
            zammad_api_token=self.zammad_api_token,
            zammad_group=self.zammad_group,
            zammad_priority_id_p0=self.zammad_priority_id_p0,
            zammad_state_id_new=self.zammad_state_id_new,
            zammad_customer=self.zammad_customer,
        )

    @property
    def sms(self) -> SmsSettings:
        """Get SMS settings as a group."""
        return SmsSettings(
            sendxms_enabled=self.sendxms_enabled,
            sendxms_base_url=self.sendxms_base_url,
            sendxms_api_key=self.sendxms_api_key,
            sendxms_from=self.sendxms_from,
            sendxms_send_path=self.sendxms_send_path,
            sendxms_mode=self.sendxms_mode,
        )

    @property
    def signal(self) -> SignalSettings:
        """Get Signal settings as a group."""
        return SignalSettings(
            signal_enabled=self.signal_enabled,
            signal_cli_endpoint=self.signal_cli_endpoint,
            signal_target_group_id=self.signal_target_group_id,
            signal_send_path=self.signal_send_path,
        )

    @property
    def webhook(self) -> WebhookSettings:
        """Get webhook settings as a group."""
        return WebhookSettings(
            webhook_enabled=self.webhook_enabled,
            webhook_url=self.webhook_url,
            webhook_secret=self.webhook_secret,
            webhook_timeout_seconds=self.webhook_timeout_seconds,
            webhook_max_retries=self.webhook_max_retries,
            webhook_retry_delay_seconds=self.webhook_retry_delay_seconds,
        )

    @property
    def escalation(self) -> EscalationSettings:
        """Get escalation settings as a group."""
        return EscalationSettings(
            escalate_t1=self.escalate_t1,
            escalate_t2=self.escalate_t2,
            escalate_t3=self.escalate_t3,
        )

    @property
    def simulation(self) -> SimulationSettings:
        """Get simulation settings as a group."""
        return SimulationSettings(
            simulation_enabled=self.simulation_enabled,
            simulation_seed_url=self.simulation_seed_url,
        )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings instance loaded from environment variables
    """
    return Settings()
