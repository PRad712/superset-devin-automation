from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DEVIN_API_KEY: str
    GITHUB_WEBHOOK_SECRET: str
    DEVIN_API_BASE_URL: str = "https://api.devin.ai/v1"
    DATABASE_PATH: str = "data/sessions.db"
    POLL_INTERVAL_SECONDS: int = 30
    REMEDIATION_LABEL: str = "devin-remediate"
    SESSION_TAG: str = "superset-remediation"
    MAX_CONCURRENT_SESSIONS: int = 5
    SUPERSET_REPO: str | None = None
    TRIGGER_TOKEN: str | None = None
    DISABLE_POLLER: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
