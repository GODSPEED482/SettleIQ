-- Kitebase's own database. SettleIQ reads it through a read-only role (merchant-postgres) and
-- corrects payment status only through merchant_setup.sql's view (internal-merchant). Every
-- statement is idempotent: the app applies this file on each start.

CREATE SEQUENCE IF NOT EXISTS demo_ids;

-- Demo controls, not business data. SettleIQ's roles get no privilege on these.
CREATE TABLE IF NOT EXISTS demo_meta (
    id integer PRIMARY KEY CHECK (id = 1),
    generation integer NOT NULL,
    fault_queue text[] NOT NULL DEFAULT '{}',
    last_reset_at timestamptz
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id text PRIMARY KEY,
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('SUBSCRIPTION', 'ADDON')),
    interval_months integer CHECK (interval_months > 0),
    price numeric(12, 2) NOT NULL CHECK (price > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$')
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id text PRIMARY KEY,
    email text NOT NULL UNIQUE,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    order_ref text PRIMARY KEY,
    account_id text NOT NULL REFERENCES accounts,
    plan_id text NOT NULL REFERENCES plans,
    total numeric(12, 2) NOT NULL,
    currency text NOT NULL,
    status text NOT NULL CHECK (status IN ('AWAITING_PAYMENT', 'PAID')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The shape merchant_setup.sql's view reads. Status words are SettleIQ's PENDING/COMPLETED.
CREATE TABLE IF NOT EXISTS payments (
    payment_id text PRIMARY KEY,
    merchant_id text NOT NULL,
    order_ref text NOT NULL REFERENCES orders,
    amount numeric(12, 2) NOT NULL,
    currency text NOT NULL,
    status text NOT NULL CHECK (status IN ('PENDING', 'COMPLETED')),
    paypal_order_id text,
    paypal_capture_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id text PRIMARY KEY,
    account_id text NOT NULL REFERENCES accounts,
    plan_id text NOT NULL REFERENCES plans,
    status text NOT NULL CHECK (status IN ('pending', 'active', 'past_due', 'canceled')),
    current_period_end timestamptz
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id text PRIMARY KEY,
    account_id text NOT NULL REFERENCES accounts,
    subscription_id text REFERENCES subscriptions,
    order_ref text REFERENCES orders,
    period_end timestamptz,
    status text NOT NULL CHECK (status IN ('open', 'paid', 'void')),
    amount numeric(12, 2) NOT NULL,
    currency text NOT NULL
);

-- Every PayPal order the checkout created, so the source view can read each one back.
CREATE TABLE IF NOT EXISTS checkout_attempts (
    paypal_order_id text PRIMARY KEY,
    order_ref text NOT NULL REFERENCES orders,
    capture_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
