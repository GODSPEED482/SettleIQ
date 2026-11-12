"""The first start, the fault queue and the billing discrepancies seeded for the SaaS pack."""

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


def test_nothing_is_ever_deleted(client, sql):
    """There is no reset: removing payment rows would strand their PayPal captures."""
    assert all(r.path != "/demo/reset" for r in client.app.routes)
    assert client.post("/demo/reset").status_code in (404, 405)
    client.post("/demo/faults", data={"queue": ""})
    started = client.post("/api/checkout",
                          json={"plan_id": "pro-monthly", "email": "b@example.com"}).json()
    client.post("/api/checkout/capture", json=started)
    assert "Reset discrepancies" not in client.get("/demo").text
    assert [r["order_ref"] for r in sql("SELECT order_ref FROM orders")] == [started["order_ref"]]


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
