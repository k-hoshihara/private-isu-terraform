# state は S3 に保存する。
# bucket 名はアカウント・接頭辞ごとに異なるため、backend.hcl で渡す。
# Terraform 変数（tfvars）では指定できない。backend は変数展開前に決まる。
#   cp backend.hcl.example backend.hcl
#   # bucket を編集したうえで:
#   terraform init -backend-config=backend.hcl
#
# バケットの作成と接頭辞は README「3. state 用 S3 バケット」を参照。
terraform {
  backend "s3" {
    key          = "private-isu/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
