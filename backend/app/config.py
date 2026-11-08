from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    # TODO(G1 1.7 Rahul): add the raw-evidence storage location.
    # TODO(G1 1.9 Swapnaneel): add SMTP relay and auth settings; values stay out of the repo.
    database_url: str
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
