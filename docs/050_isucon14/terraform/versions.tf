terraform {
  # ルートの terraform/ と同じ下限。variable の validation から別の変数を
  # 参照するには 1.9 以降が必要。
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
      Project   = "isucon14"
      ManagedBy = "terraform"
    }
  }
}
