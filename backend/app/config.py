from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    # TODO(G1 1.7 Rahul): add the raw-evidence storage location.
    database_url: str
    environment: str = "development"

    app_base_url: str = "http://localhost:5173"
    session_ttl_hours: int = 24
    email_token_ttl_hours: int = 24

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    agent_url: str = "http://agent:8100"
    agent_job_concurrency: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
