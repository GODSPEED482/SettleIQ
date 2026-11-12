"""Kitebase's business logic, including its deliberate faults.

Two checkout faults reproduce the discrepancies SettleIQ's Financial Operations pack resolves:

- LOST_CONFIRMATION: PayPal captures the payment and Kitebase records the capture ID, then
  "crashes" before marking the payment COMPLETED. PayPal says COMPLETED, Kitebase says PENDING:
  SettleIQ raises STATUS_MISMATCH and PB-001 corrects the payment row.
- DOUBLE_CHARGE: the capture succeeds but the buyer is told it timed out. Retrying checks out
  the same order again, a bug with no "already paid" guard, so PayPal holds two captures with
  one invoice ID: SettleIQ raises DUPLICATE_CAPTURE and PB-002 refunds the later one.

Each queued fault fires on the next checkout and is then used up. Reset reseeds every source
under a new generation of identifiers, plants the SaaS pack's two discrepancies and queues both
faults again. SettleIQ keeps what it already read, so nothing is ever reused.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from shop.config import Settings
from shop.paypal import PayPal, PayPalError

LOST_CONFIRMATION = "LOST_CONFIRMATION"
DOUBLE_CHARGE = "DOUBLE_CHARGE"
FAULTS = (LOST_CONFIRMATION, DOUBLE_CHARGE)

PLANS = [
    # plan_id, name, kind, interval_months, price
    ("starter-monthly", "Starter, monthly", "SUBSCRIPTION", 1, Decimal("9.00")),
    ("pro-monthly", "Pro, monthly", "SUBSCRIPTION", 1, Decimal("29.00")),
    ("team-annual", "Team, annual", "SUBSCRIPTION", 12, Decimal("290.00")),
    ("onboarding", "Onboarding session (one-off)", "ADDON", None, Decimal("49.00")),
]
CURRENCY = "USD"


class ShopError(Exception):
    """A refusal shown to the buyer, with the HTTP status it maps to."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class ConfirmationLost(ShopError):
    def __init__(self, order_ref: str) -> None:
        super().__init__(500, "CONFIRMATION_LOST",
                         "We could not confirm your payment. If PayPal charged you, it will be "
                         "sorted out: no need to pay again.")
        self.order_ref = order_ref


class CaptureTimedOut(ShopError):
    def __init__(self, order_ref: str) -> None:
        super().__init__(504, "TIMEOUT", "The payment timed out. Please try again.")
        self.order_ref = order_ref


