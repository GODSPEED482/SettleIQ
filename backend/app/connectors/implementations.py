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
)

# Declared but not built. Never in the manifest, so never certifiable or LIVE.
# TODO(G5 5.5 Rahul): register the internal-merchant adapter above and remove these entries.
PENDING_IMPLEMENTATIONS = frozenset({
    ("internal-merchant", "merchant.payment_status.correct"),
    ("internal-merchant", "merchant.payment.read"),
})
