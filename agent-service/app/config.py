from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-only runtime configuration for the Agent service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    agent_database_url: str = Field(default="", validation_alias="AGENT_DATABASE_URL", min_length=1)
    app_timezone: Literal["Asia/Shanghai"] = Field(
        default="Asia/Shanghai", validation_alias="APP_TIMEZONE"
    )
    deepseek_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="", validation_alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="", validation_alias="DEEPSEEK_MODEL")
    agent_model_provider: Literal["fixture", "deepseek"] = Field(
        default="fixture", validation_alias="AGENT_MODEL_PROVIDER"
    )
    model_timeout_seconds: float = Field(
        default=45.0, validation_alias="MODEL_TIMEOUT_SECONDS", gt=0, le=120
    )
    model_max_retries: int = Field(default=2, validation_alias="MODEL_MAX_RETRIES", ge=0, le=3)
    business_service_url: str = Field(
        default="http://business-service:8080", validation_alias="BUSINESS_SERVICE_URL"
    )
    agent_checkpoint_redis_url: str = Field(
        default="redis://redis:6379/1", validation_alias="AGENT_CHECKPOINT_REDIS_URL", min_length=1
    )
    agent_checkpoint_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        validation_alias="AGENT_CHECKPOINT_TTL_SECONDS",
        ge=60,
        le=24 * 60 * 60,
    )
    internal_service_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="INTERNAL_SERVICE_TOKEN"
    )
    agent_context_jwt_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="AGENT_CONTEXT_JWT_SECRET"
    )
    agent_context_audience: str = Field(
        default="agent-service", validation_alias="AGENT_CONTEXT_AUDIENCE", min_length=1
    )
    qdrant_url: str = Field(default="http://qdrant:6333", validation_alias="QDRANT_URL")
    qdrant_collection: str = Field(
        default="meeting_policies", validation_alias="QDRANT_COLLECTION", min_length=1
    )
    fixture_now: str = Field(default="2026-08-11T10:00:00+08:00", validation_alias="FIXTURE_NOW")
    agent_max_model_calls: int = Field(
        default=8, validation_alias="AGENT_MAX_MODEL_CALLS", ge=1, le=8
    )
    agent_max_tool_calls: int = Field(
        default=12, validation_alias="AGENT_MAX_TOOL_CALLS", ge=1, le=12
    )
    agent_max_graph_nodes: int = Field(
        default=20, validation_alias="AGENT_MAX_GRAPH_NODES", ge=1, le=20
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @property
    def deepseek_is_configured(self) -> bool:
        return bool(
            self.deepseek_api_key.get_secret_value().strip()
            and self.deepseek_base_url.strip()
            and self.deepseek_model.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
