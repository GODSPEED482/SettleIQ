"""Internal-merchant write adapter (task 5.5) and its safety tests (task 5.12, AC-MERCH-01..07).

These cover the adapter contract of spec 4.4.1. The grant, LIVE-scope and case-resolution parts
of AC-MERCH-01 and AC-MERCH-02 need the execution chain (tasks 5.1 to 5.6) and are added with it.

The merchant database is a scratch schema in the test database, set up by the shipped
`merchant_setup.sql` with a per-test restricted role, so every test runs as the role a merchant
would grant, never as the owner.
"""

import ast
import os
import threading
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.connectors.internal_merchant import actions
from app.connectors.internal_merchant.actions import (
    PaymentReadParams,
    StatusCorrectParams,
    check_health,
    correct_payment_status,
    read_payment,
)
from app.connectors.manifest import load_connector_manifests
from app.errors import ApiError
from tests.support import grant_set
from tests.test_semantics_store import Member
from tests.test_sources import create_ok

PACKAGE = Path(actions.__file__).parent
SETUP_SQL = (PACKAGE / "merchant_setup.sql").read_text(encoding="utf-8")
SECRET = "env:SOURCE_SECRET_TEST_MERCHANT_WRITE"
READ_SECRET = "env:SOURCE_SECRET_TEST_MERCHANT_READ"

BASE_TABLE = """
CREATE TABLE payments (
    payment_id text NOT NULL,
    merchant_id text NOT NULL,
    status text NOT NULL,
    amount numeric(20, 6) NOT NULL,
    currency char(3) NOT NULL,
    order_ref text,
    note text
);
INSERT INTO payments VALUES
    ('PAY-1', 'M-A', 'PENDING', 25.500000, 'USD', 'ORD-1', 'internal'),
    ('PAY-2', 'M-A', 'COMPLETED', 10.000000, 'USD', 'ORD-2', NULL),
    ('PAY-DUP', 'M-A', 'PENDING', 5.000000, 'USD', 'ORD-3', NULL),
    ('PAY-DUP', 'M-A', 'PENDING', 5.000000, 'USD', 'ORD-3', NULL),
    ('PAY-1', 'M-B', 'PENDING', 99.990000, 'EUR', 'ORD-B1', NULL),
    ('PAY-B', 'M-B', 'PENDING', 7.000000, 'EUR', 'ORD-B2', NULL);
"""


class Merchant:
    """A scratch merchant database: base table, setup script and restricted role."""

    def __init__(self) -> None:
        self.owner_url = os.environ["MIGRATION_DATABASE_URL"]
        self.schema = f"merchant_{uuid4().hex[:12]}"
        self.role = f"merchant_writer_{uuid4().hex[:12]}"
        password = uuid4().hex
        self.engine = create_engine(self.owner_url, poolclass=NullPool)
        self._raw(
            f"CREATE SCHEMA {self.schema}; SET search_path TO {self.schema};"
            + BASE_TABLE
            + SETUP_SQL.replace("settleiq_merchant_writer", self.role)
            + f"ALTER ROLE {self.role} PASSWORD '{password}';"
            + f"ALTER ROLE {self.role} SET search_path = {self.schema};"
        )
        self.role_url = (
            make_url(self.owner_url)
            .set(username=self.role, password=password)
            .render_as_string(hide_password=False)
        )

    def _raw(self, sql: str) -> None:
        # The setup script is run as the merchant's DBA would run it: verbatim, no parameters.
        raw = self.engine.raw_connection()
        try:
            raw.cursor().execute(sql)
            raw.commit()
        finally:
            raw.close()

    def rows(self) -> list[tuple]:
        with self.engine.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    text(
                        f"SELECT payment_id, merchant_id, status, amount, note "
                        f"FROM {self.schema}.payments ORDER BY merchant_id, payment_id, order_ref"
                    )
                )
            ]

    def records(self) -> list[tuple]:
        with self.engine.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    text(
                        f"SELECT merchant_id, idempotency_key, outcome "
                        f"FROM {self.schema}.settleiq_action_records ORDER BY recorded_at"
                    )
                )
            ]

    def as_role(self, sql: str) -> None:
        engine = create_engine(self.role_url, poolclass=NullPool)
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        finally:
            engine.dispose()

    def drop(self) -> None:
        self._raw(
            f"DROP SCHEMA {self.schema} CASCADE; DROP OWNED BY {self.role}; DROP ROLE {self.role};"
        )
        self.engine.dispose()


