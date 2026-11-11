"""DSSE envelopes signed with Ed25519 (spec 9.7.3).

Attestations and revocation states use different payload types, and the type is part of the
signed bytes (the DSSE pre-authentication encoding), so one can never be accepted as the other.
"""

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ATTESTATION_TYPE = "application/vnd.settleiq.release-attestation.v1+json"
REVOCATION_TYPE = "application/vnd.settleiq.revocation-state.v1+json"


class EnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Envelope:
    payload_type: str
    payload: bytes
    key_id: str
    signature: bytes


def pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE v1 pre-authentication encoding."""
    kind = payload_type.encode()
    return b"DSSEv1 %d %s %d %s" % (len(kind), kind, len(payload), payload)


def parse_envelope(data: bytes) -> Envelope:
    """One envelope with exactly one signature. The payload bytes are decoded, never rebuilt."""
    try:
        raw = json.loads(data)
        [signature] = raw["signatures"]
        if set(raw) != {"payloadType", "payload", "signatures"}:
            raise EnvelopeError("unexpected envelope fields")
        return Envelope(
            raw["payloadType"],
            base64.b64decode(raw["payload"], validate=True),
            signature["keyid"],
            base64.b64decode(signature["sig"], validate=True),
        )
    except EnvelopeError:
        raise
    except (ValueError, KeyError, TypeError):
        raise EnvelopeError("malformed envelope") from None


def verify_signature(envelope: Envelope, expected_type: str, public_key: bytes) -> None:
    if envelope.payload_type != expected_type:
        raise EnvelopeError("wrong payload type")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            envelope.signature, pae(envelope.payload_type, envelope.payload)
        )
    except (InvalidSignature, ValueError):
        raise EnvelopeError("bad signature") from None
