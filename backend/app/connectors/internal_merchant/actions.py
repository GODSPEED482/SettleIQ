"""Internal-merchant write adapter (task 5.5, spec 4.4.1, AC-MERCH-01..07).

Two capabilities, both against the tenant's own `internal-merchant` source:

- `merchant.payment.read` (1.0.0): read-only. The status, amount, currency and order reference
  of exactly one payment, for precondition checks and post-action verification.
- `merchant.payment_status.correct` (1.0.0): the only write. PENDING -> COMPLETED on exactly one
  row, by a conditional update that requires the persisted status to still be PENDING.

The merchant exposes its payments to the adapter through `settleiq_payments` and records each
action in `settleiq_action_records`, both created by `merchant_setup.sql` together with a role
that can do nothing else. Rows are bound to the tenant by the source's `config.merchant_id`, so
two tenants pointed at one database still never reach each other's rows.

Every statement below is a constant with bind parameters; no caller value reaches SQL text
(AC-MERCH-06). Parameters are validated before any connection is opened (AC-MERCH-04).

Invocation is Worker-only and grant-bound (POL-09): the Worker (task 5.6) calls these under an
execution grant and audits the attempt. Nothing here is reachable from a public route.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.db import scoped_tx
from app.errors import ApiError, conflict, not_found, validation_failed
from app.sources.postgres import CONNECT_TIMEOUT_S, STATEMENT_TIMEOUT_MS, read_only_engine
from app.sources.secrets import resolve_secret

CONNECTOR_ID = "internal-merchant"
READ_CAPABILITY = "merchant.payment.read"
CORRECT_CAPABILITY = "merchant.payment_status.correct"
LOCK_TIMEOUT_MS = 5_000

# Identifiers are opaque business keys: letters, digits and . _ : - only, so SQL text, quotes,
# whitespace, or table and column names never validate.
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_IDENTIFIER_RE = re.compile(_IDENTIFIER)

Outcome = Literal["APPLIED", "STALE_STATE", "TARGET_UNRESOLVED"]


class PaymentReadParams(BaseModel):
    """The declared parameters of merchant.payment.read, and nothing else."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    payment_id: str = Field(pattern=_IDENTIFIER)