def admin(name: str = "T") -> Member:
    """A tenant owner who also holds the Integration Admin set, which stores secret references."""
    member = Member(name)
    grant_set(member.tenant, member.principal.id, "integration_admin")
    return member


@pytest.fixture
def merchant(monkeypatch):
    db = Merchant()
    monkeypatch.setenv("SOURCE_SECRET_TEST_MERCHANT_WRITE", db.role_url)
    monkeypatch.setenv("SOURCE_SECRET_TEST_MERCHANT_READ", db.owner_url)
    yield db
    db.drop()


def adapter_source(member: Member, merchant_id: str | None = "M-A", **overrides) -> UUID:
    config = {} if merchant_id is None else {"merchant_id": merchant_id}
    source = create_ok(
        member,
        connector_id="internal-merchant",
        display_name="Merchant writes",
        config=config,
        secret_reference=SECRET,
        **overrides,
    )
    return UUID(source["id"])


def correction(member: Member, payment_id: str = "PAY-1", **overrides) -> dict:
    return {
        "tenant_id": str(member.tenant),
        "payment_id": payment_id,
        "expected_current_status": "PENDING",
        "requested_status": "COMPLETED",
    } | overrides


def correct(member: Member, source_id: UUID, params: dict, key: str | None = None):
    return correct_payment_status(member.tenant, source_id, params, key or str(uuid4()))


def read(member: Member, source_id: UUID, payment_id: str):
    return read_payment(
        member.tenant, source_id, {"tenant_id": str(member.tenant), "payment_id": payment_id}
    )


def refused(call) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call()
    return caught.value


# --- AC-MERCH-01: one row, then independent readback ----------------------------------------


@pytest.mark.db
def test_correction_changes_exactly_one_row_and_readback_sees_completed(merchant):
    member = admin()
    source = adapter_source(member)
    before = merchant.rows()

    assert read(member, source, "PAY-1").status == "PENDING"
    outcome = correct(member, source, correction(member))

    assert (outcome.outcome, outcome.from_status, outcome.to_status) == (
        "APPLIED", "PENDING", "COMPLETED",
    )
    assert not outcome.replayed
    assert len(outcome.evidence_hash) == 64
    after = merchant.rows()
    changed = [(b, a) for b, a in zip(before, after, strict=True) if b != a]
    assert changed == [
        (("PAY-1", "M-A", "PENDING", before[0][3], "internal"),
         ("PAY-1", "M-A", "COMPLETED", before[0][3], "internal")),
    ]
    payment = read(member, source, "PAY-1")
    assert (payment.status, payment.amount, payment.currency, payment.order_ref) == (
        "COMPLETED", "25.500000", "USD", "ORD-1",
    )


# --- AC-MERCH-02: stale state fails closed ----------------------------------------------------


@pytest.mark.db
def test_already_completed_payment_is_stale_with_no_mutation(merchant):
    member = admin()
    source = adapter_source(member)
    before = merchant.rows()

    outcome = correct(member, source, correction(member, "PAY-2"))

    assert (outcome.outcome, outcome.from_status, outcome.to_status) == (
        "STALE_STATE", "COMPLETED", None,
    )
    assert merchant.rows() == before
    assert merchant.records()[-1][2] == "STALE_STATE"


@pytest.mark.db
def test_concurrent_change_after_precondition_read_is_stale(merchant):
    member = admin()
    source = adapter_source(member)
    assert read(member, source, "PAY-1").status == "PENDING"
    with merchant.engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {merchant.schema}.payments SET status = 'FAILED' "
                "WHERE payment_id = 'PAY-1' AND merchant_id = 'M-A'"
            )
        )
    before = merchant.rows()

    outcome = correct(member, source, correction(member))

    assert (outcome.outcome, outcome.from_status) == ("STALE_STATE", "FAILED")
    assert merchant.rows() == before


