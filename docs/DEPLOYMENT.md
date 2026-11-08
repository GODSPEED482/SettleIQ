# SettleIQ - Deployment plan

Status: the AWS resources, production values, GitHub variables and DNS are in place, and nothing has been deployed yet. See [section 10](#10-first-deploy-runbook) for what remains.

## 1. Summary

SettleIQ runs on one EC2 instance with Docker Compose, with Caddy terminating TLS on one origin. A push to `prod`, which includes a merged PR, tests the commit, builds images tagged with its SHA, pushes them to ECR, and runs `deploy.sh` on the host through SSM. There is no SSH and no AWS key in GitHub. Terraform owns the AWS resources, and Postgres lives on a separate EBS volume that survives instance replacement.

This plan follows the FastAPI code in the repo. The backend stack is still open in `CLAUDE.md`; if the team moves to Node, only the `api` image and the commands in `deploy/compose.yaml` change.

```mermaid
flowchart LR
  dev[Push or PR merge to prod] --> test[CI: tests and image builds]
  test --> build[Build, push to ECR by SHA]
  build --> bundle[Upload deploy/ to S3]
  bundle --> ssm[SSM runs deploy.sh]
  subgraph EC2[EC2 host, ports 80 and 443 only]
    caddy[Caddy] --> web[web]
    caddy --> api[api]
    api --> db[(Postgres on EBS)]
    worker[worker] --> db
    tools[agent-tools] --> db
    agent[agent] --> tools
  end
  ssm --> EC2
```

## 2. Service inventory

| Service | Path | Language | Port | Dockerfile | Health | DB | Env vars |
| --- | --- | --- | --- | --- | --- | --- | --- |
| proxy | `deploy/Caddyfile` | Caddy | 80, 443 (only published ports) | upstream image | none | no | `ACME_EMAIL` |
| web | `frontend` | static React build, nginx | 8080 | `Dockerfile.prod` (new) | `GET /` | no | none |
| api | `backend`, `app.main` | Python, FastAPI | 8000 | `Dockerfile` | `GET /healthz` | yes, `settleiq_app` | `DATABASE_URL`, `ENVIRONMENT` |
| worker | `backend`, `app.worker_api` | Python, FastAPI | 8001, private | same image as api | TCP | yes, `settleiq_app` | as api |
| migrate | `backend`, one-shot | Python, Alembic | none | same image as api | exit code | yes, owner | plus `MIGRATION_DATABASE_URL`, `APP_DB_PASSWORD` |
| agent-tools | `backend`, `app.agent_tools_api` | Python, FastAPI | 8002, private | same image as api | TCP | yes, `settleiq_app` | plus `AGENT_TOKEN_SECRET` |
| dispatcher | `backend`, `app.investigation.dispatcher` | Python | none | same image as api | none | yes, `settleiq_dispatcher` | `AGENT_TOKEN_SECRET`, `AGENT_URL` |
| agent | `agent` | Python, FastAPI | 8100, private | `Dockerfile` | TCP | **never** | `LLM_API_KEY`, `AGENT_TOOLS_URL` |
| db | upstream image | PostgreSQL 16 | 5432, private | upstream image | `pg_isready` | n/a | `POSTGRES_*` |

agent-tools, dispatcher and agent are stubs until task 5.7, so they sit behind the `agent` Compose profile, as they do locally.

Isolation matches the local stack: `edge` holds proxy, web and api; `backend` holds the database and its clients; `agent` holds the agent and its two peers. Secrets are passed per service rather than with one shared `env_file`, because spec section 8 forbids the agent from holding database credentials.

## 3. Environments

`prod` only. To add staging: copy `infra/envs/prod`, change the state key and `env`, add a parameter path under `/settleiq/staging/`, and add a second deploy role that trusts the staging branch.

## 4. Infrastructure

| Resource | Why | Approx. monthly cost |
| --- | --- | --- |
| EC2 `t3.small`, Ubuntu 24.04, standard CPU credits | One box to debug; free-tier eligible | $16.35 ($0.0224/hr) |
| EBS gp3: 20 GB root, 10 GB data | Data outlives the instance; `prevent_destroy` | $2.74 |
| Elastic IP / public IPv4 | Stable DNS target | $3.65 |
| ECR (3 repos, last 5 images) | Immutable SHA tags, so rollback is exact | under $0.50 |
| S3 (state, releases 30 days, backups 14 days) | Remote state, deploy bundles, dumps | under $0.50 |
| SSM Parameter Store, standard | Secrets | free |
| AWS Budgets alert at $25 a month | Warns before credits drain | free |

Total is about **$23 a month, or $0.76 a day**, at Mumbai list prices checked on 2026-11-09, before any tax.

Idle, the five core containers used about 170 MB in total in a local run, so `t3.small` (2 GB plus 2 GB swap) should fit. That is an idle figure, with no load and the agent profile off, so measure it in rehearsal.

State lives in S3 with native locking (`use_lockfile`), created by `infra/bootstrap`.

### Free-tier accounts

The stack is sized for AWS accounts created on or after 15 July 2025. These get $100 in credits, plus up to $100 more for completing activities, and EC2 usage draws down those credits. Accounts created before that date have passed their 12-month free tier and pay these prices directly.

**A Free plan ends after six months or when the credits run out, whichever comes first. The account then closes, and AWS deletes its data after 90 days unless it is upgraded to the Paid plan.** Upgrading cannot be undone. Download the database dumps before that date if the data matters.

| `instance_type` | RAM | Full stack per month | $100 lasts | Use |
| --- | --- | --- | --- | --- |
| `t3.micro` | 1 GB | about $15 | the full six months | Only to stretch credits; tight once the agent services run |
| `t3.small` | 2 GB | about $23 | about 4.3 months | Default |
| `m7i-flex.large` | 8 GB | about $80 | about 5 weeks | Only if rehearsal shows `t3.small` is too small; matches the tech-stack doc's suggested 8 GB |

`t3.medium` and `t3.large` are not on the eligible list. Changing `instance_type` is an in-place stop and start. It does not change the images, so it does not void a POL-14 attestation.

`t3.small` starts with no CPU credits (separate from billing credits) and earns 24 an hour. With `cpu_credits = "standard"` it slows to its baseline, 20% per vCPU, when they run out, instead of billing the excess. Check the balance before the first deploy and before the demo; section 10 has the command.

To pause spending after the demo, run `aws ec2 stop-instances --region ap-south-1 --instance-ids <instance_id>`. Disk and IP still cost about $6.40 a month, and deploys fail while the host is stopped.

## 5. Pipelines

| Workflow | Trigger | Jobs | Touches AWS |
| --- | --- | --- | --- |
| `ci.yml` | PRs, push to `prod`, called by `deploy.yml` | backend (real Postgres, `REQUIRE_DB_TESTS=1`), agent, frontend, image builds, Terraform validate, shellcheck | no |
| `deploy.yml` | **push to `prod` only**, ignoring `infra/`, `docs/`, `*.md` | test, build and push, deploy through SSM, public smoke test | yes |
| `infra.yml` | **push to `prod` only**, `infra/**` | terraform plan and apply | yes |

Any failing CI job blocks the deploy.

**CD runs from `prod` and nowhere else.** Three layers enforce this:

1. The workflows trigger only on `push: branches: [prod]`. A merged PR is a push. There is no `workflow_dispatch` or `pull_request` trigger on anything that deploys.
2. Every entry job has `if: github.ref == 'refs/heads/prod'`, in case a trigger is added later.
3. Both AWS roles trust exactly `repo:GODSPEED482@138929751/SettleIQ@1450123530:ref:refs/heads/prod`, the immutable subject format this repository's tokens use. A workflow edited on another branch cannot get AWS credentials at all. This is enforced by AWS, so it does not depend on any GitHub setting.

Two consequences:

- Jobs that assume these roles must not set `environment:`, because that changes the OIDC subject and AWS would refuse it.
- PRs get no Terraform plan, because a plan needs the admin-level infra role. PRs run keyless `fmt` and `validate`; `infra.yml` prints the plan in its log before applying. To preview a change, run `terraform plan` locally.

Protect `prod` in GitHub, requiring a PR and passing CI, since a direct push deploys.

`ci.yml` still runs on its own for a push to `prod`, so a deploying push runs the tests twice. That is deliberate: the standalone run also covers pushes that `deploy.yml` ignores.

Image tags are immutable, so "Re-run all jobs" on a finished deploy fails at the push step. Use "Re-run failed jobs".

## 6. Config and secrets

Production values live in `.env.production` at the repo root, which git ignores. Local development uses `.env`, and CI uses throwaway values in `ci.yml`. `.env.example` lists the names only.

`bash infra/push-prod-env.sh` copies `.env.production` to Parameter Store. It prints names only, and sends nothing if a required value is empty or if any value matches `.env`. Run it again after changing the file. `deploy.sh` then renders `.env` on the host from Parameter Store, and refuses to deploy if a required value is still `CHANGE_ME`.

Migration output stays in `migrate.log` in the release directory on the host, because a failed migration can print `APP_DB_PASSWORD` and this repository's Actions logs are public.

| Name | Secret | Set where | Used by |
| --- | --- | --- | --- |
| `ENVIRONMENT` | no | SSM | backend services |
| `POSTGRES_DB`, `POSTGRES_USER` | no | SSM | db, migrate, connection strings |
| `POSTGRES_PASSWORD` | yes | SSM | db, migrate |
| `APP_DB_PASSWORD` | yes | SSM | migrate (creates the role), api, worker, agent-tools |
| `ACME_EMAIL` | no | SSM | proxy |
| `DISPATCHER_DB_PASSWORD` | yes | SSM | dispatcher (agent profile) |
| `AGENT_TOKEN_SECRET` | yes | SSM | dispatcher, agent-tools (agent profile) |
| `LLM_API_KEY` | yes | SSM | agent only (agent profile) |
| `COMPOSE_PROFILES` | no | SSM, optional | set to `agent` after task 5.7 |
| `BUDGET_ALERT_EMAIL` | yes, because logs are public | GitHub secret | `infra.yml`, Terraform budget alert |

Rules for values:

- Use `openssl rand -hex 32` for passwords. They are embedded in connection URLs and read from a Compose `.env`, so characters such as `$`, `@` or `/` break them.
- Each value must fit on one line; base64-encode multi-line values.
- `APP_DB_PASSWORD` is applied once, when migration 0001 creates the role. Changing it later needs `ALTER ROLE` as well.

**Open for task 4.5 (secret references).** Containers cannot reach the instance role, because the IMDS hop limit is 1 by design. So the API cannot read or write Parameter Store at runtime with the current setup. Storing tenant PayPal credentials needs a decision: either a narrowly scoped credential for the API, or a different secret backend.

## 7. Database

- `deploy.sh` runs `alembic upgrade head && python -m app.authz.seed` as the owner role before traffic moves. The permission catalogue therefore syncs on every deploy. Running it twice was verified to be safe.
- **Expand and contract:** every migration must work with the previous release's code. Add now, remove in a later deploy, because rollback does not undo migrations.
- Nightly `pg_dump` to S3 at 21:30 UTC, kept 14 days.
- `pg_dump` does not include roles, so a restore must create `settleiq_app` first. This drill was run locally, and tables, forced RLS, triggers and the Alembic revision all matched:

```bash
# On the host, against an EMPTY database
aws s3 cp s3://$RELEASE_BUCKET/backups/<date>.sql.gz /tmp/restore.sql.gz
docker exec settleiq-db-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE ROLE settleiq_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '"'"'<APP_DB_PASSWORD>'"'"'"'
gunzip -c /tmp/restore.sql.gz | docker exec -i settleiq-db-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'
```

## 8. Rollback and failure

- **Automatic:** if pulling, migrating or the health wait fails, `deploy.sh` restarts the previous release. Images roll back; migrations do not.
- **Preferred manual route:** `git revert` the bad commit and merge to `prod`. It goes through the normal pipeline.
- **Fast manual route**, for when the pipeline is too slow. The last 5 releases stay on the host, and `--rollback` skips migrations:

```bash
aws ssm start-session --target <instance_id>
sudo bash /opt/settleiq/releases/<last-good-sha>/deploy.sh <last-good-sha> --rollback
```

There is deliberately no "Run workflow" rollback button, since `workflow_dispatch` can be started from any branch.

**POL-14 interaction.** An attestation binds to the exact build. After task 6.3 attests, any deploy or rollback that changes the images makes the attestation mismatch, and LIVE fails closed to SHADOW. Freeze `prod` before 6.3. Docs-only merges do not deploy.

A single host restarts containers in place, so expect a few seconds of downtime per deploy.

## 9. Observability

- Container logs use `json-file` with rotation (10 MB, 3 files). Read them with `docker compose logs` through `aws ssm start-session`.
- Add an external uptime check on `https://app.settleiq.dpdns.org/healthz`. It proves the API process is up, but does not ping the database.
- Sponsor tooling: the tech-stack doc puts Elastic Cloud in the traces and logs seat. It would add to `docker compose logs` once the app emits OpenTelemetry; it is not wired here.

## 10. First-deploy runbook

Do step 1 **before** merging this to `prod`. On Windows, use Git Bash and run `export MSYS_NO_PATHCONV=1` first, or `/settleiq/...` arguments get rewritten into Windows paths.

```bash
# 1. Check CPU credits, about an hour after first boot. Merge when the latest value is above about 20,
#    or the health wait in deploy.sh may time out on a throttled host.
aws cloudwatch get-metric-statistics --region ap-south-1 --namespace AWS/EC2 --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=<instance_id> --statistics Average --period 300 \
  --start-time "$(date -u -d '-30 minutes' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)"
#    Then merge to prod. deploy.yml runs; open https://app.settleiq.dpdns.org

# 2. Shell on the box when needed (no SSH)
aws ssm start-session --target <instance_id>
```

Once live, the PayPal webhook target is the public `/api/webhooks/...` route in `backend/app/api/webhooks.py`. Task 4.1's tunnel is then needed only for local development.

## 11. Demo-day checklist

- [ ] Merge freeze on `prod` before task 6.3; any later image change voids the attestation
- [ ] Last deploy at least an hour before the slot
- [ ] Certificate issued and `/healthz` green on the public URL
- [ ] CPU credit balance checked (command in section 10, step 1) and memory measured with the demo's profiles on
- [ ] Billing credits and Free plan end date checked in the Billing console
- [ ] Manual backup: `docker exec settleiq-db-1 sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > /data/pre-demo.sql.gz`
- [ ] Note the last-good SHA for `deploy.sh <sha> --rollback`
- [ ] Demo data seeded through a script, not by hand (spec G1: no seeded demo identities)
- [ ] If the agent is in the demo, `COMPOSE_PROFILES=agent` is set and deployed

## 12. Deliberately deferred

| Item | Why not now |
| --- | --- |
| Multi-AZ, autoscaling | One demo tenant load; adds cost and moving parts |
| RDS | Postgres on EBS with dumps is enough; spec section 6 and the plan stay PostgreSQL-first |
| Kubernetes, blue/green | Compose restart costs seconds; the merge freeze covers the demo |
| WAF, CDN | No production traffic |
| Secrets Manager | Parameter Store is free and meets the spec's secret-store rule for now; see task 4.5 above |
| Staging environment | 3-day slice; recipe in section 3 |
| Production SLOs, RTO/RPO | OPEN-15, required only before production |
