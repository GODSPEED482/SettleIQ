# Demo business runbook (tasks 6.15 and 6.16)

Kitebase (`demo-business/`) is the fictional SaaS business the demo reconciles. It runs on its own instance at `https://subs.settleiq.dpdns.org`. SettleIQ reaches it only through its connectors: PayPal webhooks and reads, a read-only database role (`merchant-postgres`), and a role that can change payment status only (`internal-merchant`). Judges use the website's **Judge guide**, **Sources** and **Demo controls** pages, and one shared SettleIQ demo tenant.

## 1. Host (6.15)

1. **Before merging, or any time after:** create `.env.demo` at the repo root (git ignores it), then run `bash infra/push-demo-env.sh`. It creates the parameters under `/settleiq/demo`, prints names only, and sends nothing unless every check passes.

   | Name | Value |
   | --- | --- |
   | `POSTGRES_PASSWORD` | `openssl rand -hex 24`, the shop database's owner |
   | `MERCHANT_READER_PASSWORD` | `openssl rand -hex 24`, for SettleIQ's `merchant-postgres` source |
   | `MERCHANT_WRITER_PASSWORD` | `openssl rand -hex 24`, for SettleIQ's `internal-merchant` source |
   | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | The Sandbox app the demo tenant connects in SettleIQ. Captures and refunds must belong to the same Sandbox business account. |
   | `ACME_EMAIL` | Let's Encrypt contact |
   | `MERCHANT_ID` | optional, default `kitebase` |

2. **Merge to `prod`.** `infra.yml` creates the instance, its Elastic IP, the `settleiq-demo` ECR repository and the deploy permissions. `demo-deploy.yml` waits up to 15 minutes for the repository and for the host to come online in SSM, then deploys.
3. **Read the addresses** (read-only):

   ```
   aws ec2 describe-instances --region ap-south-1 \
     --filters Name=tag:Name,Values=settleiq-prod-demo Name=instance-state-name,Values=running \
     --query 'Reservations[].Instances[].[PublicIpAddress,PrivateIpAddress]' --output text
   ```

   Add an `A` record for `subs.settleiq.dpdns.org` pointing at the public IP. Caddy fetches the certificate once DNS resolves. The public check in `demo-deploy.yml` only warns until then.
4. If the first deploy ran before step 1, re-run its failed jobs.
5. **Check from the SettleIQ host** (SSM shell) that only SettleIQ's roles reach the database:
   - `psql "postgresql://settleiq_reader:<password>@<private-ip>:5432/kitebase" -c 'select count(*) from payments'` succeeds.
   - The same with the `kitebase` owner is refused by `pg_hba.conf`.

## 2. PayPal Sandbox account

- In the Sandbox business account's payment receiving preferences, allow duplicate invoice IDs. Otherwise PayPal blocks the double-charge retry, and the shop says so (HTTP 409).
- Have a Sandbox personal (buyer) account ready for judges. Its sign-in goes in the submission, not on the website.

## 3. Demo tenant (6.16)

One shared tenant, created by real registration at `https://app.settleiq.dpdns.org`.

1. Register the Tenant Owner, verify the email and create the tenant.
2. **Sources**, as Integration Admin:
   - **PayPal.** Connect the Sandbox app with **Connect your PayPal app**, then register its webhook (`PUT /webhooks` through the Sources card). Backfill with Transaction Search.
   - **`merchant-postgres`.** URL `postgresql://settleiq_reader:<MERCHANT_READER_PASSWORD>@<private-ip>:5432/kitebase`. Store it in SSM as `/settleiq/prod/source/<tenant_id>/KITEBASE_READ_URL` and reference it as `managed:KITEBASE_READ_URL`. Run discovery: it sees `accounts`, `plans`, `orders`, `payments`, `subscriptions` and `invoices`.
   - **`internal-merchant`.** URL `postgresql://settleiq_merchant_writer:<MERCHANT_WRITER_PASSWORD>@<private-ip>:5432/kitebase`, stored as `/settleiq/prod/source/<tenant_id>/KITEBASE_WRITE_URL`, with config `{"merchant_id": "kitebase"}`.
   - Redeploy SettleIQ after adding the SSM parameters: `deploy.sh` renders source secrets only at deploy time.
3. **Ontology and mappings.** Generate the draft from discovery, then bind:

   | Kitebase column | Class and property | Transform |
   | --- | --- | --- |
   | `payments.paypal_capture_id` | `finops:Capture` `finops:providerReference` | identity |
   | `payments.order_ref` | `finops:Capture` `finops:orderReference` | identity |
   | `payments.status` | `finops:Capture` `finops:lifecycleStatus` | upper |
   | `payments.amount`, `currency` | `finops:amount`, `finops:currency` | decimal_string, upper |
   | `orders.order_ref` | `finops:Order` `finops:orderReference` | identity |
   | `subscriptions.subscription_id` | `saas:Subscription` `saas:subscriptionReference` | identity |
   | `subscriptions.status` | `saas:status` | upper |
   | `subscriptions.current_period_end` | `saas:currentPeriodEnd` | iso8601 |
   | `invoices.invoice_id`, `subscription_id` | `saas:Invoice` `saas:invoiceReference`, `saas:subscriptionReference` | identity |
   | `invoices.status`, `period_end` | `saas:status`, `saas:periodEnd` | upper, iso8601 |

   The shop stores statuses in lower case (`active`, `paid`), so the SaaS status bindings must use `upper`. Make the Kitebase database the authority for the SaaS properties. Publish.
4. **Activate both packs**, Financial Operations and SaaS Subscription Lifecycle. Leave both execution-mode scopes on SHADOW.
5. **Judge accounts.** Invite the Domain Operator (who also holds an Approver grant, for the POL-01 denial) and the Approver. Put their sign-ins and the Sandbox buyer in the submission.
6. **Rehearse** the Judge guide end to end, then press **Reset discrepancies** and return both scopes to SHADOW.

## 4. What reset can and cannot undo

Reset rebuilds every Kitebase row under a new identifier generation and queues both checkout faults again. SettleIQ keeps what it already read, so earlier cases, executions and audit records remain, and nothing is reused. PayPal captures and refunds cannot be undone. The PayPal-side discrepancies return through new checkouts: two Sandbox checkouts, one per fault. Reset does not replay checkouts for you.