# --- AC-MERCH-03: zero, several or foreign targets --------------------------------------------


@pytest.mark.db
def test_unknown_ambiguous_and_foreign_targets_fail_closed_alike(merchant):
    tenant_a, tenant_b = admin("A"), admin("B")
    source_a = adapter_source(tenant_a, "M-A")
    adapter_source(tenant_b, "M-B")
    before = merchant.rows()

    outcomes = [
        correct(tenant_a, source_a, correction(tenant_a, payment_id))
        for payment_id in ("PAY-404", "PAY-DUP", "PAY-B")
    ]

    assert {(o.outcome, o.from_status, o.to_status) for o in outcomes} == {
        ("TARGET_UNRESOLVED", None, None)
    }
    assert merchant.rows() == before
    for payment_id in ("PAY-404", "PAY-DUP", "PAY-B"):
        error = refused(lambda p=payment_id: read(tenant_a, source_a, p))
        assert (error.status, error.code) == (409, "MERCHANT_TARGET_UNRESOLVED")


@pytest.mark.db
def test_same_payment_id_under_another_merchant_is_never_touched(merchant):
    tenant_a, tenant_b = admin("A"), admin("B")
    source_a = adapter_source(tenant_a, "M-A")
    source_b = adapter_source(tenant_b, "M-B")

    assert correct(tenant_a, source_a, correction(tenant_a)).outcome == "APPLIED"

    statuses = {(row[0], row[1]): row[2] for row in merchant.rows()}
    assert statuses[("PAY-1", "M-A")] == "COMPLETED"
    assert statuses[("PAY-1", "M-B")] == "PENDING"
    assert read(tenant_b, source_b, "PAY-1").status == "PENDING"


@pytest.mark.db
def test_another_tenants_source_or_tenant_id_reveals_nothing(merchant):
    tenant_a, tenant_b = admin("A"), admin("B")
    source_a = adapter_source(tenant_a, "M-A")
    source_b = adapter_source(tenant_b, "M-B")
    before = merchant.rows()

    # Tenant A naming tenant B's source: B's source does not exist in A's tenant context.
    error = refused(lambda: correct(tenant_a, source_b, correction(tenant_a, "PAY-B")))
    assert (error.status, error.code) == (404, "NOT_FOUND")
    # Tenant A's own source, with tenant B named in the parameters.
    foreign = correction(tenant_a, "PAY-B", tenant_id=str(tenant_b.tenant))
    error = refused(lambda: correct(tenant_a, source_a, foreign))
    assert (error.status, error.code) == (409, "MERCHANT_TARGET_UNRESOLVED")
    assert merchant.rows() == before


# --- AC-MERCH-04: transition allowlist, before any statement ---------------------------------


@pytest.mark.parametrize(
    ("expected", "requested"),
    [
        ("COMPLETED", "PENDING"),
        ("PENDING", "REFUNDED"),
        ("PENDING", "PENDING"),
        ("FAILED", "COMPLETED"),
        ("pending", "completed"),
    ],
)
def test_only_pending_to_completed_validates(monkeypatch, expected, requested):
    def no_connection(*_args, **_kwargs):
        raise AssertionError("a database was reached before validation")

    monkeypatch.setattr(actions, "scoped_tx", no_connection)
    monkeypatch.setattr(actions, "_write_engine", no_connection)
    tenant = uuid4()
    params = {
        "tenant_id": str(tenant),
        "payment_id": "PAY-1",
        "expected_current_status": expected,
        "requested_status": requested,
    }
    error = refused(lambda: correct_payment_status(tenant, uuid4(), params, "key-1"))
    assert (error.status, error.code) == (422, "VALIDATION_ERROR")


# --- AC-MERCH-05: idempotency ----------------------------------------------------------------


