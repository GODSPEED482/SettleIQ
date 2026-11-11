"""Sign test webhook deliveries the way PayPal does (spec 10.4, AC-PP-03).

The route verifies with the production code path: SHA256withRSA over
`transmission_id|transmission_time|webhook_id|crc32(body)`, checked against the certificate at
`paypal-cert-url`. Tests hold their own key and place its certificate in the verifier's cache
under a PayPal URL, so nothing is fetched and no bypass exists in the code under test.
"""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.connectors.paypal import webhooks
from tests.support import grant_set

CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-SETTLEIQ-TEST"


def _key_and_cert() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "settleiq-test-paypal")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert


_KEY, _CERT = _key_and_cert()


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


@dataclass
class Signer:
    key: rsa.RSAPrivateKey = _KEY

    def headers(
        self,
        body: bytes,
        webhook_id: str,
        *,
        transmission_id: str | None = None,
        transmission_time: str = "2026-11-10T12:00:00Z",
        cert_url: str = CERT_URL,
    ) -> dict[str, str]:
        transmission_id = transmission_id or f"trans-{uuid4().hex}"
        message = webhooks.signed_message(transmission_id, transmission_time, webhook_id, body)
        signature = self.key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return {
            "content-type": "application/json",
            "paypal-auth-algo": "SHA256withRSA",
            "paypal-transmission-id": transmission_id,
            "paypal-transmission-time": transmission_time,
            "paypal-cert-url": cert_url,
            "paypal-transmission-sig": base64.b64encode(signature).decode(),
        }

    def deliver(self, client, source_id: UUID, payload: dict, webhook_id: str, **kwargs):
        """POST a correctly signed delivery of `payload` to the PayPal webhook route."""
        body = encode(payload)
        return client.post(
            f"/api/webhooks/paypal/{source_id}",
            content=body,
            headers=self.headers(body, webhook_id) | kwargs.pop("extra_headers", {}),
            **kwargs,
        )


EVENT_TYPES = [
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.REFUNDED",
]


def register_webhook(member, source_id: UUID, webhook_id: str) -> None:
    """Register through PUT /webhooks, the only path ingress trusts (AC-PP-10).

    The member is granted the integration set first: the owner set lacks the webhook key."""
    grant_set(member.tenant, member.principal.id, "integration_admin")
    response = member.request(
        "PUT",
        f"/api/sources/{source_id}/webhooks",
        json={"webhook_id": webhook_id, "event_types": EVENT_TYPES},
    )
    assert response.status_code == 200, response.text


def install(monkeypatch) -> Signer:
    """Trust the test certificate at CERT_URL for this test; the `paypal_signer` fixture."""
    monkeypatch.setitem(webhooks._CERT_CACHE, CERT_URL, _CERT)
    return Signer()


def foreign_signer() -> Signer:
    """A well-formed signer whose key does not match the PayPal certificate."""
    return Signer(key=_key_and_cert()[0])