def _months(moment: datetime, months: int) -> datetime:
    month = moment.month - 1 + months
    return moment.replace(year=moment.year + month // 12, month=month % 12 + 1)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _meta(conn: psycopg.Connection, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    return conn.execute(f"SELECT * FROM demo_meta WHERE id = 1{suffix}").fetchone()


def _new_id(conn: psycopg.Connection, prefix: str, generation: int) -> str:
    number = conn.execute("SELECT nextval('demo_ids') AS n").fetchone()["n"]
    return f"{prefix}-{generation}-{number}"


def ensure_catalogue(conn: psycopg.Connection) -> None:
    for plan_id, name, kind, interval, price in PLANS:
        conn.execute(
            """
            INSERT INTO plans (plan_id, name, kind, interval_months, price, currency)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (plan_id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                interval_months = EXCLUDED.interval_months, price = EXCLUDED.price
            """,
            (plan_id, name, kind, interval, price, CURRENCY),
        )


def catalogue(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM plans ORDER BY kind DESC, price").fetchall()


# Checkout


def _account(conn: psycopg.Connection, generation: int, email: str, name: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 200:
        raise ShopError(422, "BAD_EMAIL", "Enter an email address.")
    row = conn.execute("SELECT account_id FROM accounts WHERE email = %s", (email,)).fetchone()
    if row:
        return row["account_id"]
    account_id = _new_id(conn, "ACC", generation)
    conn.execute("INSERT INTO accounts (account_id, email, name) VALUES (%s, %s, %s)",
                 (account_id, email, (name.strip() or email.split("@")[0])[:100]))
    return account_id


def _new_order(conn: psycopg.Connection, settings: Settings, plan_id: str, email: str,
               name: str) -> dict[str, Any]:
    generation = _meta(conn)["generation"]
    plan = conn.execute("SELECT * FROM plans WHERE plan_id = %s", (plan_id,)).fetchone()
    if plan is None:
        raise ShopError(422, "UNKNOWN_PLAN", "Choose a plan.")
    account_id = _account(conn, generation, email, name)
    order_ref = _new_id(conn, "ORD", generation)
    payment_id = _new_id(conn, "PAY", generation)
    conn.execute(
        "INSERT INTO orders (order_ref, account_id, plan_id, total, currency, status) "
        "VALUES (%s, %s, %s, %s, %s, 'AWAITING_PAYMENT')",
        (order_ref, account_id, plan_id, plan["price"], plan["currency"]),
    )
    conn.execute(
        "INSERT INTO payments (payment_id, merchant_id, order_ref, amount, currency, status) "
        "VALUES (%s, %s, %s, %s, %s, 'PENDING')",
        (payment_id, settings.merchant_id, order_ref, plan["price"], plan["currency"]),
    )
    subscription_id = None
    period_end = None
    if plan["kind"] == "SUBSCRIPTION":
        subscription_id = _new_id(conn, "SUB", generation)
        period_end = _months(datetime.now(UTC).replace(microsecond=0), plan["interval_months"])
        conn.execute(
            "INSERT INTO subscriptions (subscription_id, account_id, plan_id, status, "
            "current_period_end) VALUES (%s, %s, %s, 'pending', NULL)",
            (subscription_id, account_id, plan_id),
        )
    conn.execute(
        "INSERT INTO invoices (invoice_id, account_id, subscription_id, order_ref, period_end, "
        "status, amount, currency) VALUES (%s, %s, %s, %s, %s, 'open', %s, %s)",
        (_new_id(conn, "INV", generation), account_id, subscription_id, order_ref, period_end,
         plan["price"], plan["currency"]),
    )
    return {"order_ref": order_ref, "payment_id": payment_id, "amount": plan["price"],
            "currency": plan["currency"], "description": f"Kitebase {plan['name']}"}


def start_checkout(conn: psycopg.Connection, settings: Settings, paypal: PayPal, *,
                   plan_id: str | None = None, email: str = "", name: str = "",
                   order_ref: str | None = None) -> dict[str, str]:
    """A PayPal order for a new shop order, or another one for an existing order.

    The second form is the retry after a timeout. It checks nothing about the order's payment:
    that missing guard is the DOUBLE_CHARGE bug.
    """
    if order_ref:
        row = conn.execute(
            "SELECT p.payment_id, p.amount, p.currency, pl.name FROM payments p "
            "JOIN orders o USING (order_ref) JOIN plans pl USING (plan_id) "
            "WHERE p.order_ref = %s", (order_ref,),
        ).fetchone()
        if row is None:
            raise ShopError(404, "UNKNOWN_ORDER", "That order no longer exists.")
        order = {"order_ref": order_ref, "payment_id": row["payment_id"],
                 "amount": row["amount"], "currency": row["currency"],
                 "description": f"Kitebase {row['name']}"}
    else:
        order = _new_order(conn, settings, plan_id or "", email, name)
    try:
        paypal_order_id = paypal.create_order(order["order_ref"], order["payment_id"],
                                              order["amount"], order["currency"],
                                              order["description"])
    except PayPalError as error:
        raise _paypal_refusal(error) from error
    conn.execute("INSERT INTO checkout_attempts (paypal_order_id, order_ref) VALUES (%s, %s)",
                 (paypal_order_id, order["order_ref"]))
    conn.execute("UPDATE payments SET paypal_order_id = COALESCE(paypal_order_id, %s) "
                 "WHERE payment_id = %s", (paypal_order_id, order["payment_id"]))
    return {"order_ref": order["order_ref"], "paypal_order_id": paypal_order_id}


def _paypal_refusal(error: PayPalError) -> ShopError:
    if error.issue == "DUPLICATE_INVOICE_ID":
        return ShopError(409, "DUPLICATE_INVOICE_ID",
                         "PayPal blocked a second payment for this invoice. Allow duplicate "
                         "invoice IDs in the Sandbox account's payment receiving preferences "
                         "to run the double-charge scenario.")
    return ShopError(502, "PAYPAL_ERROR", str(error))


def _take_fault(conn: psycopg.Connection) -> str | None:
    queue = _meta(conn, lock=True)["fault_queue"]
    if not queue:
        return None
    conn.execute("UPDATE demo_meta SET fault_queue = %s WHERE id = 1", (queue[1:],))
    return queue[0]


def capture(conn: psycopg.Connection, paypal: PayPal, order_ref: str,
            paypal_order_id: str) -> dict[str, str]:
    attempt = conn.execute(
        "SELECT * FROM checkout_attempts WHERE paypal_order_id = %s AND order_ref = %s",
        (paypal_order_id, order_ref),
    ).fetchone()
    if attempt is None:
        raise ShopError(404, "UNKNOWN_ORDER", "That checkout does not belong to this order.")
    try:
        result = paypal.capture_order(paypal_order_id)
    except PayPalError as error:
        raise _paypal_refusal(error) from error
    if result["status"] != "COMPLETED":
        raise ShopError(402, "NOT_CAPTURED", f"PayPal did not complete the payment "
                                             f"({result['status']}).")
    conn.execute("UPDATE checkout_attempts SET capture_id = %s WHERE paypal_order_id = %s",
                 (result["capture_id"], paypal_order_id))
    payment = conn.execute("SELECT * FROM payments WHERE order_ref = %s FOR UPDATE",
                           (order_ref,)).fetchone()
    if payment["status"] == "COMPLETED":
        # The retried checkout of an already paid order: the shop notices nothing.
        return {"order_ref": order_ref, "status": "PAID"}

    fault = _take_fault(conn)
    # The capture ID is recorded first, in its own step, as a real integration would.
    conn.execute("UPDATE payments SET paypal_capture_id = %s, updated_at = now() "
                 "WHERE payment_id = %s", (result["capture_id"], payment["payment_id"]))
    if fault == LOST_CONFIRMATION:
        conn.commit()  # what was written so far survives the "crash" below
        raise ConfirmationLost(order_ref)
    conn.execute("UPDATE payments SET status = 'COMPLETED', updated_at = now() "
                 "WHERE payment_id = %s", (payment["payment_id"],))
    provision(conn)
    if fault == DOUBLE_CHARGE:
        conn.commit()
        raise CaptureTimedOut(order_ref)
    return {"order_ref": order_ref, "status": "PAID"}


def provision(conn: psycopg.Connection) -> int:
    """Fulfils every order whose payment reads COMPLETED and is not fulfilled yet. It runs after
    each checkout and on each page, so a payment SettleIQ corrected is fulfilled too."""
    rows = conn.execute(
        """
        SELECT o.order_ref, o.plan_id, pl.kind, pl.interval_months
        FROM orders o JOIN payments p USING (order_ref) JOIN plans pl USING (plan_id)
        WHERE o.status = 'AWAITING_PAYMENT' AND p.status = 'COMPLETED'
        FOR UPDATE OF o
        """
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE orders SET status = 'PAID' WHERE order_ref = %s",
                     (row["order_ref"],))
        invoice = conn.execute(
            "UPDATE invoices SET status = 'paid' WHERE order_ref = %s "
            "RETURNING subscription_id, period_end", (row["order_ref"],),
        ).fetchone()
        if invoice and invoice["subscription_id"]:
            conn.execute(
                "UPDATE subscriptions SET status = 'active', current_period_end = %s "
                "WHERE subscription_id = %s",
                (invoice["period_end"], invoice["subscription_id"]),
            )
    return len(rows)


# Reset


def reset(conn: psycopg.Connection, settings: Settings, *, force: bool = False) -> int:
    """Back to the discrepant starting state, under a new generation of identifiers."""
    meta = _meta(conn, lock=True)
    if meta is not None and meta["last_reset_at"] and not force:
        elapsed = (datetime.now(UTC) - meta["last_reset_at"]).total_seconds()
        if elapsed < settings.reset_cooldown_seconds:
            raise ShopError(429, "RESET_COOLDOWN",
                            f"Reset again in {int(settings.reset_cooldown_seconds - elapsed) + 1}"
                            " seconds.")
    generation = (meta["generation"] if meta else 0) + 1
    for table in ("checkout_attempts", "invoices", "subscriptions", "payments", "orders",
                  "accounts"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute(
        "INSERT INTO demo_meta (id, generation, fault_queue, last_reset_at) "
        "VALUES (1, %s, %s, now()) ON CONFLICT (id) DO UPDATE SET "
        "generation = EXCLUDED.generation, fault_queue = EXCLUDED.fault_queue, "
        "last_reset_at = EXCLUDED.last_reset_at",
        (generation, list(FAULTS)),
    )
    _seed(conn, generation)
    return generation


def _seed(conn: psycopg.Connection, generation: int) -> None:
    """Three billing customers: one consistent, and the SaaS pack's two discrepancies. They pay
    by invoice outside PayPal, so they have no payment rows."""
    month = _month_start(datetime.now(UTC))
    customers = [
        # name, email, plan, status, current period end, [(invoice period end, status)]
        ("Ada Byron", "ada", "pro-monthly", "active", _months(month, 1),
         [(month, "paid"), (_months(month, 1), "paid")]),
        # Renewed for next month, but next month's invoice is still open.
        ("Grace Hopper", "grace", "pro-monthly", "active", _months(month, 1),
         [(month, "paid"), (_months(month, 1), "open")]),
        # Paid for the next year, but the subscription was never extended.
        ("Linus Pauling", "linus", "team-annual", "past_due", month,
         [(month, "paid"), (_months(month, 12), "paid")]),
    ]
    prices = {p[0]: p[4] for p in PLANS}
    for name, handle, plan_id, status, period_end, invoices in customers:
        account_id = _new_id(conn, "ACC", generation)
        conn.execute("INSERT INTO accounts (account_id, email, name) VALUES (%s, %s, %s)",
                     (account_id, f"{handle}.g{generation}@kitebase.example", name))
        subscription_id = _new_id(conn, "SUB", generation)
        conn.execute(
            "INSERT INTO subscriptions (subscription_id, account_id, plan_id, status, "
            "current_period_end) VALUES (%s, %s, %s, %s, %s)",
            (subscription_id, account_id, plan_id, status, period_end),
        )
        for invoice_end, invoice_status in invoices:
            conn.execute(
                "INSERT INTO invoices (invoice_id, account_id, subscription_id, order_ref, "
                "period_end, status, amount, currency) VALUES (%s, %s, %s, NULL, %s, %s, %s, %s)",
                (_new_id(conn, "INV", generation), account_id, subscription_id, invoice_end,
                 invoice_status, prices[plan_id], CURRENCY),
            )


def set_faults(conn: psycopg.Connection, faults: list[str]) -> None:
    unknown = set(faults) - set(FAULTS)
    if unknown:
        raise ShopError(422, "UNKNOWN_FAULT", f"Unknown fault: {sorted(unknown)[0]}")
    conn.execute("UPDATE demo_meta SET fault_queue = %s WHERE id = 1", (faults,))


def status(conn: psycopg.Connection) -> dict[str, Any]:
    return _meta(conn)


# Source view


@dataclass
class _Cached:
    value: Any
    until: float


class PayPalReader:
    """Reads each PayPal order back at most every few seconds, so the auto-refreshing source
    view does not hammer the Sandbox."""

    def __init__(self, paypal: PayPal, ttl: float = 8.0) -> None:
        self._paypal = paypal
        self._ttl = ttl
        self._cache: dict[str, _Cached] = {}

    def order(self, paypal_order_id: str) -> dict[str, Any]:
        cached = self._cache.get(paypal_order_id)
        if cached and cached.until > time.monotonic():
            return cached.value
        try:
            body = self._paypal.get_order(paypal_order_id)
            value = _paypal_view(body)
        except PayPalError as error:
            value = {"error": str(error), "captures": [], "refunds": []}
        self._cache[paypal_order_id] = _Cached(value, time.monotonic() + self._ttl)
        return value


def _paypal_view(body: dict[str, Any]) -> dict[str, Any]:
    unit = (body.get("purchase_units") or [{}])[0]
    payments = unit.get("payments") or {}
    return {
        "status": body.get("status"),
        "invoice_id": unit.get("invoice_id"),
        "captures": [{"id": c["id"], "status": c["status"],
                      "amount": c["amount"]["value"], "currency": c["amount"]["currency_code"],
                      "time": c.get("create_time")} for c in payments.get("captures", [])],
        "refunds": [{"id": r["id"], "status": r["status"], "amount": r["amount"]["value"]}
                    for r in payments.get("refunds", [])],
    }


def orders_view(conn: psycopg.Connection, reader: PayPalReader) -> list[dict[str, Any]]:
    orders = conn.execute(
        """
        SELECT o.order_ref, o.status AS order_status, o.total, o.currency, o.created_at,
            a.email, pl.name AS plan, p.payment_id, p.status AS payment_status,
            p.paypal_capture_id, i.status AS invoice_status, s.status AS subscription_status,
            s.current_period_end
        FROM orders o JOIN accounts a USING (account_id) JOIN plans pl USING (plan_id)
        JOIN payments p USING (order_ref)
        LEFT JOIN invoices i ON i.order_ref = o.order_ref
        LEFT JOIN subscriptions s ON s.subscription_id = i.subscription_id
        ORDER BY o.created_at DESC
        """
    ).fetchall()
    for order in orders:
        attempts = conn.execute(
            "SELECT paypal_order_id FROM checkout_attempts WHERE order_ref = %s "
            "ORDER BY created_at", (order["order_ref"],),
        ).fetchall()
        order["paypal"] = [{"paypal_order_id": a["paypal_order_id"],
                            **reader.order(a["paypal_order_id"])} for a in attempts]
        captures = [c for p in order["paypal"] for c in p["captures"]]
        completed = [c for c in captures if c["status"] == "COMPLETED"]
        refunded = [c for c in captures if c["status"] in ("REFUNDED", "PARTIALLY_REFUNDED")]
        flags = []
        if order["payment_status"] == "PENDING" and completed:
            flags.append("PayPal captured this payment; Kitebase still says PENDING.")
        if len(completed) > 1:
            flags.append(f"PayPal charged this order {len(completed)} times.")
        order["flags"] = flags
        order["notes"] = [f"Capture {c['id']} refunded." for c in refunded]
    return orders


def subscriptions_view(conn: psycopg.Connection) -> list[dict[str, Any]]:
    subscriptions = conn.execute(
        """
        SELECT s.*, a.name AS account_name, pl.name AS plan
        FROM subscriptions s JOIN accounts a USING (account_id) JOIN plans pl USING (plan_id)
        WHERE s.status <> 'pending'
        ORDER BY s.subscription_id
        """
    ).fetchall()
    for subscription in subscriptions:
        invoices = conn.execute(
            "SELECT invoice_id, period_end, status, amount FROM invoices "
            "WHERE subscription_id = %s ORDER BY period_end", (subscription["subscription_id"],),
        ).fetchall()
        subscription["invoices"] = invoices
        end = subscription["current_period_end"]
        paid = [i["period_end"] for i in invoices if i["status"] == "paid"]
        flags = []
        if subscription["status"] == "active" and end not in paid:
            flags.append("Active for a period with no paid invoice.")
        if end is not None and any(p is not None and p > end for p in paid):
            flags.append("Paid for a period the subscription does not cover.")
        subscription["flags"] = flags
    return subscriptions


def ago(moment: datetime | None) -> str:
    if moment is None:
        return ""
    seconds = int((datetime.now(UTC) - moment).total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60} minutes ago"
    return moment.strftime("%d %b %H:%M UTC")