@pytest.mark.db
def test_same_key_and_payload_returns_the_original_outcome_once(merchant):
    member = admin()
    source = adapter_source(member)

    first = correct(member, source, correction(member), key="exec-1")
    again = correct(member, source, correction(member), key="exec-1")

    assert first.outcome == again.outcome == "APPLIED"
    assert first.evidence_hash == again.evidence_hash
    assert (first.replayed, again.replayed) == (False, True)
    assert merchant.records() == [("M-A", "exec-1", "APPLIED")]


@pytest.mark.db
def test_same_key_with_a_different_payload_is_refused_without_mutation(merchant):
    member = admin()
    source = adapter_source(member)
    correct(member, source, correction(member, "PAY-2"), key="exec-1")
    before = merchant.rows()

    error = refused(lambda: correct(member, source, correction(member, "PAY-1"), key="exec-1"))

    assert (error.status, error.code) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert merchant.rows() == before
    assert len(merchant.records()) == 1


@pytest.mark.db
def test_concurrent_attempts_under_one_key_mutate_once(merchant):
    member = admin()
    source = adapter_source(member)
    barrier = threading.Barrier(4)
    results: list = []

    def attempt():
        barrier.wait()
        results.append(correct(member, source, correction(member), key="exec-race"))

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 4
    assert {r.outcome for r in results} == {"APPLIED"}
    assert {r.evidence_hash for r in results} == {results[0].evidence_hash}
    assert sum(not r.replayed for r in results) == 1
    assert merchant.records() == [("M-A", "exec-race", "APPLIED")]


@pytest.mark.db
def test_keys_are_scoped_to_the_merchant(merchant):
    tenant_a, tenant_b = admin("A"), admin("B")
    source_a = adapter_source(tenant_a, "M-A")
    source_b = adapter_source(tenant_b, "M-B")

    assert correct(tenant_a, source_a, correction(tenant_a), key="shared").outcome == "APPLIED"
    assert correct(tenant_b, source_b, correction(tenant_b), key="shared").outcome == "APPLIED"


# --- AC-MERCH-06: declared fields only, constant SQL, restricted role -------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"sql": "UPDATE payments SET amount = 0"},
        {"table": "payments"},
        {"column": "amount"},
        {"amount": "0.00"},
    ],
)
def test_undeclared_fields_are_rejected(monkeypatch, extra):
    monkeypatch.setattr(actions, "scoped_tx", None)
    tenant = uuid4()
    params = {
        "tenant_id": str(tenant),
        "payment_id": "PAY-1",
        "expected_current_status": "PENDING",
        "requested_status": "COMPLETED",
    } | extra
    error = refused(lambda: correct_payment_status(tenant, uuid4(), params, "key-1"))
    assert error.code == "VALIDATION_ERROR"
    error = refused(
        lambda: read_payment(tenant, uuid4(), {"tenant_id": str(tenant), "payment_id": "P"} | extra)
    )
    assert error.code == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "payment_id",
    ["PAY-1'; DROP TABLE payments; --", "PAY-1 OR 1=1", "PAY 1", "", "x" * 201],
)
def test_sql_text_in_an_identifier_is_rejected(monkeypatch, payment_id):
    monkeypatch.setattr(actions, "scoped_tx", None)
    tenant = uuid4()
    params = {"tenant_id": str(tenant), "payment_id": payment_id}
    assert refused(lambda: read_payment(tenant, uuid4(), params)).code == "VALIDATION_ERROR"


def test_every_statement_is_a_constant_with_bind_parameters_only():
    """No caller-supplied value can reach SQL text: every text() argument is a literal string,
    and nothing in the package builds SQL by formatting or executes driver SQL directly."""
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                assert name not in {"exec_driver_sql", "raw_connection"}, path
                if name == "text":
                    assert len(node.args) == 1, path
                    assert isinstance(node.args[0], ast.Constant), (path, node.lineno)
                    assert isinstance(node.args[0].value, str)
                if name == "execute":
                    statement = node.args[0]
                    assert isinstance(statement, ast.Name) and statement.id.startswith("_"), (
                        path, node.lineno,
                    )


