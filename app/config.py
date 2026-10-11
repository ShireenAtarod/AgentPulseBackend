from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+psycopg://agentpulse:agentpulse_dev_password@localhost:5432/agentpulse"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins_raw: str = Field(default="http://localhost:3000", validation_alias="CORS_ORIGINS")
    trusted_origins_raw: str = Field(
        default="http://localhost:3000,http://127.0.0.1:8000", validation_alias="TRUSTED_ORIGINS"
    )
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    session_ttl_seconds: int = 8 * 60 * 60
    login_rate_limit: int = 10
    ingest_rate_limit: int = 120
    rate_limit_window_seconds: int = 60
    metrics_cache_enabled: bool = False
    metrics_cache_ttl_seconds: int = 30
    experiments_enabled: bool = False
    experiment_max_records: int = 50_000
    initial_user_email: str | None = None
    initial_user_password: str | None = None
    initial_user_role: str = "admin"
    seed_random_seed: int = 20261011
    seed_session_count: int = 50_000
    simulator_rate_per_minute: int = 30
    simulator_scenario: str = "normal"

    @staticmethod
    def _split_csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return self._split_csv(self.cors_origins_raw)

    @property
    def trusted_origins(self) -> list[str]:
        return self._split_csv(self.trusted_origins_raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()

