"""Registered implementations of state-changing capabilities and their readbacks (spec 9.7.1).

The capability manifest is generated from this table, never from manifests alone: a capability
that a connector or pack declares but nothing implements cannot appear in it, and so can never
be certified. Startup fails if a declared action capability is in neither table.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Implementation:
    connector_id: str
    capability: str
    # `module:attribute`, where the attribute may be dotted (a method on a client class).
    entrypoint: str


IMPLEMENTATIONS = (
    Implementation("paypal", "refunds.create", "app.connectors.paypal.refunds:create_refund"),
    Implementation("paypal", "refunds.read", "app.connectors.paypal.refunds:read_refund"),
    Implementation("paypal", "captures.read",
                   "app.connectors.paypal.client:PayPalClient.captures_read"),
    Implementation("internal-merchant", "merchant.payment_status.correct",
                   "app.connectors.internal_merchant.actions:correct_payment_status"),
    Implementation("internal-merchant", "merchant.payment.read",
                   "app.connectors.internal_merchant.actions:read_payment"),
)

# Declared but not built, as (connector_id, capability). Never in the manifest, so never
# certifiable or LIVE.
PENDING_IMPLEMENTATIONS: frozenset[tuple[str, str]] = frozenset()