def test_parameter_models_match_the_manifest():
    manifest = load_connector_manifests()["internal-merchant"]
    declared = {c.key: c.parameters for c in manifest.capabilities}
    assert set(PaymentReadParams.model_fields) == set(
        declared["merchant.payment.read"]["properties"]
    )
    assert set(StatusCorrectParams.model_fields) == set(
        declared["merchant.payment_status.correct"]["properties"]
    )
    for model in (PaymentReadParams, StatusCorrectParams):
        assert model.model_config["extra"] == "forbid"


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE settleiq_payments SET amount = 0 WHERE payment_id = 'PAY-1'",
        "UPDATE settleiq_payments SET merchant_id = 'M-B' WHERE payment_id = 'PAY-1'",
        "DELETE FROM settleiq_payments WHERE payment_id = 'PAY-1'",
        "INSERT INTO settleiq_payments (payment_id, merchant_id, status) VALUES ('X', 'M-A', 'P')",
        "SELECT note FROM payments",
        "UPDATE payments SET status = 'COMPLETED'",
        "UPDATE settleiq_action_records SET outcome = 'APPLIED'",
        "DELETE FROM settleiq_action_records",
        "CREATE TABLE intruder (id int)",
        "DROP VIEW settleiq_payments",
    ],
)
def test_the_adapter_role_can_only_read_and_correct_status(merchant, statement):
    before = merchant.rows()
    with pytest.raises(DBAPIError, match="permission denied|must be owner"):
        merchant.as_role(statement)
    assert merchant.rows() == before


@pytest.mark.db
def test_the_adapter_role_can_run_the_declared_statements(merchant):
    merchant.as_role(
        "SELECT payment_id, status FROM settleiq_payments WHERE merchant_id = 'M-A' FOR UPDATE"
    )
    merchant.as_role(
        "UPDATE settleiq_payments SET status = 'COMPLETED' "
        "WHERE merchant_id = 'M-A' AND payment_id = 'PAY-1' AND status = 'PENDING'"
    )


# --- AC-MERCH-07 and the separate registration (spec 4.4.1) -----------------------------------


@pytest.mark.db
def test_a_merchant_postgres_read_source_cannot_write(merchant):
    member = admin()
    read_source = create_ok(
        member,
        connector_id="merchant-postgres",
        display_name="Merchant reads",
        config={"merchant_id": "M-A"},
        secret_reference=READ_SECRET,
    )["id"]
    read_source = UUID(read_source)
    before = merchant.rows()

    error = refused(lambda: correct(member, read_source, correction(member)))

    assert (error.status, error.code) == (409, "CAPABILITY_NOT_DECLARED")
    assert merchant.rows() == before


@pytest.mark.db
def test_a_source_without_a_merchant_binding_fails_closed(merchant):
    member = admin()
    source = adapter_source(member, merchant_id=None)
    error = refused(lambda: correct(member, source, correction(member)))
    assert error.code == "MERCHANT_BINDING_MISSING"


@pytest.mark.db
def test_an_unresolved_secret_fails_closed(merchant, monkeypatch):
    member = admin()
    source = adapter_source(member)
    monkeypatch.delenv("SOURCE_SECRET_TEST_MERCHANT_WRITE")
    error = refused(lambda: correct(member, source, correction(member)))
    assert error.code == "SECRET_UNRESOLVED"
    assert merchant.rows()[0][2] == "PENDING"


@pytest.mark.db
def test_unknown_source_is_404(merchant):
    member = admin()
    error = refused(lambda: correct(member, uuid4(), correction(member)))
    assert (error.status, error.code) == (404, "NOT_FOUND")


@pytest.mark.db
def test_health_check_reports_the_database_connection(merchant):
    member = admin()
    source = adapter_source(member)
    assert check_health(member.tenant, source) == {
        "signal": "database_connection", "status": "OK",
    }
    with merchant.engine.begin() as conn:
        conn.execute(text(f"DROP VIEW {merchant.schema}.settleiq_payments"))
    assert check_health(member.tenant, source) == {
        "signal": "database_connection", "status": "UNAVAILABLE",
    }
