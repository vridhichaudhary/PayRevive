from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.decision.policy import RecoveryRules


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYREVIVE_", extra="ignore")

    execution_mode: Literal["simulation"] = "simulation"
    recovery_rules: RecoveryRules = Field(default_factory=RecoveryRules)
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://payrevive:payrevive_local@localhost:5432/payrevive"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    result_backend_url: SecretStr = SecretStr("redis://localhost:6379/1")
    dependency_timeout_seconds: int = Field(default=2, ge=1, le=10)

    @field_validator("database_url")
    @classmethod
    def require_postgres(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL with psycopg is required")
        return value

    @field_validator("redis_url", "result_backend_url")
    @classmethod
    def require_redis(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("A Redis URL is required")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
