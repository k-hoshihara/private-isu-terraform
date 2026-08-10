terraform {
  # 1.10 以降。S3 backend の use_lockfile（ネイティブロック）に必要
  # variable の validation から別の変数を参照するには 1.9 以降が必要
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}


provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "private-isu"
      ManagedBy = "terraform"
    }
  }
}
