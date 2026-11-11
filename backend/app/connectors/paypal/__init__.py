"""PayPal connector package (tasks 4.1, 4.2, 4.3, 4.10, spec 10)."""

from app.connectors.paypal.client import PayPalClient
from app.connectors.paypal.refunds import create_refund, read_refund
from app.connectors.paypal.sync import sync_paypal
from app.connectors.paypal.webhooks import verify_signature

__all__ = [
    "PayPalClient",
    "create_refund",
    "read_refund",
    "sync_paypal",
    "verify_signature",
]

