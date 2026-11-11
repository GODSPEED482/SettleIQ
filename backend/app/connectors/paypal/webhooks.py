"""PayPal webhook signature verification (task 4.3, spec 10.4, POL-15, AC-PP-03).

Verifies incoming transmission metadata before trusting event content or deriving tenant
context. An invalid or missing signature is rejected before tenant derivation; no tenant
evidence or inbox row is written.

There is no test bypass: tests sign events with PayPal's algorithm against their own
certificate (tests/paypal_signing.py), so the production path is the one under test.
"""

import base64
import binascii
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.connectors.paypal.client import PayPalClient

logger = logging.getLogger("settleiq.connectors.paypal.webhooks")

REQUIRED_HEADERS = (
    "paypal-auth-algo",
    "paypal-transmission-id",
    "paypal-cert-url",
    "paypal-transmission-sig",
    "paypal-transmission-time",
)

PAYPAL_CERT_HOST_PATTERN = re.compile(
    r"^([a-zA-Z0-9-]+\.)*(paypal\.com|paypalobjects\.com)$", re.IGNORECASE
)

# In-memory certificate cache: cert_url -> certificate
_CERT_CACHE: dict[str, Any] = {}


def _get_cert(cert_url: str) -> Any:
    if cert_url in _CERT_CACHE:
        return _CERT_CACHE[cert_url]

    parsed = urlparse(cert_url)
    if parsed.scheme != "https" or not PAYPAL_CERT_HOST_PATTERN.match(parsed.netloc):
        raise ValueError(f"Invalid PayPal cert URL host: {parsed.netloc}")

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(cert_url)
        resp.raise_for_status()
        cert_data = resp.content

    cert = x509.load_pem_x509_certificate(cert_data)
    _CERT_CACHE[cert_url] = cert
    return cert


def signed_message(
    transmission_id: str, transmission_time: str, webhook_id: str, raw_body: bytes
) -> bytes:
    """The string PayPal signs: transmission ID, time, webhook ID and the body's CRC32."""
    crc = binascii.crc32(raw_body) & 0xFFFFFFFF
    return f"{transmission_id}|{transmission_time}|{webhook_id}|{crc}".encode()


def _verify_crypto(
    *,
    auth_algo: str,
    transmission_id: str,
    transmission_time: str,
    cert_url: str,
    transmission_sig: str,
    webhook_id: str,
    raw_body: bytes,
) -> bool:
    """Locally verify signature using PayPal transmission algorithm, cert and CRC32."""
    if auth_algo.upper() != "SHA256WITHRSA":
        logger.warning("Unsupported auth algo: %s", auth_algo)
        return False
    try:
        message = signed_message(transmission_id, transmission_time, webhook_id, raw_body)
        sig = base64.b64decode(transmission_sig, validate=True)
        public_key = _get_cert(cert_url).public_key()
        public_key.verify(sig, message, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception as exc:
        logger.debug("Local signature verification failed: %s", exc)
        return False


def verify_signature(
    headers: dict[str, str],
    raw_body: bytes,
    webhook_id: str,
    client: PayPalClient | None = None,
    json_payload: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Verify PayPal webhook signature against transmission headers and registered webhook ID.

    Returns (is_valid, reason).
    """
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    for req in REQUIRED_HEADERS:
        if req not in normalized_headers or not normalized_headers[req]:
            return False, f"Missing required header: {req}"

    auth_algo = normalized_headers["paypal-auth-algo"]
    transmission_id = normalized_headers["paypal-transmission-id"]
    cert_url = normalized_headers["paypal-cert-url"]
    transmission_sig = normalized_headers["paypal-transmission-sig"]
    transmission_time = normalized_headers["paypal-transmission-time"]

    # 1. If PayPal client is provided, try provider API verification
    if client is not None and json_payload is not None:
        try:
            ok = client.verify_webhook_signature(
                transmission_id=transmission_id,
                transmission_time=transmission_time,
                cert_url=cert_url,
                auth_algo=auth_algo,
                transmission_sig=transmission_sig,
                webhook_id=webhook_id,
                webhook_event=json_payload,
            )
            if ok:
                return True, "verified_by_paypal_api"
        except Exception as exc:
            logger.debug("PayPal API verification attempt failed: %s", exc)

    # 2. Local cryptographic verification
    ok = _verify_crypto(
        auth_algo=auth_algo,
        transmission_id=transmission_id,
        transmission_time=transmission_time,
        cert_url=cert_url,
        transmission_sig=transmission_sig,
        webhook_id=webhook_id,
        raw_body=raw_body,
    )
    if ok:
        return True, "verified_by_local_crypto"

    return False, "signature_mismatch"
