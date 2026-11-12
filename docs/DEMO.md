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
   - **PayPal.** Register a `paypal` source and connect the Sandbox app with **Connect your PayPal app**. The card's **Webhook registration** then shows the source's webhook URL with a **Copy** button. In the PayPal developer dashboard, add a webhook to that URL for the `PAYMENT.CAPTURE.*` events, then enter the webhook ID PayPal gives it under **Webhook registration**. Backfill with **Sync** (Transaction Search).
   - **`merchant-postgres`.** URL `postgresql://settleiq_reader:<MERCHANT_READER_PASSWORD>@<private-ip>:5432/kitebase`. Store it in SSM as `/settleiq/prod/source/<tenant_id>/KITEBASE_READ_URL` and enter `managed:KITEBASE_READ_URL` under **Platform-managed reference** on the card. Run discovery: it sees `accounts`, `plans`, `orders`, `payments`, `subscriptions` and `invoices`.
   - **`internal-merchant`.** URL `postgresql://settleiq_merchant_writer:<MERCHANT_WRITER_PASSWORD>@<private-ip>:5432/kitebase`, stored as `/settleiq/prod/source/<tenant_id>/KITEBASE_WRITE_URL` and referenced as `managed:KITEBASE_WRITE_URL`, with config `{"merchant_id": "kitebase"}`. It has no discovery or sync: it only carries PB-001's status correction.
   - Redeploy SettleIQ after adding the SSM parameters: `deploy.sh` renders source secrets only at deploy time.
3. **Activate both packs** in Ontology Studio's **Domain packs** panel: `finops` (Financial Operations) and `saas` (SaaS Subscription Lifecycle). Do this before starting a draft: a draft composes the packs active when it starts, and generation proposes only terms the draft composes. Leave both execution-mode scopes on SHADOW.
4. **Ontology and mappings.** **Start a draft**, then **Generate proposals** from discovery.
   - Each binding proposal names its class and carries a complete mapping, so **Accept all ready bindings** adds every proposal whose terms the draft holds. A proposal that leans on a proposed `tenant:` term waits until that term is accepted. Check the result against the table below and retire anything that disagrees.
   - Under **Pack bindings**, **Apply finops bindings** to the PayPal source. This adds the pack's capture and refund bindings and makes PayPal the authority for capture status and amount.
   - **Pack bindings** lists what each active pack's run would miss with the draft's bindings: a class the pack does not resolve, a class nothing identifies, a property its shapes require, a link whose shared value is unbound. Publish when it lists nothing.
   - Every property the engines compare needs one authority assertion, or its value stays unresolved and the run skips it. Applying the finops pack bindings covers PayPal. For SaaS, make Kitebase database the authority for `saas:status` and `saas:currentPeriodEnd` on `saas:Subscription`, and `saas:status` and `saas:periodEnd` on `saas:Invoice`, with no end date.
   - Validate, then publish. A run reads only published bindings: a published version with no bindings yields no cases.

   The Kitebase bindings should be:

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

   The shop stores statuses in lower case (`active`, `paid`), so status bindings use `upper`, which generated proposals apply to every status. Then **Sync** each source and press **Run finops** and **Run saas** under Domain Operations.
5. **Judge accounts.** Invite the Domain Operator (who also holds an Approver grant, for the POL-01 denial) and the Approver. Put their sign-ins and the Sandbox buyer in the submission.
6. **Rehearse** the Judge guide end to end, then queue both faults again on **Demo controls** and return both scopes to SHADOW.

## 4. Running the demo again

There is no reset. Kitebase never deletes its rows: a payment row that disappeared would leave its PayPal captures, which cannot be undone, with nothing to reconcile against. To run again, queue both faults on **Demo controls** and check out twice; each checkout is a new order with its own discrepancy, and everything SettleIQ already reconciled stays consistent.
