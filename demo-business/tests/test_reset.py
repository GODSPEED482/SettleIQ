"""Reset discrepancies (task 6.14) and the billing discrepancies it plants for the SaaS pack."""

from conftest import FakePayPal

from shop import db, store


def subscriptions(sql):
    return sql("SELECT s.*, a.name FROM subscriptions s JOIN accounts a USING (account_id) "
               "ORDER BY a.name")


def test_the_first_start_seeds_generation_one_with_both_faults_queued(client, sql):
    meta = sql("SELECT * FROM demo_meta")[0]
    assert meta["generation"] == 1
    assert meta["fault_queue"] == ["LOST_CONFIRMATION", "DOUBLE_CHARGE"]
    assert [s["name"] for s in subscriptions(sql)] == ["Ada Byron", "Grace Hopper",
                                                       "Linus Pauling"]


def test_the_seed_plants_the_two_billing_discrepancies_and_one_consistent_customer(
        client, settings):
    with db.connect(settings) as conn:
        views = {s["account_name"]: s["flags"] for s in store.subscriptions_view(conn)}
    assert views["Ada Byron"] == []
    assert views["Grace Hopper"] == ["Active for a period with no paid invoice."]
    assert views["Linus Pauling"] == ["Paid for a period the subscription does not cover."]
    page = client.get("/sources").text
    assert "Active for a period with no paid invoice." in page


def test_a_restart_keeps_the_state(make_client, sql):
    make_client()
    client = make_client()
    assert sql("SELECT generation FROM demo_meta")[0]["generation"] == 1
    assert client.get("/healthz").json() == {"status": "ok"}


def test_reset_restores_every_discrepancy_under_new_identifiers(client, sql, paypal):
    client.post("/demo/faults", data={"queue": ""})
    started = client.post("/api/checkout",
                          json={"plan_id": "pro-monthly", "email": "b@example.com"}).json()
    client.post("/api/checkout/capture", json=started)
    before = {r["subscription_id"] for r in subscriptions(sql)}

    response = client.post("/demo/reset")
    assert response.status_code == 200
    assert "Reset to generation 2." in response.text

    meta = sql("SELECT * FROM demo_meta")[0]
    assert meta["generation"] == 2
    assert meta["fault_queue"] == ["LOST_CONFIRMATION", "DOUBLE_CHARGE"]
    assert sql("SELECT * FROM orders") == []
    assert sql("SELECT * FROM payments") == []
    after = subscriptions(sql)
    assert [s["name"] for s in after] == ["Ada Byron", "Grace Hopper", "Linus Pauling"]
    assert not before & {s["subscription_id"] for s in after}
    assert all(s["subscription_id"].startswith("SUB-2-") for s in after)
    emails = [r["email"] for r in sql("SELECT email FROM accounts")]
    assert all(".g2@" in e for e in emails)

    # New checkouts after the reset get generation-2 identifiers too.
    again = client.post("/api/checkout",
                        json={"plan_id": "pro-monthly", "email": "b@example.com"}).json()
    assert again["order_ref"].startswith("ORD-2-")
    assert again["order_ref"] != started["order_ref"]


def test_reset_is_refused_during_the_cooldown(make_client, sql):
    client = make_client(reset_cooldown_seconds=600)
    client.post("/api/checkout", json={"plan_id": "pro-monthly", "email": "c@example.com"})

    response = client.post("/demo/reset")
    assert "Reset again in" in response.text
    assert sql("SELECT generation FROM demo_meta")[0]["generation"] == 1
    assert len(sql("SELECT * FROM orders")) == 1


def test_an_unknown_fault_is_refused(client, sql):
    response = client.post("/demo/faults", data={"queue": "LOST_CONFIRMATION,EXPLODE"})
    assert "Unknown fault: EXPLODE" in response.text
    assert sql("SELECT fault_queue FROM demo_meta")[0]["fault_queue"] == [
        "LOST_CONFIRMATION", "DOUBLE_CHARGE"]


def test_the_source_view_reads_paypal_through_a_short_cache(client):
    paypal = FakePayPal()
    calls = []
    original = paypal.get_order

    def counting(order_id):
        calls.append(order_id)
        return original(order_id)

    paypal.get_order = counting
    reader = store.PayPalReader(paypal)
    order_id = paypal.create_order("ORD-1-1", "PAY-1-2", store.PLANS[0][4], "USD", "x")
    reader.order(order_id)
    reader.order(order_id)
    assert calls == [order_id]
