terraform {
  required_version = ">= 1.10" # needed for S3-native state locking (use_lockfile)
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # TODO(G1 1.1 Rishav): replace ACCOUNT_ID with the bootstrap tfstate_bucket output.
  backend "s3" {
    bucket       = "settleiq-tfstate-ACCOUNT_ID" # from bootstrap output tfstate_bucket
    key          = "prod/terraform.tfstate"
    region       = "ap-south-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "settleiq"
      Environment = var.env
      ManagedBy   = "terraform"
    }
  }
}
