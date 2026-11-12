-- Merchant-side setup for the SettleIQ internal-merchant write adapter (spec 4.4.1, task 5.5).
--
-- The merchant's DBA runs this once, in the merchant database, connected as the owner of the
-- payments table, with search_path set to the schema that holds it. SettleIQ never names the
-- merchant's own tables or columns: it reads and corrects payments only through the view below,
-- so a merchant whose schema differs edits only the view's FROM clause and column aliases.
--
-- The adapter's role gets exactly what spec 4.4.1 and AC-MERCH-06 allow:
--   * SELECT on settleiq_payments, the readback (merchant.payment.read);
--   * UPDATE of the status column only, through the same view, the conditional correction
--     (merchant.payment_status.correct);
--   * SELECT and INSERT on settleiq_action_records, the durable idempotency record, written in
--     the same transaction as the correction so mutation and record can never diverge.
-- It has no privilege on the base table, no DELETE, no other column and no DDL.
--
-- Afterwards set the role's password (ALTER ROLE settleiq_merchant_writer PASSWORD '...') and its
-- search_path (ALTER ROLE settleiq_merchant_writer SET search_path = <this schema>), because the
-- adapter's statements name the view and the record table unqualified. Give SettleIQ the role's
-- connection URL only through the internal-merchant source's secret reference.
-- Register that source with config {"merchant_id": "<this merchant's identifier>"}: the adapter
-- touches only rows carrying that merchant_id.

CREATE VIEW settleiq_payments AS
    SELECT
        payment_id::text AS payment_id,
        merchant_id::text AS merchant_id,
        status,
        amount,
        currency,
        order_ref
    FROM payments;

CREATE TABLE settleiq_action_records (
    merchant_id text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
    payload_hash text NOT NULL,
    capability text NOT NULL,
    payment_id text NOT NULL,
    expected_status text NOT NULL,
    requested_status text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'STALE_STATE', 'TARGET_UNRESOLVED')),
    from_status text,
    to_status text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, idempotency_key)
);

CREATE ROLE settleiq_merchant_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO settleiq_merchant_writer', current_schema());
END
$$;

GRANT SELECT ON settleiq_payments TO settleiq_merchant_writer;
GRANT UPDATE (status) ON settleiq_payments TO settleiq_merchant_writer;
GRANT SELECT, INSERT ON settleiq_action_records TO settleiq_merchant_writer;
