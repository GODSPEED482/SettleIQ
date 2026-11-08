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
  type    = string
  default = "t3.medium"
}

variable "data_volume_gb" {
  type    = number
  default = 20
}

variable "services" {
  type        = list(string)
  default     = ["api", "web", "agent"] # one ECR repo per image the pipeline builds
  description = "Image names; repos are created as settleiq-<name>"
}

variable "app_domain" {
  type    = string
  default = "settleiq.swapnaneelray.me"
}

variable "route53_zone_id" {
  type        = string
  default     = ""
  description = "Leave empty if DNS is not on Route 53; add the A record at your DNS host instead"
}

variable "github_repo" {
  type        = string
  description = "owner/repo"
}

# TODO(G1 1.9 Swapnaneel): add the SMTP relay and auth secret names.
variable "secret_names" {
  type = list(string)
  default = [
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "ACME_EMAIL",
    "DISPATCHER_DB_PASSWORD",
    "AGENT_TOKEN_SECRET",
    "LLM_API_KEY",
  ]
  description = "Runtime env var names. Terraform creates placeholders only; real values are set with `aws ssm put-parameter --overwrite`"
}
