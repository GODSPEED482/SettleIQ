"""Checkout, normally and with each fault (task 6.12). PayPal is the in-memory fake."""

import psycopg
from conftest import WRITER_PASSWORD, as_role

from shop import store


def checkout(client, plan_id="pro-monthly", email="buyer@example.com"):
    response = client.post("/api/checkout", json={"plan_id": plan_id, "email": email})
    assert response.status_code == 200, response.text
    return response.json()


def capture(client, started):
    return client.post("/api/checkout/capture", json=started)


def payment(sql, order_ref):
    return sql("SELECT * FROM payments WHERE order_ref = %s", (order_ref,))[0]


def subscription(sql, order_ref):
    return sql("SELECT s.* FROM subscriptions s JOIN invoices i USING (subscription_id) "
               "WHERE i.order_ref = %s", (order_ref,))[0]


def clear_faults(client):
    client.post("/demo/faults", data={"queue": ""})


def test_a_checkout_without_a_fault_pays_and_provisions(client, sql, paypal):
    clear_faults(client)
    started = checkout(client)
    response = capture(client, started)

    assert response.status_code == 200
    assert response.json() == {"order_ref": started["order_ref"], "status": "PAID"}
    row = payment(sql, started["order_ref"])
    assert row["status"] == "COMPLETED"
    assert row["paypal_capture_id"] == paypal.captures_for(started["order_ref"])[0]
    assert subscription(sql, started["order_ref"])["status"] == "active"
    invoice = sql("SELECT * FROM invoices WHERE order_ref = %s", (started["order_ref"],))[0]
    assert invoice["status"] == "paid"
    assert subscription(sql, started["order_ref"])["current_period_end"] == invoice["period_end"]


def test_the_paypal_order_carries_the_order_reference_as_invoice_id(client, sql, paypal):
    started = checkout(client)
    order = paypal.orders[started["paypal_order_id"]]
    assert order["invoice_id"] == started["order_ref"]
    assert order["custom_id"] == payment(sql, started["order_ref"])["payment_id"]
    assert order["amount"] == "29.00"


def test_a_lost_confirmation_leaves_the_payment_pending_with_its_capture(client, sql, paypal):
    client.post("/demo/faults", data={"queue": "LOST_CONFIRMATION"})
    started = checkout(client)
    response = capture(client, started)

    assert response.status_code == 500
    assert response.json()["error"] == "CONFIRMATION_LOST"
    assert response.json()["order_ref"] == started["order_ref"]
    row = payment(sql, started["order_ref"])
    assert row["status"] == "PENDING"
    # Recorded before the "crash": SettleIQ matches PayPal's capture to this row through it.
    assert row["paypal_capture_id"] == paypal.captures_for(started["order_ref"])[0]
    assert subscription(sql, started["order_ref"])["status"] == "pending"
    assert sql("SELECT fault_queue FROM demo_meta")[0]["fault_queue"] == []

    page = client.get("/sources").text
    assert "PayPal captured this payment; Kitebase still says PENDING." in page


def test_a_correction_through_the_writer_role_activates_the_subscription(
        client, sql, settings):
    client.post("/demo/faults", data={"queue": "LOST_CONFIRMATION"})
    started = checkout(client)
    capture(client, started)
    payment_id = payment(sql, started["order_ref"])["payment_id"]

    # What SettleIQ's PB-001 does: the conditional update, as settleiq_merchant_writer.
    writer = as_role(settings.database_url, "settleiq_merchant_writer", WRITER_PASSWORD)
    with psycopg.connect(writer) as conn:
        updated = conn.execute(
            "UPDATE settleiq_payments SET status = 'COMPLETED' "
            "WHERE payment_id = %s AND merchant_id = %s AND status = 'PENDING'",
            (payment_id, settings.merchant_id),
        ).rowcount
    assert updated == 1

    page = client.get(f"/orders/{started['order_ref']}").text
    assert "Paid." in page
    assert subscription(sql, started["order_ref"])["status"] == "active"
    assert "Kitebase still says PENDING" not in client.get("/sources").text


def test_a_timed_out_capture_charges_the_order_twice_on_retry(client, sql, paypal):
    client.post("/demo/faults", data={"queue": "DOUBLE_CHARGE"})
    started = checkout(client)
    first = capture(client, started)

    assert first.status_code == 504
    assert first.json()["order_ref"] == started["order_ref"]
    assert payment(sql, started["order_ref"])["status"] == "COMPLETED"

    # The buyer retries the same order; nothing checks it is already paid.
    retry = client.post("/api/checkout", json={"order_ref": started["order_ref"]})
    assert retry.status_code == 200
    assert retry.json()["paypal_order_id"] != started["paypal_order_id"]
    second = capture(client, retry.json())
    assert second.status_code == 200

    captures = paypal.captures_for(started["order_ref"])
    assert len(captures) == 2
    # The shop's row keeps the first capture; only PayPal holds the second.
    assert payment(sql, started["order_ref"])["paypal_capture_id"] == captures[0]
    assert "PayPal charged this order 2 times." in client.get("/sources").text


def test_a_sandbox_that_refuses_duplicate_invoices_explains_itself(client, paypal):
    client.post("/demo/faults", data={"queue": "DOUBLE_CHARGE"})
    started = checkout(client)
    capture(client, started)
    paypal.refuse_duplicate_invoices = True

    retry = client.post("/api/checkout", json={"order_ref": started["order_ref"]})
    assert retry.status_code == 409
    assert "Allow duplicate invoice IDs" in retry.json()["message"]


def test_checkout_refuses_an_unknown_plan_and_a_foreign_capture(client):
    assert client.post("/api/checkout", json={"plan_id": "gold", "email": "a@b.c"}).status_code \
        == 422
    first, second = checkout(client), checkout(client, plan_id="starter-monthly")
    mixed = {"order_ref": first["order_ref"], "paypal_order_id": second["paypal_order_id"]}
    assert capture(client, mixed).status_code == 404


def test_an_addon_has_no_subscription(client, sql):
    clear_faults(client)
    started = checkout(client, plan_id="onboarding")
    assert capture(client, started).status_code == 200
    invoice = sql("SELECT * FROM invoices WHERE order_ref = %s", (started["order_ref"],))[0]
    assert invoice["subscription_id"] is None
    assert invoice["status"] == "paid"


def test_faults_fire_in_queue_order(client, sql):
    client.post("/demo/faults", data={"queue": "LOST_CONFIRMATION,DOUBLE_CHARGE"})
    assert capture(client, checkout(client)).status_code == 500
    assert capture(client, checkout(client)).status_code == 504
    assert capture(client, checkout(client)).status_code == 200
    assert sql("SELECT fault_queue FROM demo_meta")[0]["fault_queue"] == []


def test_the_store_page_renders_the_plans_and_the_next_fault(client):
    page = client.get("/").text
    assert "Pro, monthly" in page
    assert "PayPal Sandbox only" in page
    assert "lose PayPal&#39;s payment confirmation" in page or \
        "lose PayPal's payment confirmation" in page
    assert store.LOST_CONFIRMATION in client.get("/demo").text
