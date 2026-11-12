"""The demo moves no real money: only PayPal Sandbox is accepted."""

import pytest

from shop import config


def test_live_paypal_is_refused(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1/x")
    for base in ("https://api-m.paypal.com", "https://api-m.sandbox.paypal.com.evil.example",
                 "http://api-m.sandbox.paypal.com"):
        monkeypatch.setenv("PAYPAL_API_BASE", base)
        with pytest.raises(config.ConfigError):
            config.load()


def test_sandbox_is_the_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1/x")
    monkeypatch.delenv("PAYPAL_API_BASE", raising=False)
    assert config.load().paypal_api_base == config.SANDBOX_API


def test_a_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(config.ConfigError):
        config.load()
