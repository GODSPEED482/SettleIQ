# One-time bootstrap: remote-state bucket + GitHub OIDC + the role the infra pipeline uses.
# Run locally with your own AWS credentials: terraform init && terraform apply
# Its own state stays local (commit nothing from .terraform/ or *.tfstate).

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "github_repo" {
  type        = string
  description = "owner/repo of the SettleIQ repository"
}

variable "create_oidc_provider" {
  type        = bool
  default     = true
  description = "Set false if this AWS account already has the GitHub OIDC provider (e.g. from another project)"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "settleiq"
      ManagedBy = "terraform-bootstrap"
    }
  }
}

data "aws_caller_identity" "me" {}

# ---------- remote state ----------
resource "aws_s3_bucket" "tfstate" {
  bucket = "settleiq-tfstate-${data.aws_caller_identity.me.account_id}"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------- GitHub OIDC ----------
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  oidc_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# Infra role: only a workflow running on the prod branch can assume it. Jobs that use this role
# must not set `environment:`, because that changes the token subject and this trust would refuse it.
data "aws_iam_policy_document" "infra_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/prod"]
    }
  }
}

resource "aws_iam_role" "gha_infra" {
  name               = "settleiq-gha-infra"
  assume_role_policy = data.aws_iam_policy_document.infra_trust.json
}

# Hackathon trade-off: terraform creates IAM roles, so it needs broad rights.
# The trust policy above (this repo, prod branch only) is the real guard.
resource "aws_iam_role_policy_attachment" "gha_infra_admin" {
  role       = aws_iam_role.gha_infra.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "infra_role_arn" {
  value = aws_iam_role.gha_infra.arn
}
