"""Runtime settings, read once from the environment. Secrets are never logged."""

import os
from dataclasses import dataclass

SANDBOX_API = "https://api-m.sandbox.paypal.com"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    merchant_id: str
    paypal_client_id: str
    paypal_client_secret: str
    paypal_api_base: str
    reader_password: str
    writer_password: str
    settleiq_url: str
    reset_cooldown_seconds: int


def load() -> Settings:
    base = os.environ.get("PAYPAL_API_BASE", SANDBOX_API).rstrip("/")
    # The demo moves no real money: anything but PayPal Sandbox refuses to start.
    if base != SANDBOX_API:
        raise ConfigError("PAYPAL_API_BASE must be PayPal Sandbox.")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise ConfigError("DATABASE_URL is not set.")
    return Settings(
        database_url=database_url,
        merchant_id=os.environ.get("MERCHANT_ID", "kitebase"),
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", ""),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", ""),
        paypal_api_base=base,
        reader_password=os.environ.get("MERCHANT_READER_PASSWORD", ""),
        writer_password=os.environ.get("MERCHANT_WRITER_PASSWORD", ""),
        settleiq_url=os.environ.get("SETTLEIQ_URL", "https://app.settleiq.dpdns.org"),
        reset_cooldown_seconds=int(os.environ.get("RESET_COOLDOWN_SECONDS", "20")),
    )
