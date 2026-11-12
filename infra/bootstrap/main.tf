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

variable "github_oidc_sub_prefix" {
  type        = string
  default     = "repo:GODSPEED482@138929751/SettleIQ@1450123530"
  description = "OIDC subject prefix from GET /repos/{owner}/{repo}/actions/oidc/customization/sub; this repository uses immutable owner and repo IDs"
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
      values   = ["${var.github_oidc_sub_prefix}:ref:refs/heads/prod"]
    }
  }
}

resource "aws_iam_role" "gha_infra" {
  name               = "settleiq-gha-infra"
  assume_role_policy = data.aws_iam_policy_document.infra_trust.json
}

# Only what terraform plan/apply of infra/envs/prod calls: CloudTrail for reads, the provider's create and
# replace paths for writes. IAM is limited to the stack's own named roles and profile, but the stack writes
# their inline policies and trust, so the trust policy above (this repo, prod branch only) still guards that.
# No ec2:DeleteVolume: the data volume is prevent_destroy. KMS comes from the aws/ssm and aws/ebs key policies.
locals {
  account_id = data.aws_caller_identity.me.account_id
  ec2        = "arn:aws:ec2:${var.region}:${local.account_id}"
  ssm_prod   = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/settleiq/prod/*"
  iam        = "arn:aws:iam::${local.account_id}"
  # The SettleIQ host and the demo business host (infra/envs/prod/demo.tf), each with its own role and profile.
  host_roles    = ["${local.iam}:role/settleiq-prod-host", "${local.iam}:role/settleiq-prod-demo-host"]
  host_profiles = ["${local.iam}:instance-profile/settleiq-prod-host", "${local.iam}:instance-profile/settleiq-prod-demo-host"]
  stack_roles   = concat(local.host_roles, ["${local.iam}:role/settleiq-prod-gha-deploy"])
  state_bucket  = aws_s3_bucket.tfstate.arn
  state_object  = "${aws_s3_bucket.tfstate.arn}/prod/terraform.tfstate"
  release_store = "arn:aws:s3:::settleiq-prod-releases-${local.account_id}"
}

data "aws_iam_policy_document" "gha_infra" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket]
  }
  statement {
    sid       = "StateObject"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [local.state_object]
  }
  statement {
    sid       = "StateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_object}.tflock"]
  }
  statement {
    sid = "ReleasesBucket"
    actions = [
      "s3:CreateBucket", "s3:DeleteBucket", "s3:ListBucket",
      "s3:GetAccelerateConfiguration", "s3:GetBucketAcl", "s3:GetBucketCORS", "s3:GetBucketLogging",
      "s3:GetBucketObjectLockConfiguration", "s3:GetBucketPolicy", "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketRequestPayment", "s3:GetBucketTagging", "s3:GetBucketVersioning", "s3:GetBucketWebsite",
      "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration",
      "s3:PutBucketPublicAccessBlock", "s3:PutBucketTagging", "s3:PutLifecycleConfiguration",
    ]
    resources = [local.release_store]
  }
  statement {
    sid       = "Ec2Read"
    actions   = ["ec2:Describe*"]
    resources = ["*"]
  }
  statement {
    sid     = "Ec2Launch"
    actions = ["ec2:RunInstances"]
    resources = [
      "arn:aws:ec2:${var.region}::image/*",
      "${local.ec2}:instance/*",
      "${local.ec2}:network-interface/*",
      "${local.ec2}:security-group/*",
      "${local.ec2}:subnet/*",
      "${local.ec2}:volume/*",
    ]
  }
  statement {
    sid = "Ec2Instance"
    actions = [
      "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceCreditSpecification", "ec2:ModifyInstanceMetadataOptions",
      "ec2:ModifyNetworkInterfaceAttribute", "ec2:StartInstances", "ec2:StopInstances", "ec2:TerminateInstances",
    ]
    resources = [
      "${local.ec2}:instance/*",
      "${local.ec2}:network-interface/*",
      "${local.ec2}:security-group/*",
      "${local.ec2}:volume/*",
    ]
  }
  statement {
    sid       = "Ec2Volume"
    actions   = ["ec2:AttachVolume", "ec2:CreateVolume", "ec2:DetachVolume", "ec2:ModifyVolume"]
    resources = ["${local.ec2}:instance/*", "${local.ec2}:volume/*"]
  }
  statement {
    sid = "Ec2SecurityGroup"
    actions = [
      "ec2:AuthorizeSecurityGroupEgress", "ec2:AuthorizeSecurityGroupIngress", "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup", "ec2:RevokeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress",
      "ec2:UpdateSecurityGroupRuleDescriptionsEgress", "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
    ]
    resources = ["${local.ec2}:security-group/*", "${local.ec2}:vpc/*"]
  }
  statement {
    sid       = "Ec2Address"
    actions   = ["ec2:AllocateAddress", "ec2:AssociateAddress", "ec2:DisassociateAddress", "ec2:ReleaseAddress"]
    resources = ["${local.ec2}:elastic-ip/*", "${local.ec2}:instance/*", "${local.ec2}:network-interface/*"]
  }
  statement {
    sid     = "Ec2Tags"
    actions = ["ec2:CreateTags", "ec2:DeleteTags"]
    resources = [
      "${local.ec2}:elastic-ip/*",
      "${local.ec2}:instance/*",
      "${local.ec2}:network-interface/*",
      "${local.ec2}:security-group/*",
      "${local.ec2}:volume/*",
    ]
  }
  statement {
    sid = "Ecr"
    actions = [
      "ecr:CreateRepository", "ecr:DeleteLifecyclePolicy", "ecr:DeleteRepository", "ecr:DescribeRepositories",
      "ecr:GetLifecyclePolicy", "ecr:ListTagsForResource", "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability", "ecr:PutLifecyclePolicy", "ecr:TagResource", "ecr:UntagResource",
    ]
    resources = ["arn:aws:ecr:${var.region}:${local.account_id}:repository/settleiq-*"]
  }
  statement {
    sid = "SsmProdParams"
    actions = [
      "ssm:AddTagsToResource", "ssm:DeleteParameter", "ssm:GetParameter", "ssm:ListTagsForResource",
      "ssm:PutParameter", "ssm:RemoveTagsFromResource",
    ]
    resources = [local.ssm_prod]
  }
  statement {
    sid       = "SsmAmiLookup"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}::parameter/aws/service/canonical/*"]
  }
  statement {
    sid       = "SsmDescribe"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }
  statement {
    sid = "IamStackRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:DeleteRolePolicy", "iam:GetRole", "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:ListRolePolicies", "iam:PutRolePolicy",
      "iam:TagRole", "iam:UntagRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole",
    ]
    resources = local.stack_roles
  }
  statement {
    sid       = "IamHostManagedPolicies"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = local.host_roles
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values = [
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
      ]
    }
  }
  statement {
    sid = "IamInstanceProfile"
    actions = [
      "iam:AddRoleToInstanceProfile", "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile", "iam:TagInstanceProfile", "iam:UntagInstanceProfile",
    ]
    resources = local.host_profiles
  }
  statement {
    sid       = "IamPassHostRole"
    actions   = ["iam:PassRole"]
    resources = local.host_roles
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }
  statement {
    sid       = "IamOidcRead"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = [local.oidc_arn]
  }
  statement {
    sid       = "IamOidcList"
    actions   = ["iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }
  statement {
    sid = "Budget"
    actions = [
      "budgets:ListTagsForResource", "budgets:ModifyBudget", "budgets:TagResource", "budgets:UntagResource",
      "budgets:ViewBudget",
    ]
    resources = ["arn:aws:budgets::${local.account_id}:budget/settleiq-prod-monthly"]
  }
  # Keeps this role off itself and the OIDC provider even if the allows above are widened later.
  statement {
    sid    = "DenySelfAndOidcChange"
    effect = "Deny"
    actions = [
      "iam:Add*", "iam:Attach*", "iam:Create*", "iam:Delete*", "iam:Detach*", "iam:PassRole", "iam:Put*",
      "iam:Remove*", "iam:Tag*", "iam:Untag*", "iam:Update*",
    ]
    resources = [aws_iam_role.gha_infra.arn, local.oidc_arn]
  }
}

resource "aws_iam_role_policy" "gha_infra" {
  name   = "settleiq-gha-infra-terraform"
  role   = aws_iam_role.gha_infra.id
  policy = data.aws_iam_policy_document.gha_infra.json
}

output "tfstate_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "infra_role_arn" {
  value = aws_iam_role.gha_infra.arn
}
