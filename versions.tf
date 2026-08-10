terraform {
  # 1.9 以降。variable の validation から別の変数を参照するために必要
  required_version = ">= 1.9"

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