class StatusCorrectParams(BaseModel):
    """The declared parameters of merchant.payment_status.correct. PB-001 V1 permits only
    PENDING -> COMPLETED, so any other pair fails here (AC-MERCH-04)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    payment_id: str = Field(pattern=_IDENTIFIER)
    expected_current_status: Literal["PENDING"]
    requested_status: Literal["COMPLETED"]


@dataclass(frozen=True)
class MerchantPayment:
    payment_id: str
    status: str
    amount: str
    currency: str
    order_ref: str | None


@dataclass(frozen=True)
class ActionOutcome:
    """The durable outcome of one correction, as recorded in the merchant database.

    `replayed` is true when the idempotency key had already been recorded and this is that
    original outcome rather than a new attempt.
    """

    outcome: Outcome
    payment_id: str
    from_status: str | None
    to_status: str | None
    evidence_hash: str
    replayed: bool


# --- SQL: constants only, bind parameters only (AC-MERCH-06) ---------------------------------

_LOAD_SOURCE = text(
    """
    SELECT connector_id, config, secret_reference, status, capabilities
    FROM data_sources WHERE id = :id
    """
)
_READ_PAYMENT = text(
    """
    SELECT payment_id, status, amount, currency, order_ref
    FROM settleiq_payments
    WHERE merchant_id = :merchant_id AND payment_id = :payment_id
    LIMIT 2
    """
)
_LOCK_PAYMENT = text(
    """
    SELECT status
    FROM settleiq_payments
    WHERE merchant_id = :merchant_id AND payment_id = :payment_id
    LIMIT 2
    FOR UPDATE
    """
)
_CORRECT_STATUS = text(
    """
    UPDATE settleiq_payments SET status = :requested_status
    WHERE merchant_id = :merchant_id AND payment_id = :payment_id
      AND status = :expected_current_status
    """
)
_FIND_RECORD = text(
    """
    SELECT merchant_id, idempotency_key, payload_hash, capability, payment_id, expected_status,
        requested_status, outcome, from_status, to_status
    FROM settleiq_action_records
    WHERE merchant_id = :merchant_id AND idempotency_key = :idempotency_key
    """
)
_INSERT_RECORD = text(
    """
    INSERT INTO settleiq_action_records (merchant_id, idempotency_key, payload_hash, capability,
        payment_id, expected_status, requested_status, outcome, from_status, to_status)
    VALUES (:merchant_id, :idempotency_key, :payload_hash, :capability, :payment_id,
        :expected_status, :requested_status, :outcome, :from_status, :to_status)
    """
)
_PROBE_PAYMENTS = text("SELECT 1 FROM settleiq_payments LIMIT 0")
_PROBE_RECORDS = text("SELECT 1 FROM settleiq_action_records LIMIT 0")


# --- errors ----------------------------------------------------------------------------------


def _unresolved() -> ApiError:
    """One response for an unknown, ambiguous or foreign target, so it reveals nothing about
    another tenant's or merchant's rows (AC-MERCH-03)."""
    return conflict("MERCHANT_TARGET_UNRESOLVED", "The payment could not be resolved.")


def _unavailable() -> ApiError:
    return conflict("MERCHANT_UNAVAILABLE", "The merchant database could not be reached.")


# --- validation, before any connection -------------------------------------------------------


def _parse(model: type[BaseModel], params: Any) -> Any:
    if not isinstance(params, dict):
        raise validation_failed("Action parameters must be an object.")
    try:
        return model.model_validate(params)
    except ValidationError:
        # The validation detail could echo the rejected value; name no field values.
        raise validation_failed("Action parameters do not match the declared schema.") from None


def _check_tenant(tenant_id: UUID, declared: UUID) -> None:
    if declared != tenant_id:
        raise _unresolved()


def _check_key(idempotency_key: Any) -> str:
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
        raise validation_failed("An idempotency key of 1 to 200 characters is required.")
    return idempotency_key


@dataclass(frozen=True)
class _Binding:
    merchant_id: str
    secret: str


def _binding(tenant_id: UUID, source_id: UUID, capability: str) -> _Binding:
    """The tenant's own internal-merchant source, resolved in its tenant context only.

    A merchant-postgres read source never qualifies: registering the read source grants no
    write authority (spec 4.4.1).
    """
    with scoped_tx(tenant_id) as conn:
        row = conn.execute(_LOAD_SOURCE, {"id": source_id}).first()
    if row is None:
        raise not_found()
    if row.connector_id != CONNECTOR_ID or capability not in {
        item.get("key") for item in row.capabilities or []
    }:
        raise conflict("CAPABILITY_NOT_DECLARED", "This source does not declare the capability.")
    if row.status != "ACTIVE":
        raise conflict("SOURCE_INACTIVE", "Only an active source can be used.")
    merchant_id = (row.config or {}).get("merchant_id")
    if not isinstance(merchant_id, str) or not _IDENTIFIER_RE.match(merchant_id):
        raise conflict(
            "MERCHANT_BINDING_MISSING", "The source has no valid merchant_id binding."
        )
    return _Binding(merchant_id, resolve_secret(tenant_id, row.secret_reference))


def _write_engine(secret: str) -> Engine:
    """A read-write session with bounded statement and lock waits."""
    try:
        url = make_url(secret)
    except ArgumentError:
        raise _unavailable() from None
    if url.get_backend_name() != "postgresql":
        raise _unavailable()
    return create_engine(
        url.set(drivername="postgresql+psycopg"),
        poolclass=NullPool,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "options": (
                f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c lock_timeout={LOCK_TIMEOUT_MS}"
            ),
        },
    )


def _payload_hash(capability: str, params: StatusCorrectParams, merchant_id: str) -> str:
    payload = {
        "capability": capability,
        "merchant_id": merchant_id,
        **params.model_dump(mode="json"),
    }
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def _outcome(record: Any, *, replayed: bool) -> ActionOutcome:
    evidence = {
        "merchant_id": record.merchant_id,
        "idempotency_key": record.idempotency_key,
        "payload_hash": record.payload_hash,
        "capability": record.capability,
        "payment_id": record.payment_id,
        "expected_status": record.expected_status,
        "requested_status": record.requested_status,
        "outcome": record.outcome,
        "from_status": record.from_status,
        "to_status": record.to_status,
    }
    return ActionOutcome(
        outcome=record.outcome,
        payment_id=record.payment_id,
        from_status=record.from_status,
        to_status=record.to_status,
        evidence_hash=hashlib.sha256(rfc8785.dumps(evidence)).hexdigest(),
        replayed=replayed,
    )


def _replay(conn: Connection, keys: dict[str, str], payload_hash: str) -> ActionOutcome | None:
    record = conn.execute(_FIND_RECORD, keys).first()
    if record is None:
        return None
    if record.payload_hash != payload_hash:
        raise conflict(
            "IDEMPOTENCY_KEY_REUSED",
            "This idempotency key was already used with a different action payload.",
        )
    return _outcome(record, replayed=True)


@dataclass(frozen=True)
class _Record:
    merchant_id: str
    idempotency_key: str
    payload_hash: str
    capability: str
    payment_id: str
    expected_status: str
    requested_status: str
    outcome: Outcome
    from_status: str | None
    to_status: str | None


# --- capabilities ----------------------------------------------------------------------------


def read_payment(tenant_id: UUID, source_id: UUID, params: Any) -> MerchantPayment:
    """merchant.payment.read: exactly one tenant-scoped payment, read-only."""
    parsed: PaymentReadParams = _parse(PaymentReadParams, params)
    _check_tenant(tenant_id, parsed.tenant_id)
    binding = _binding(tenant_id, source_id, READ_CAPABILITY)
    engine = read_only_engine(binding.secret)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                _READ_PAYMENT,
                {"merchant_id": binding.merchant_id, "payment_id": parsed.payment_id},
            ).all()
    except SQLAlchemyError:
        raise _unavailable() from None
    finally:
        engine.dispose()
    if len(rows) != 1:
        raise _unresolved()
    row = rows[0]
    return MerchantPayment(
        payment_id=row.payment_id,
        status=row.status,
        amount=str(row.amount),
        currency=row.currency,
        order_ref=row.order_ref,
    )


def correct_payment_status(
    tenant_id: UUID, source_id: UUID, params: Any, idempotency_key: Any
) -> ActionOutcome:
    """merchant.payment_status.correct: PENDING -> COMPLETED on exactly one row, once per key.

    In one merchant transaction: replay a recorded key, lock the target, refuse a zero or
    ambiguous match or a stale status with no mutation, apply the conditional update, and record
    the outcome. A concurrent attempt under the same key loses the record insert, rolls back and
    returns the winner's outcome, so the key maps to exactly one mutation (AC-MERCH-05).
    """
    parsed: StatusCorrectParams = _parse(StatusCorrectParams, params)
    key = _check_key(idempotency_key)
    _check_tenant(tenant_id, parsed.tenant_id)
    binding = _binding(tenant_id, source_id, CORRECT_CAPABILITY)
    payload_hash = _payload_hash(CORRECT_CAPABILITY, parsed, binding.merchant_id)
    keys = {"merchant_id": binding.merchant_id, "idempotency_key": key}
    target = {"merchant_id": binding.merchant_id, "payment_id": parsed.payment_id}

    engine = _write_engine(binding.secret)
    try:
        try:
            with engine.begin() as conn:
                prior = _replay(conn, keys, payload_hash)
                if prior is not None:
                    return prior
                record = _attempt(conn, parsed, target, keys, payload_hash)
                conn.execute(_INSERT_RECORD, record)
        except IntegrityError:
            # Another attempt under this key committed first; its outcome is the outcome.
            with engine.connect() as conn:
                prior = _replay(conn, keys, payload_hash)
            if prior is None:
                raise _unavailable() from None
            return prior
    except SQLAlchemyError:
        raise _unavailable() from None
    finally:
        engine.dispose()
    return _outcome(_Record(**record), replayed=False)


def _attempt(
    conn: Connection,
    parsed: StatusCorrectParams,
    target: dict[str, str],
    keys: dict[str, str],
    payload_hash: str,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        **keys,
        "payload_hash": payload_hash,
        "capability": CORRECT_CAPABILITY,
        "payment_id": parsed.payment_id,
        "expected_status": parsed.expected_current_status,
        "requested_status": parsed.requested_status,
        "from_status": None,
        "to_status": None,
    }
    locked = conn.execute(_LOCK_PAYMENT, target).all()
    if len(locked) != 1:
        return record | {"outcome": "TARGET_UNRESOLVED"}
    current = locked[0].status
    if current != parsed.expected_current_status:
        return record | {"outcome": "STALE_STATE", "from_status": current}
    updated = conn.execute(
        _CORRECT_STATUS,
        target
        | {
            "expected_current_status": parsed.expected_current_status,
            "requested_status": parsed.requested_status,
        },
    )
    if updated.rowcount != 1:
        # The row is locked, so this cannot happen short of a broken view; never commit it.
        raise _unresolved()
    return record | {
        "outcome": "APPLIED",
        "from_status": current,
        "to_status": parsed.requested_status,
    }


def check_health(tenant_id: UUID, source_id: UUID) -> dict[str, str]:
    """The manifest's `database_connection` health signal: the adapter's role can reach both
    merchant-side objects. Reads no rows and never echoes connection detail."""
    binding = _binding(tenant_id, source_id, READ_CAPABILITY)
    engine = read_only_engine(binding.secret)
    try:
        with engine.connect() as conn:
            conn.execute(_PROBE_PAYMENTS)
            conn.execute(_PROBE_RECORDS)
    except SQLAlchemyError:
        return {"signal": "database_connection", "status": "UNAVAILABLE"}
    finally:
        engine.dispose()
    return {"signal": "database_connection", "status": "OK"}
