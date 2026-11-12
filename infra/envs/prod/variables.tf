variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "env" {
  type    = string
  default = "prod"
}

variable "az" {
  type        = string
  default     = "ap-south-1a"
  description = "Instance and data volume must share an AZ; pinning it keeps a replaced instance next to its volume"
}

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "t3.medium and larger T3 sizes are not free-tier eligible; docs/DEPLOYMENT.md section 4 lists the options"
}

variable "cpu_credits" {
  type        = string
  default     = "standard"
  description = "standard throttles at the CPU baseline; unlimited bills sustained use above it, which a runaway process would turn into spend"
}

variable "data_volume_gb" {
  type        = number
  default     = 10
  description = "With the 20 GB root volume this totals 30 GB. EBS volumes can grow in place but cannot shrink"
}

variable "monthly_budget_usd" {
  type        = number
  default     = 40
  description = "Alerts when actual or forecast monthly usage passes this; the expected cost is about $36 with the demo host"
}

variable "budget_alert_email" {
  type        = string
  sensitive   = true # infra.yml logs the plan, and this repository's logs are public
  description = "Receives the budget alerts"
  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+$", var.budget_alert_email))
    error_message = "Set budget_alert_email to an email address; infra.yml reads it from the BUDGET_ALERT_EMAIL secret."
  }
}

# A new image needs its ECR repo applied before the deploy that pushes it; deploy and infra run in parallel.
variable "services" {
  type        = list(string)
  default     = ["api", "web", "agent", "demo"] # one ECR repo per image the pipelines build
  description = "Image names; repos are created as settleiq-<name>"
}

variable "app_domain" {
  type    = string
  default = "app.settleiq.dpdns.org"
}

variable "demo_domain" {
  type    = string
  default = "subs.settleiq.dpdns.org"
}

variable "demo_instance_type" {
  type        = string
  default     = "t3.micro"
  description = "The demo business host: one small shop and its Postgres"
}

variable "route53_zone_id" {
  type        = string
  default     = ""
  description = "Leave empty if DNS is not on Route 53; add the A record at your DNS host instead"
}

variable "github_oidc_sub_prefix" {
  type        = string
  default     = "repo:GODSPEED482@138929751/SettleIQ@1450123530"
  description = "OIDC subject prefix from GET /repos/{owner}/{repo}/actions/oidc/customization/sub; this repository uses immutable owner and repo IDs"
}

variable "secret_names" {
  type = list(string)
  default = [
    "ENVIRONMENT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "ACME_EMAIL",
    "DISPATCHER_DB_PASSWORD",
    "AGENT_TOKEN_SECRET",
    "LLM_API_KEY",
    "LLM_MODEL",
    "APP_BASE_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
  ]
  description = "Runtime env var names. Terraform creates placeholders only; infra/push-prod-env.sh sets the real values from .env.production"
}
