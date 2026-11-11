from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str
    environment: str = "development"
    raw_evidence_dir: str = "/data/raw-evidence"
    # Where `managed:<NAME>` source secret references resolve (app/sources/secrets.py).
    source_secret_dir: str = ""

    app_base_url: str = "http://localhost:5173"
    session_ttl_hours: int = 24
    email_token_ttl_hours: int = 24
    invitation_ttl_hours: int = 168

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
